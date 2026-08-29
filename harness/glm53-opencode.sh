#!/usr/bin/env bash
# Launch OpenCode directly against the local GLM-5.3 CPU server.
#
#   ./glm53-opencode.sh                  # interactive TUI
#   ./glm53-opencode.sh --continue       # resume the last session
#   ./glm53-opencode.sh -s <session-id>  # resume a specific session
#   ./glm53-opencode.sh run "message"    # headless one-shot
#   GLM53_BASE_URL=http://host:port/v1 ./glm53-opencode.sh  # direct-route override
#
# GLM-5.3 uses max reasoning in the production registry row. Only one giant
# model is resident, and llama-server accepts a request naming the wrong alias,
# so this launcher verifies both the selected variant and the served alias before
# starting OpenCode. If the right model is still loading, it waits rather than
# misreporting the missing /v1/models response as malformed status.
set -euo pipefail

CFG_HOME="${GLM_OPENCODE_XDG:-$HOME/.glm-opencode-config}"
MODEL="${GLM53_OPENCODE_MODEL:-local/glm-5.3}"
GLM53_VARIANT="${GLM53_VARIANT:-glm53-q4xl}"
GLM53_READY_TIMEOUT="${GLM53_READY_TIMEOUT:-1800}"
GLM53_READY_POLL="${GLM53_READY_POLL:-5}"
GLM_HOST="${GLM_SERVER_HOST:-chuckdancer}"
GLM_SSH_CONFIG="${GLM_SSH_CONFIG:-none}"
GLM53_DIRECT_BASE_URL="${GLM53_BASE_URL:-http://$GLM_HOST:8080/v1}"
GLM53_DIRECT_BASE_URL="${GLM53_DIRECT_BASE_URL%/}"
GLM53_SKIP_VARIANT_CHECK="${GLM53_SKIP_VARIANT_CHECK:-0}"
OPENCODE="${OPENCODE_BIN:-/usr/bin/opencode}"

MODEL_ID="${MODEL#*/}"
CONFIG="$CFG_HOME/opencode/opencode.json"
API_KEY="${GLM_API_KEY:-$(cat "${GLM_API_KEY_FILE:-$HOME/.glm-api-key}" 2>/dev/null || true)}"

[ -x "$OPENCODE" ] || { echo "opencode CLI not found at $OPENCODE" >&2; exit 1; }
[ -f "$CONFIG" ] || { echo "put opencode.json at $CONFIG" >&2; exit 1; }
[ -x "$(command -v curl 2>/dev/null)" ] || { echo "curl is required" >&2; exit 1; }
[ -n "$API_KEY" ] || {
  echo "No local server key. Export GLM_API_KEY or set GLM_API_KEY_FILE." >&2
  exit 1
}
[[ "$GLM53_SKIP_VARIANT_CHECK" == 0 || "$GLM53_SKIP_VARIANT_CHECK" == 1 ]] || {
  echo "GLM53_SKIP_VARIANT_CHECK must be 0 or 1" >&2
  exit 1
}
[[ "$GLM53_READY_TIMEOUT" =~ ^[0-9]+$ ]] || {
  echo "GLM53_READY_TIMEOUT must be a non-negative integer" >&2
  exit 1
}
[[ "$GLM53_READY_POLL" =~ ^[1-9][0-9]*$ ]] || {
  echo "GLM53_READY_POLL must be a positive integer" >&2
  exit 1
}

if [ "$GLM53_SKIP_VARIANT_CHECK" = 0 ]; then
  READY_STARTED=$SECONDS
  READY_NOTICE=0
  while :; do
    if ! STATUS=$(ssh -F "$GLM_SSH_CONFIG" -o BatchMode=yes -o ConnectTimeout=4 "$GLM_HOST" \
        'glm-model status 2>/dev/null' 2>/dev/null); then
      echo "ERROR: cannot verify the resident model on $GLM_HOST; refusing an ambiguous launch." >&2
      echo "       Fix SSH, or explicitly set GLM53_SKIP_VARIANT_CHECK=1." >&2
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
    if [ "$SELECTED" != "$GLM53_VARIANT" ]; then
      echo "ERROR: $GLM_HOST selected '$SELECTED', not '$GLM53_VARIANT'." >&2
      echo "       Switch with: ssh -F $GLM_SSH_CONFIG $GLM_HOST 'sudo glm-model use $GLM53_VARIANT'" >&2
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

    if [ "$SERVICE_STATE" != active ] && [ "$SERVICE_STATE" != activating ]; then
      echo "ERROR: $GLM_HOST selected '$GLM53_VARIANT', but glm-server is '$SERVICE_STATE'." >&2
      printf '%s\n' "$STATUS" | sed 's/^/       /' >&2
      exit 1
    fi
    if [ -n "$HEALTH" ] && [[ "$HEALTH" != "not responding"* ]]; then
      echo "ERROR: $GLM_HOST is healthy but its serving alias could not be verified." >&2
      printf '%s\n' "$STATUS" | sed 's/^/       /' >&2
      exit 1
    fi

    READY_ELAPSED=$((SECONDS - READY_STARTED))
    if (( READY_ELAPSED >= GLM53_READY_TIMEOUT )); then
      echo "ERROR: timed out after ${GLM53_READY_TIMEOUT}s waiting for $GLM53_VARIANT on $GLM_HOST." >&2
      printf '%s\n' "$STATUS" | sed 's/^/       /' >&2
      exit 1
    fi
    if [ "$READY_NOTICE" = 0 ]; then
      echo "$GLM53_VARIANT is selected on $GLM_HOST and is still loading; waiting up to ${GLM53_READY_TIMEOUT}s." >&2
      echo "Watch it: ssh -F $GLM_SSH_CONFIG $GLM_HOST 'sudo journalctl -fu glm-server'" >&2
      READY_NOTICE=1
    fi
    READY_REMAINING=$((GLM53_READY_TIMEOUT - READY_ELAPSED))
    if (( READY_REMAINING < GLM53_READY_POLL )); then
      sleep "$READY_REMAINING"
    else
      sleep "$GLM53_READY_POLL"
    fi
  done
fi

# The isolated config predates this launcher and may still route provider
# `local` through a localhost LiteLLM proxy. Prove the direct route from this
# workstation separately from the SSH-side resident-model check. Otherwise a
# stopped proxy or an unreachable server would become another infinite spinner.
if ! DIRECT_MODELS=$(curl -sf --max-time 5 \
    -H "Authorization: Bearer $API_KEY" "$GLM53_DIRECT_BASE_URL/models" 2>/dev/null); then
  echo "ERROR: direct GLM endpoint is unreachable at $GLM53_DIRECT_BASE_URL." >&2
  echo "       Fix network access or override GLM53_BASE_URL; refusing to launch." >&2
  exit 1
fi
DIRECT_SERVED=$(printf '%s' "$DIRECT_MODELS" | python3 -c \
  'import json,sys; print((json.load(sys.stdin).get("data") or [{}])[0].get("id", ""))' \
  2>/dev/null || true)
if [ "$DIRECT_SERVED" != "$MODEL_ID" ]; then
  echo "ERROR: direct endpoint serves '$DIRECT_SERVED', not '$MODEL_ID'." >&2
  echo "       Requests would be answered by the wrong model; refusing to launch." >&2
  exit 1
fi

echo "GLM-5.3 Q4 CPU: max reasoning is enabled; large fresh prompts can be quiet for minutes." >&2
echo "                The launcher verified resident variant '$GLM53_VARIANT' as '$MODEL_ID'." >&2

# Add the newly released catalog entry at the highest-precedence config layer.
# This lets the launcher work with an existing isolated config immediately; the
# checked-in opencode.json carries the same entry for fresh installations.
INLINE_CONFIG='{"provider":{"local":{"options":{"baseURL":"{env:GLM53_DIRECT_BASE_URL}","apiKey":"{env:GLM_API_KEY}","timeout":false,"chunkTimeout":300000},"models":{"glm-5.3":{"name":"GLM-5.3 (UD-Q4_K_XL, CPU, max reasoning)","limit":{"context":60000,"output":32768}}}}}}'

exec env \
  XDG_CONFIG_HOME="$CFG_HOME" \
  OPENCODE_CONFIG_CONTENT="$INLINE_CONFIG" \
  GLM_API_KEY="$API_KEY" \
  GLM53_DIRECT_BASE_URL="$GLM53_DIRECT_BASE_URL" \
  DO_NOT_TRACK=1 \
  DISABLE_TELEMETRY=1 \
  "$OPENCODE" --model "$MODEL" "$@"
