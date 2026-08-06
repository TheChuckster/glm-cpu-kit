#!/usr/bin/env bash
# Launch OpenCode against Kimi K3 (UD-Q2_K_XL, 803 GB) on chuckdancer, via the
# local litellm proxy. Same isolated-config trick as glm-opencode.sh: a clean
# XDG_CONFIG_HOME so the global oh-my-openagent plugin is NOT loaded, which keeps
# the system prompt small enough that first-token latency is bearable on CPU.
#
#   ./kimi-opencode.sh                  # interactive TUI (new session)
#   ./kimi-opencode.sh --continue       # resume the last session in this dir
#   ./kimi-opencode.sh run "message"    # headless one-shot
#
# READ THIS FIRST - K3 is the slowest model on the box, and the number that
# hurts is not the one you would expect:
#
#   ~39 tok/s prompt processing, ~3.7 tok/s generation. opencode's system
#   prompt plus tool definitions runs well over 10K tokens, so EXPECT ROUGHLY
#   FIVE MINUTES BEFORE THE FIRST TOKEN of a fresh session, every session.
#   Nothing is hung. It looks exactly like a hang, and an HTTP client with a
#   default timeout will give up before the model answers - which is what
#   "kimi-opencode doesn't work" turned out to be.
#
#   DeepSeek-V4 does ~377 PP / ~23 TG. If you want to get work done rather than
#   exercise K3 specifically, use ./glm-opencode.sh with deepseek-v4-flash-0731.
#
#   Output itself is clean: the chat parser for K3's <|open|>/<|sep|>/<|close|>
#   template landed, reasoning goes to reasoning_content, and no structural
#   markers reach content. (Earlier versions of this script warned that they
#   did. They no longer do.)
#
# K3 must be the resident model - only one is, and they are 155-860 GB mlocked:
#   ssh chuckdancer 'sudo glm-model use kimi-k3'   # ~30s once page-cached
#   ssh chuckdancer 'glm-model status'             # confirm before trusting it
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
SERVED=$(ssh -o BatchMode=yes -o ConnectTimeout=4 chuckdancer \
           'glm-model status 2>/dev/null | sed -n "s/^serving alias *: //p"' 2>/dev/null || true)
if [ -n "$SERVED" ] && [ "$SERVED" != "kimi-k3" ]; then
  echo "WARNING: chuckdancer is serving '$SERVED', not kimi-k3." >&2
  echo "         Your requests will be answered by that model instead." >&2
  echo "         Switch with: ssh chuckdancer 'sudo glm-model use kimi-k3'" >&2
  echo >&2
fi

# Say this out loud every time. A five-minute silence before the first token is
# indistinguishable from a hang, and treating it as one is the single most
# likely way to conclude - wrongly - that this script is broken.
echo "kimi-k3: ~39 tok/s prompt processing. A fresh session sends 10K+ tokens of" >&2
echo "         system prompt and tools, so the FIRST reply takes ~5 minutes." >&2
echo "         It is not hung. Watch it work:  ssh chuckdancer 'sudo journalctl -fu glm-server'" >&2
echo >&2

exec env \
  XDG_CONFIG_HOME="$CFG_HOME" \
  LITELLM_API_KEY="${LITELLM_API_KEY:-sk-litellm-not-needed}" \
  DO_NOT_TRACK=1 \
  DISABLE_TELEMETRY=1 \
  "$OPENCODE" --model "$MODEL" "$@"
