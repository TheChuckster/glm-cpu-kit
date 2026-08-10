#!/usr/bin/env bash
# Launch OpenCode against Kimi K3 (UD-Q2_K_XL) on $GLM_SERVER_HOST, via the
# local litellm proxy. Same isolated-config trick as glm-opencode.sh: a clean
# XDG_CONFIG_HOME so the global oh-my-openagent plugin is NOT loaded, which keeps
# the system prompt small enough that first-token latency is bearable on CPU.
#
#   ./kimi-opencode.sh                  # interactive TUI (new session)
#   ./kimi-opencode.sh --continue       # resume the last session in this dir
#   ./kimi-opencode.sh run "message"    # headless one-shot
#
# THIS WORKS. It did not for most of its life, and the header used to say so at
# length - "does not produce usable agent sessions", blaming the 2.479 bpw quant.
# That was wrong. Measured on a trivial "read util.py and say what it does" task:
#
#   DeepSeek-V4  41s,  invoked the tool, correct answer
#   GLM-5.2      67s,  chained Glob then Read, correct answer
#   Kimi K3      234s, chained Glob then Read, correct answer
#
# K3 is the slowest of the three by a wide margin and that part has not changed.
# What changed is that it now finishes: the fork's KDA path never passed
# build_qkv's per-step checkpoint tensors, so speculative decoding had nothing to
# roll back to on a rejected draft and the recurrent state drifted into
# repetition loops - 8000 tokens about home medical visits instead of reading a
# file. Wiring them took tool calls from 0/5 to 5/5.
#
# Use DeepSeek-V4 when you want the work done quickly:
#
#     ./ds4-opencode.sh
#
# Use K3 when you specifically want K3. Both are real options now.
#
# The rest of this header is the speed detail:
#
#   ~40 tok/s prompt processing, ~4.3 tok/s generation. opencode's system
#   prompt plus tool definitions runs well over 10K tokens, so EXPECT ROUGHLY
#   FOUR MINUTES BEFORE THE FIRST TOKEN of a fresh session, every session.
#   (Measured end to end: 225s for a one-word reply on a cold session.)
#   Nothing is hung. It looks exactly like a hang, and an HTTP client with a
#   default timeout will give up before the model answers - which is what
#   "kimi-opencode doesn't work" turned out to be.
#
#   USE DEEPSEEK-V4 INSTEAD FOR ACTUAL AGENT WORK. Not a preference - measured:
#   the same "read this file and say what it does" task takes DeepSeek-V4 41
#   seconds and it invokes the tool correctly; on K3 it ran 838 seconds and
#   printed nothing. K3 emits a valid tool call only about half the time (the
#   model degenerates mid-reasoning at 2.479 bpw), where DeepSeek-V4 and GLM are
#   5/5. Run:
#       ./ds4-opencode.sh
#
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
# K3 must be the resident model - only one is, and they are 155-860 GB mlocked:
#   ssh $GLM_SERVER_HOST 'sudo glm-model use kimi-k3'   # ~30s once page-cached
#   ssh $GLM_SERVER_HOST 'glm-model status'             # confirm before trusting it
# (GLM_SERVER_HOST defaults to chuckdancer)
set -euo pipefail

BASE="${OPENCODE_BASE_URL:-http://127.0.0.1:4000/v1}"
MODEL="${KIMI_OPENCODE_MODEL:-local/kimi-k3}"
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

SERVED=$(ssh -o BatchMode=yes -o ConnectTimeout=4 "$GLM_HOST" \
           'glm-model status 2>/dev/null | sed -n "s/^serving alias *: //p"' 2>/dev/null || true)
# Compare against the SELECTED model, not the literal "kimi-k3". With the
# literal, KIMI_OPENCODE_MODEL=local/kimi-k3-abl against a resident plain kimi-k3
# printed no warning at all and was silently answered by the base model - the
# exact failure this block exists to catch - while pointing the launcher at any
# other model produced a false warning telling you to unload the one you asked
# for. ds4-opencode.sh already did this correctly.
if [ -n "$SERVED" ] && [ "$SERVED" != "$MODEL_ID" ]; then
  echo "WARNING: $GLM_HOST is serving '$SERVED', not $MODEL_ID." >&2
  echo "         Your requests will be answered by that model instead." >&2
  echo "         Switch with: ssh $GLM_HOST 'sudo glm-model use <variant serving $MODEL_ID>'" >&2
  echo "         (glm-model list shows which variant maps to which alias)" >&2
  echo >&2
fi

# Say this out loud every time. A five-minute silence before the first token is
# indistinguishable from a hang, and treating it as one is the single most
# likely way to conclude - wrongly - that this script is broken.
echo "kimi-k3: ~40 tok/s prompt processing. A fresh session sends 10K+ tokens of" >&2
echo "         system prompt and tools, so the FIRST reply takes ~4 minutes." >&2
echo "         It is not hung. Watch it work:  ssh $GLM_HOST 'sudo journalctl -fu glm-server'" >&2
echo >&2

exec env \
  XDG_CONFIG_HOME="$CFG_HOME" \
  LITELLM_API_KEY="${LITELLM_API_KEY:-sk-litellm-not-needed}" \
  DO_NOT_TRACK=1 \
  DISABLE_TELEMETRY=1 \
  "$OPENCODE" --model "$MODEL" "$@"
