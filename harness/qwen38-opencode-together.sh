#!/usr/bin/env bash
# OpenCode against the open-weight Qwen3.8 2.4T A95B on Together AI.
#
# Qwen calls its deepest reasoning tier `xhigh` (not `max`). It is the closest
# current open-weight peer to Kimi K3: essentially tied on HLE and close on
# GPQA, though K3 leads the broader coding/agent comparison. Its output tokens
# are materially cheaper.
#
#   ./qwen38-opencode-together.sh                 # interactive TUI
#   ./qwen38-opencode-together.sh --continue      # resume the last session
#   ./qwen38-opencode-together.sh run "message"   # headless
#   QWEN_REASONING_EFFORT=medium ./qwen38-opencode-together.sh
set -euo pipefail

SCRIPT_DIR=$(dirname "$(readlink -f "$0")")
exec env \
  TOGETHER_MODEL="Qwen/Qwen3.8-2.4T-A95B" \
  TOGETHER_VARIANT="${QWEN_REASONING_EFFORT:-${TOGETHER_VARIANT:-xhigh}}" \
  "$SCRIPT_DIR/together-opencode.sh" "$@"
