#!/usr/bin/env bash
# Shared OpenCode launcher for frontier models on Together AI.
#
# Prefer the small model-specific wrappers:
#   ./kimi-opencode-together.sh       # Kimi K3, max reasoning (default)
#   ./glm53-opencode-together.sh      # GLM-5.3, max reasoning
#   ./qwen38-opencode-together.sh     # Qwen3.8 2.4T, xhigh reasoning
#   ./ds4-pro-opencode-together.sh    # DeepSeek V4 Pro 0813, max reasoning
#
# Or select directly:
#   TOGETHER_MODEL=moonshotai/Kimi-K3 TOGETHER_VARIANT=max ./together-opencode.sh
#
# The key is read from TOGETHER_API_KEY first, then ~/.together-key. The custom
# config only fills catalog gaps for models newer than OpenCode's bundled
# models.dev snapshot; requests use OpenCode's native @ai-sdk/togetherai
# provider so reasoning_content and tool calls survive multi-turn sessions.
set -euo pipefail

SCRIPT_DIR=$(dirname "$(readlink -f "$0")")
MODEL="${TOGETHER_MODEL:-moonshotai/Kimi-K3}"

case "$MODEL" in
  moonshotai/Kimi-K3)
    DEFAULT_VARIANT=max
    DEFAULT_OUTPUT_TOKEN_MAX=130000
    MODEL_NOTE="Kimi K3 (\$3/M input, \$0.30/M cached, \$15/M output)"
    ;;
  Qwen/Qwen3.8-2.4T-A95B)
    DEFAULT_VARIANT=xhigh
    DEFAULT_OUTPUT_TOKEN_MAX=131072
    MODEL_NOTE="Qwen3.8 2.4T (\$2.50/M input, \$0.50/M cached, \$6.25/M output)"
    ;;
  deepseek-ai/DeepSeek-V4-Pro-0813)
    DEFAULT_VARIANT=max
    DEFAULT_OUTPUT_TOKEN_MAX=131072
    MODEL_NOTE="DeepSeek V4 Pro 0813 (\$1.32/M input, \$0.13/M cached, \$3.96/M output)"
    ;;
  zai-org/GLM-5.3)
    DEFAULT_VARIANT=max
    DEFAULT_OUTPUT_TOKEN_MAX=131072
    MODEL_NOTE="GLM-5.3 (\$1.40/M input, \$0.26/M cached, \$4.40/M output)"
    ;;
  zai-org/GLM-5.2)
    DEFAULT_VARIANT=max
    DEFAULT_OUTPUT_TOKEN_MAX=131072
    MODEL_NOTE="GLM-5.2 (\$1.40/M input, \$0.26/M cached, \$4.40/M output)"
    ;;
  *)
    DEFAULT_VARIANT=
    DEFAULT_OUTPUT_TOKEN_MAX=32000
    MODEL_NOTE="$MODEL"
    ;;
esac

# OpenCode otherwise caps every request at 32K even when a reasoning model's
# catalog entry advertises more. That can leave max-tier runs with reasoning
# tokens but no final answer. 131K is supported by these frontier endpoints and
# matches the published agent-evaluation budget; lower it explicitly if desired.
OUTPUT_TOKEN_MAX="${TOGETHER_OUTPUT_TOKEN_MAX:-$DEFAULT_OUTPUT_TOKEN_MAX}"
if ! [[ "$OUTPUT_TOKEN_MAX" =~ ^[1-9][0-9]*$ ]]; then
  echo "TOGETHER_OUTPUT_TOKEN_MAX must be a positive integer." >&2
  exit 2
fi

# `${name-default}` intentionally lets TOGETHER_VARIANT='' select the base
# model without a variant, while an unset variable gets the model's best tier.
VARIANT="${TOGETHER_VARIANT-$DEFAULT_VARIANT}"
case "$MODEL:$VARIANT" in
  moonshotai/Kimi-K3:low|moonshotai/Kimi-K3:high|moonshotai/Kimi-K3:max|\
  Qwen/Qwen3.8-2.4T-A95B:low|Qwen/Qwen3.8-2.4T-A95B:medium|Qwen/Qwen3.8-2.4T-A95B:xhigh|\
  deepseek-ai/DeepSeek-V4-Pro-0813:low|deepseek-ai/DeepSeek-V4-Pro-0813:high|deepseek-ai/DeepSeek-V4-Pro-0813:max|\
  zai-org/GLM-5.3:low|zai-org/GLM-5.3:high|zai-org/GLM-5.3:max|\
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
  echo "  Get one at https://api.together.ai/settings/api-keys" >&2
  exit 1
fi

BASE_URL="${TOGETHER_BASE_URL:-https://api.together.xyz/v1}"

OPENCODE="${OPENCODE_BIN:-/usr/bin/opencode}"
[ -x "$OPENCODE" ] || { echo "opencode CLI not found at $OPENCODE" >&2; exit 1; }

# Isolate the config directory so a large global plugin prompt is not injected.
# OpenCode's session database remains in ~/.local/share/opencode, so --continue
# and -s work across all of these launchers.
CFG_HOME="${TOGETHER_OPENCODE_XDG:-$HOME/.together-opencode-config}"
mkdir -p "$CFG_HOME"

MODEL_REF="togetherai/$MODEL"

# OpenCode 1.18 accepts `--variant` for `run` but not for its default TUI, and
# does not parse a `#variant` suffix in --model. Apply the same model option via
# the highest-precedence inline config so interactive and headless runs behave
# identically. MODEL/VARIANT are restricted to the literal pairs above whenever
# a variant is present, so this JSON cannot contain untrusted text.
INLINE_CONFIG='{}'
if [ -n "$VARIANT" ]; then
  printf -v INLINE_CONFIG \
    '{"provider":{"togetherai":{"models":{"%s":{"options":{"reasoningEffort":"%s"}}}}}}' \
    "$MODEL" "$VARIANT"
fi

echo "Together AI: $MODEL_NOTE${VARIANT:+; effort $VARIANT}; output cap $OUTPUT_TOKEN_MAX tokens/step" >&2
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
