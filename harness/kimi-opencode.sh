#!/usr/bin/env bash
# Launch OpenCode against Kimi K3 Q5-attention on $GLM_SERVER_HOST, via the
# local litellm proxy. Same isolated-config trick as glm-opencode.sh: a clean
# XDG_CONFIG_HOME so the global oh-my-openagent plugin is NOT loaded, which keeps
# the system prompt small enough that first-token latency is bearable on CPU.
#
#   ./kimi-opencode.sh                  # interactive TUI (new session)
#   ./kimi-opencode.sh --continue       # resume the last session in this dir
#   ./kimi-opencode.sh run "message"    # headless one-shot
#
# THIS WORKS. It did not for most of its life, and the header used to blame the
# 2.479 bpw quant. The actual fault was missing per-step recurrent checkpoints
# in the fork's KDA builder. The fixed engine passes tool calls 5/5 and completes
# a real OpenCode loop. Measured on "read util.py and say what it does":
#
#   DeepSeek-V4  41s,  invoked the tool, correct answer
#   GLM-5.2      67s,  chained Glob then Read, correct answer
#   Kimi K3      234s, chained Glob then Read, correct answer
#
# K3 is slower than DS4, but it is the quality-first local choice. Active V26
# measured 43.018 tok/s on the fixed 897-token prompt (two-run mean) and 4.398
# tok/s generation across six forced 128-token samples; the repeat run averaged
# 4.494 tok/s (2026-08-26), effectively unchanged from the prior deployment.
# OpenCode's system prompt plus tool definitions is large, so
#   EXPECT ROUGHLY
#   THREE TO FOUR MINUTES FOR THE FIRST REPLY of a fresh session.
#   A 2026-08-23 headless canary evaluated 7,313 prompt tokens and returned its
#   37-token greeting in 183 seconds; prompt shape and cache state vary.
#   Silence while the server is evaluating that prompt is normal, and an HTTP
#   client with a default timeout will give up before the model answers. Do not
#   confuse it with the older termination bug: before engine `d39033a5`, K3
#   could finish a response and then repeat its message trailer to the 8K output
#   cap. If the server log shows generation climbing after a completed reply,
#   update the `kimi-k3` engine branch rather than waiting it out.
#   Output itself is clean: the chat parser for K3's <|open|>/<|sep|>/<|close|>
#   template landed, reasoning goes to reasoning_content, and no structural
#   markers reach content. (Earlier versions of this script warned that they
#   did. They no longer do.)
#
#   BUDGET TOKENS GENEROUSLY. K3 always reasons and is not brief about it -
#   1800+ characters of reasoning for "write a one-liner and name a capital".
#   Reasoning is spent BEFORE the response section opens, so a tight max_tokens
#   returns EMPTY content with a full reasoning_content and finish_reason
#   "length". That reads like a broken parser and is not one. opencode.json's
#   output limit of 8000 is fine; hand-rolled curl calls are where this bites.
#
# K3 must be the resident model - only one is, and they are 155-800 GB mlocked:
#   ssh $GLM_SERVER_HOST 'sudo glm-model use kimi-k3-q5attn-abl-v26'
#   ssh $GLM_SERVER_HOST 'glm-model status'             # confirm before trusting it
# (GLM_SERVER_HOST defaults to chuckdancer)
set -euo pipefail

BASE="${OPENCODE_BASE_URL:-http://127.0.0.1:4000/v1}"
MODEL="${KIMI_OPENCODE_MODEL:-local/kimi-k3}"
KIMI_VARIANT="${KIMI_VARIANT:-kimi-k3-q5attn-abl-v26}"
CFG_HOME="${GLM_OPENCODE_XDG:-$HOME/.glm-opencode-config}"
KIMI_READY_TIMEOUT="${KIMI_READY_TIMEOUT:-1800}"
KIMI_READY_POLL="${KIMI_READY_POLL:-5}"

OPENCODE="${OPENCODE_BIN:-/usr/bin/opencode}"
[ -x "$OPENCODE" ] || { echo "opencode CLI not found at $OPENCODE" >&2; exit 1; }

MODEL_ID="${MODEL#*/}"
if ! curl -s -m 2 "$BASE/models" -H "Authorization: Bearer sk-litellm-not-needed" \
     | grep -q "$MODEL_ID"; then
  echo "litellm proxy unreachable or model '$MODEL_ID' not registered at $BASE" >&2
  echo "Start it with: ~/Projects_new/ai/proxy.sh" >&2
  exit 1
fi

# llama-server serves whatever is loaded regardless of the name asked for, so a
# request for kimi-k3 while DeepSeek-V4 is resident silently answers as DS4.
# Fail closed unless explicitly skipped: this server accepts a wrong model name
# and answers with whatever is resident, so an unverified launch is not safe.
# Hostname of the box running glm-server. Override for any other host. This
# installation's user SSH config is malformed, so `none` deliberately uses the
# system host/key configuration; set GLM_SSH_CONFIG to a real config path when
# aliases are required.
GLM_HOST="${GLM_SERVER_HOST:-chuckdancer}"
GLM_SSH_CONFIG="${GLM_SSH_CONFIG:-none}"
KIMI_SKIP_VARIANT_CHECK="${KIMI_SKIP_VARIANT_CHECK:-0}"
[[ "$KIMI_SKIP_VARIANT_CHECK" == 0 || "$KIMI_SKIP_VARIANT_CHECK" == 1 ]] || {
  echo "KIMI_SKIP_VARIANT_CHECK must be 0 or 1" >&2
  exit 1
}
[[ "$KIMI_READY_TIMEOUT" =~ ^[0-9]+$ ]] || {
  echo "KIMI_READY_TIMEOUT must be a non-negative integer" >&2
  exit 1
}
[[ "$KIMI_READY_POLL" =~ ^[1-9][0-9]*$ ]] || {
  echo "KIMI_READY_POLL must be a positive integer" >&2
  exit 1
}

if [ "$KIMI_SKIP_VARIANT_CHECK" = 0 ]; then
  READY_STARTED=$SECONDS
  READY_NOTICE=0
  while :; do
    if ! STATUS=$(ssh -F "$GLM_SSH_CONFIG" -o BatchMode=yes -o ConnectTimeout=4 "$GLM_HOST" \
        'glm-model status 2>/dev/null' 2>/dev/null); then
      echo "ERROR: cannot verify the resident model on $GLM_HOST; refusing an ambiguous launch." >&2
      echo "       Fix SSH, or explicitly set KIMI_SKIP_VARIANT_CHECK=1." >&2
      exit 1
    fi
    SELECTED=$(printf '%s\n' "$STATUS" | sed -n 's/^selected variant *: \([^ ]*\).*/\1/p')
    SERVICE_STATE=$(printf '%s\n' "$STATUS" | sed -n 's/^service *: //p')
    HEALTH=$(printf '%s\n' "$STATUS" | sed -n 's/^health *: //p')
    SERVED=$(printf '%s\n' "$STATUS" | sed -n 's/^serving alias *: //p')

    if [ -z "$SELECTED" ]; then
      echo "ERROR: could not parse the selected variant from glm-model status on $GLM_HOST." >&2
      printf '%s\n' "$STATUS" | sed 's/^/       /' >&2
      exit 1
    fi
    # Several K3 variants deliberately share alias `kimi-k3`, so the alias alone
    # cannot distinguish the Q5-attention build from the lower-quality base quant.
    if [ "$SELECTED" != "$KIMI_VARIANT" ]; then
      echo "ERROR: $GLM_HOST selected '$SELECTED', not '$KIMI_VARIANT'." >&2
      echo "       Switch with: ssh -F $GLM_SSH_CONFIG $GLM_HOST 'sudo glm-model use $KIMI_VARIANT'" >&2
      exit 1
    fi
    if [ -n "$SERVED" ]; then
      if [ "$SERVED" != "$MODEL_ID" ]; then
        echo "ERROR: $GLM_HOST is serving alias '$SERVED', not '$MODEL_ID'." >&2
        echo "       Requests would be answered by the wrong model; refusing to launch." >&2
        exit 1
      fi
      break
    fi

    # While a large model is loading, glm-model intentionally prints the
    # selected variant but cannot print a serving alias until /health and
    # /v1/models respond. That is a readiness state, not malformed output.
    if [ "$SERVICE_STATE" != active ] && [ "$SERVICE_STATE" != activating ]; then
      echo "ERROR: $GLM_HOST selected '$KIMI_VARIANT', but glm-server is '$SERVICE_STATE'." >&2
      printf '%s\n' "$STATUS" | sed 's/^/       /' >&2
      exit 1
    fi
    if [ -n "$HEALTH" ] && [[ "$HEALTH" != "not responding"* ]]; then
      echo "ERROR: $GLM_HOST is healthy but its serving alias could not be verified." >&2
      printf '%s\n' "$STATUS" | sed 's/^/       /' >&2
      exit 1
    fi

    READY_ELAPSED=$((SECONDS - READY_STARTED))
    if (( READY_ELAPSED >= KIMI_READY_TIMEOUT )); then
      echo "ERROR: timed out after ${KIMI_READY_TIMEOUT}s waiting for $KIMI_VARIANT on $GLM_HOST." >&2
      printf '%s\n' "$STATUS" | sed 's/^/       /' >&2
      exit 1
    fi
    if [ "$READY_NOTICE" = 0 ]; then
      echo "$KIMI_VARIANT is selected on $GLM_HOST and is still loading; waiting up to ${KIMI_READY_TIMEOUT}s." >&2
      echo "Watch it: ssh -F $GLM_SSH_CONFIG $GLM_HOST 'sudo journalctl -fu glm-server'" >&2
      READY_NOTICE=1
    fi
    READY_REMAINING=$((KIMI_READY_TIMEOUT - READY_ELAPSED))
    if (( READY_REMAINING < KIMI_READY_POLL )); then
      sleep "$READY_REMAINING"
    else
      sleep "$KIMI_READY_POLL"
    fi
  done
fi

# Say this out loud every time. A multi-minute silence before the first token is
# indistinguishable from a hang, and treating it as one is the single most
# likely way to conclude - wrongly - that this script is broken.
echo "kimi-k3 V26: ~43.0 tok/s prompt processing, ~4.4 tok/s generation. A fresh" >&2
echo "         session sends 7K+ tokens of system prompt and tools, so the" >&2
echo "         FIRST reply usually takes ~3-4 minutes." >&2
echo "         Quiet prompt evaluation is normal. Watch it:  ssh -F $GLM_SSH_CONFIG $GLM_HOST 'sudo journalctl -fu glm-server'" >&2
echo "         Generation running to 8K without a reply means the engine is older than d39033a5." >&2
echo >&2

exec env \
  XDG_CONFIG_HOME="$CFG_HOME" \
  LITELLM_API_KEY="${LITELLM_API_KEY:-sk-litellm-not-needed}" \
  DO_NOT_TRACK=1 \
  DISABLE_TELEMETRY=1 \
  "$OPENCODE" --model "$MODEL" "$@"
