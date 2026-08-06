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
# READ THIS FIRST - K3 is the slowest and roughest model on the box:
#
#   ~3.6 tok/s. K3's per-channel KDA gate cannot use ik's fused AVX-512
#   delta-net kernel, so 69 of its 93 layers run the scalar path. DeepSeek-V4
#   does ~23 tok/s; if you want to get work done rather than exercise K3
#   specifically, use ./glm-opencode.sh with deepseek-v4-flash-0731.
#
#   The chat template's structural markers (<|open|>, <|sep|>, <|close|>) leak
#   into the response text. ik has no parser for K3's template family, so the
#   answer arrives wrapped in section markers. It is correct, but a coding agent
#   will trip over them. See glm-cpu-kit/porting/k3/GRAPH-BUILDER-SPEC.md.
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

exec env \
  XDG_CONFIG_HOME="$CFG_HOME" \
  LITELLM_API_KEY="${LITELLM_API_KEY:-sk-litellm-not-needed}" \
  DO_NOT_TRACK=1 \
  DISABLE_TELEMETRY=1 \
  "$OPENCODE" --model "$MODEL" "$@"
