#!/usr/bin/env bash
# Shared OpenCode launcher for reasoning models on Together AI.
#
# Prefer the model wrapper:
#   ./kimi-opencode-together.sh       # Kimi K3, max reasoning by default
#
# Or select directly:
#   TOGETHER_MODEL=moonshotai/Kimi-K3 TOGETHER_VARIANT=max ./together-opencode.sh
#
# This uses OpenCode's native Together provider so reasoning_content and tool
# calls survive multi-turn sessions. The key comes from TOGETHER_API_KEY, then
# ~/.together-key.
set -euo pipefail

SCRIPT_DIR=$(dirname "$(readlink -f "$0")")
MODEL="${TOGETHER_MODEL:-moonshotai/Kimi-K3}"

case "$MODEL" in
  moonshotai/Kimi-K3)
    DEFAULT_VARIANT=max
    DEFAULT_OUTPUT_TOKEN_MAX=130000
    MODEL_NOTE="Kimi K3 max"
    ;;
  zai-org/GLM-5.2)
    DEFAULT_VARIANT=max
    DEFAULT_OUTPUT_TOKEN_MAX=131072
    MODEL_NOTE="GLM-5.2 max"
    ;;
  *)
    DEFAULT_VARIANT=
    DEFAULT_OUTPUT_TOKEN_MAX=32000
    MODEL_NOTE="$MODEL"
    ;;
esac

OUTPUT_TOKEN_MAX="${TOGETHER_OUTPUT_TOKEN_MAX:-$DEFAULT_OUTPUT_TOKEN_MAX}"
if ! [[ "$OUTPUT_TOKEN_MAX" =~ ^[1-9][0-9]*$ ]]; then
  echo "TOGETHER_OUTPUT_TOKEN_MAX must be a positive integer." >&2
  exit 2
fi

# An explicitly empty TOGETHER_VARIANT selects the model without a reasoning
# variant; an unset variable gets the model's best tier.
VARIANT="${TOGETHER_VARIANT-$DEFAULT_VARIANT}"
case "$MODEL:$VARIANT" in
  moonshotai/Kimi-K3:low|moonshotai/Kimi-K3:high|moonshotai/Kimi-K3:max|\
  zai-org/GLM-5.2:high|zai-org/GLM-5.2:max|*:)
    ;;
  *)
    echo "Unsupported Together reasoning variant '$VARIANT' for '$MODEL'." >&2
    exit 2
    ;;
esac

KEY="${TOGETHER_API_KEY:-$(cat "$HOME/.together-key" 2>/dev/null || true)}"
if [ -z "$KEY" ]; then
  echo "No Together key. Put it in ~/.together-key (chmod 600) or export TOGETHER_API_KEY." >&2
  exit 1
fi

BASE_URL="${TOGETHER_BASE_URL:-https://api.together.xyz/v1}"
OPENCODE="${OPENCODE_BIN:-/usr/bin/opencode}"
[ -x "$OPENCODE" ] || { echo "opencode CLI not found at $OPENCODE" >&2; exit 1; }

CFG_HOME="${TOGETHER_OPENCODE_XDG:-$HOME/.together-opencode-config}"
mkdir -p "$CFG_HOME"

MODEL_REF="togetherai/$MODEL"
INLINE_CONFIG='{}'
if [ -n "$VARIANT" ]; then
  printf -v INLINE_CONFIG \
    '{"provider":{"togetherai":{"models":{"%s":{"options":{"reasoningEffort":"%s"}}}}}}' \
    "$MODEL" "$VARIANT"
fi

echo "Together AI: $MODEL_NOTE; output cap $OUTPUT_TOKEN_MAX tokens/step" >&2
exec env \
  XDG_CONFIG_HOME="$CFG_HOME" \
  OPENCODE_CONFIG="$SCRIPT_DIR/together-opencode.json" \
  OPENCODE_CONFIG_CONTENT="$INLINE_CONFIG" \
  OPENCODE_EXPERIMENTAL_OUTPUT_TOKEN_MAX="$OUTPUT_TOKEN_MAX" \
  TOGETHER_API_KEY="$KEY" \
  TOGETHER_BASE_URL="$BASE_URL" \
  DO_NOT_TRACK=1 \
  DISABLE_TELEMETRY=1 \
  "$OPENCODE" --model "$MODEL_REF" "$@"
