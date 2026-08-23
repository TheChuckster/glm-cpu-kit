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
# K3 is slower than DS4, but it is the quality-first local choice. The current
# `kimi-k3-q5attn` deployment measured 42.715 tok/s on a fresh 897-token prompt
# and 4.491 tok/s generation (three forced 128-token samples, 2026-08-23).
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
#   ssh $GLM_SERVER_HOST 'sudo glm-model use kimi-k3-q5attn'
#   ssh $GLM_SERVER_HOST 'glm-model status'             # confirm before trusting it
# (GLM_SERVER_HOST defaults to chuckdancer)
set -euo pipefail

BASE="${OPENCODE_BASE_URL:-http://127.0.0.1:4000/v1}"
MODEL="${KIMI_OPENCODE_MODEL:-local/kimi-k3}"
KIMI_VARIANT="${KIMI_VARIANT:-kimi-k3-q5attn}"
CFG_HOME="${GLM_OPENCODE_XDG:-$HOME/.glm-opencode-config}"

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
# Warn rather than fail: the check needs SSH, which may not be available.
# Hostname of the box running glm-server. Was hardcoded to `chuckdancer`, which
# is fine on the box this was written on and silently skips the check everywhere
# else - the `|| true` means a failed ssh looks exactly like "nothing to warn
# about". Override for any other host.
GLM_HOST="${GLM_SERVER_HOST:-chuckdancer}"

STATUS=$(ssh -o BatchMode=yes -o ConnectTimeout=4 "$GLM_HOST" \
           'glm-model status 2>/dev/null' 2>/dev/null || true)
SELECTED=$(printf '%s\n' "$STATUS" | sed -n 's/^selected variant *: \([^ ]*\).*/\1/p')
SERVED=$(printf '%s\n' "$STATUS" | sed -n 's/^serving alias *: //p')
# Several K3 variants deliberately share alias `kimi-k3`, so the alias alone
# cannot distinguish the Q5-attention build from the lower-quality base quant.
# Check the selected registry variant as well as the served API alias.
if [ -n "$SELECTED" ] && [ "$SELECTED" != "$KIMI_VARIANT" ]; then
  echo "WARNING: $GLM_HOST selected '$SELECTED', not '$KIMI_VARIANT'." >&2
  echo "         Switch with: ssh $GLM_HOST 'sudo glm-model use $KIMI_VARIANT'" >&2
  echo >&2
elif [ -n "$SERVED" ] && [ "$SERVED" != "$MODEL_ID" ]; then
  echo "WARNING: $GLM_HOST is serving alias '$SERVED', not '$MODEL_ID'." >&2
  echo "         Your requests will be answered by that model instead." >&2
  echo "         Switch with: ssh $GLM_HOST 'sudo glm-model use $KIMI_VARIANT'" >&2
  echo >&2
fi

# Say this out loud every time. A multi-minute silence before the first token is
# indistinguishable from a hang, and treating it as one is the single most
# likely way to conclude - wrongly - that this script is broken.
echo "kimi-k3: ~42.7 tok/s prompt processing, ~4.49 tok/s generation. A fresh" >&2
echo "         session sends 7K+ tokens of system prompt and tools, so the" >&2
echo "         FIRST reply usually takes ~3-4 minutes." >&2
echo "         Quiet prompt evaluation is normal. Watch it:  ssh $GLM_HOST 'sudo journalctl -fu glm-server'" >&2
echo "         Generation running to 8K without a reply means the engine is older than d39033a5." >&2
echo >&2

exec env \
  XDG_CONFIG_HOME="$CFG_HOME" \
  LITELLM_API_KEY="${LITELLM_API_KEY:-sk-litellm-not-needed}" \
  DO_NOT_TRACK=1 \
  DISABLE_TELEMETRY=1 \
  "$OPENCODE" --model "$MODEL" "$@"
