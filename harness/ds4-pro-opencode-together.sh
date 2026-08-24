#!/usr/bin/env bash
# OpenCode against DeepSeek V4 Pro 0813 on Together AI at max reasoning effort.
#
# It trails Kimi K3/Qwen3.8 on single-shot general-reasoning scores, but is the
# useful frontier value alternate for long coding loops: 1M context, max-tier
# reasoning, and much cheaper output. Keep this as an escalation/routing peer,
# not as a claim that it beats K3 overall.
#
#   ./ds4-pro-opencode-together.sh                 # interactive TUI
#   ./ds4-pro-opencode-together.sh --continue      # resume the last session
#   ./ds4-pro-opencode-together.sh run "message"   # headless
#   DEEPSEEK_REASONING_EFFORT=high ./ds4-pro-opencode-together.sh
set -euo pipefail

SCRIPT_DIR=$(dirname "$(readlink -f "$0")")
exec env \
  TOGETHER_MODEL="deepseek-ai/DeepSeek-V4-Pro-0813" \
  TOGETHER_VARIANT="${DEEPSEEK_REASONING_EFFORT:-${TOGETHER_VARIANT:-max}}" \
  "$SCRIPT_DIR/together-opencode.sh" "$@"
