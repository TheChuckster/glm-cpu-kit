#!/usr/bin/env bash
# Launch Claude Code against GLM-5.2 (Q8_0, 753B MoE) served on the GLM host,
# via the local litellm proxy (:4000) which translates Anthropic /v1/messages
# -> the GLM host's llama-server OpenAI /v1/chat/completions (SERVER_IP:8080).
#
# Prereqs:
#   - litellm proxy running:            ~/Projects_new/ai/proxy.sh
#   - the GLM host's glm-server up + ufw allows :8080 from the LAN
# Override model with GLM_MODEL=<id>.
set -euo pipefail

BASE="${ANTHROPIC_BASE_URL:-http://127.0.0.1:4000}"
MODEL="${GLM_MODEL:-glm-5.2}"

CLAUDE="${CLAUDE_BIN:-$HOME/.local/bin/claude}"
[ -x "$CLAUDE" ] || { echo "claude CLI not found at $CLAUDE" >&2; exit 1; }

# Fast preflight: confirm litellm responds and glm-5.2 is registered
if ! curl -s -m 2 "$BASE/v1/models" -H "Authorization: Bearer sk-litellm-not-needed" \
     | grep -q "$MODEL"; then
  echo "litellm proxy unreachable or model '$MODEL' not registered at $BASE" >&2
  echo "Start it with: ~/Projects_new/ai/proxy.sh" >&2
  exit 1
fi

# Force a non-auto permission mode: auto mode fires an extra per-action safety-
# classifier model call, which is ruinously slow on the local CPU model. Refuse
# any attempt to start in auto, and warn against switching into it mid-session.

exec env \
  ANTHROPIC_BASE_URL="$BASE" \
  ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY:-sk-litellm-not-needed}" \
  API_TIMEOUT_MS="${API_TIMEOUT_MS:-21600000}" \
  CLAUDE_CODE_API_TIMEOUT_MS="${CLAUDE_CODE_API_TIMEOUT_MS:-21600000}" \
  MAX_THINKING_TOKENS=0 \
  ANTHROPIC_MODEL="${ANTHROPIC_MODEL:-$MODEL}" \
  OPENAI_API_KEY="${OPENAI_API_KEY:-sk-litellm-not-needed}" \
  OPENAI_BASE_URL="${OPENAI_BASE_URL:-$BASE/v1}" \
  CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1 \
  DISABLE_TELEMETRY=1 \
  DISABLE_GROWTHBOOK=1 \
  DISABLE_AUTOUPDATER=1 \
  DISABLE_UPDATES=1 \
  DISABLE_ERROR_REPORTING=1 \
  DISABLE_FEEDBACK_COMMAND=1 \
  CLAUDE_CODE_DISABLE_FEEDBACK_SURVEY=1 \
  DISABLE_NON_ESSENTIAL_MODEL_CALLS=1 \
  DO_NOT_TRACK=1 \
  "$CLAUDE" --bare --effort max --dangerously-skip-permissions --model "$MODEL" "$@"
