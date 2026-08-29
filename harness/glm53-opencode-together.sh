#!/usr/bin/env bash
# OpenCode against GLM-5.3 on Together AI, at max reasoning by default.
#
#   ./glm53-opencode-together.sh                 # interactive TUI
#   ./glm53-opencode-together.sh --continue      # resume the last session
#   ./glm53-opencode-together.sh run "message"   # headless
#   GLM53_REASONING_EFFORT=high ./glm53-opencode-together.sh
#
# The live endpoint supports low/high/max. Reasoning tokens are billed as output
# tokens, so high or low is useful for routine edits; max is the quality-first
# default and matches the local registry row.
set -euo pipefail

SCRIPT_DIR=$(dirname "$(readlink -f "$0")")
exec env \
  TOGETHER_MODEL="zai-org/GLM-5.3" \
  TOGETHER_VARIANT="${GLM53_REASONING_EFFORT:-${TOGETHER_VARIANT:-max}}" \
  "$SCRIPT_DIR/together-opencode.sh" "$@"
