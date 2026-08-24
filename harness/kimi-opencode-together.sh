#!/usr/bin/env bash
# OpenCode against Kimi K3 on Together AI, explicitly at max reasoning effort.
#
#   ./kimi-opencode-together.sh                 # interactive TUI
#   ./kimi-opencode-together.sh --continue      # resume the last session
#   ./kimi-opencode-together.sh run "message"   # headless
#
# K3 supports low/high/max; max is both this launcher's default and the tier
# used for Moonshot's published frontier benchmark results. Thinking tokens are
# billed as output tokens, so override when the task does not need max depth:
#   KIMI_REASONING_EFFORT=high ./kimi-opencode-together.sh
set -euo pipefail

SCRIPT_DIR=$(dirname "$(readlink -f "$0")")
exec env \
  TOGETHER_MODEL="moonshotai/Kimi-K3" \
  TOGETHER_VARIANT="${KIMI_REASONING_EFFORT:-${TOGETHER_VARIANT:-max}}" \
  "$SCRIPT_DIR/together-opencode.sh" "$@"
