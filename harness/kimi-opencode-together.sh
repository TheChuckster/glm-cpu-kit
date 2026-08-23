#!/usr/bin/env bash
# OpenCode against Kimi K3 on Together AI, at max reasoning effort by default.
#
#   ./kimi-opencode-together.sh                 # interactive TUI
#   ./kimi-opencode-together.sh --continue      # resume the last session
#   ./kimi-opencode-together.sh run "message"   # headless
#
# Override when a task does not need maximum depth:
#   KIMI_REASONING_EFFORT=high ./kimi-opencode-together.sh
set -euo pipefail

SCRIPT_DIR=$(dirname "$(readlink -f "$0")")
exec env \
  TOGETHER_MODEL="moonshotai/Kimi-K3" \
  TOGETHER_VARIANT="${KIMI_REASONING_EFFORT:-${TOGETHER_VARIANT:-max}}" \
  "$SCRIPT_DIR/together-opencode.sh" "$@"
