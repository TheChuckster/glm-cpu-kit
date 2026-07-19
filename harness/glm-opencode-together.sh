#!/usr/bin/env bash
# opencode against GLM-5.2 on Together AI (fastest route, ~346 tok/s) — one command.
# It just presets the Together endpoint/slug and calls glm-opencode-cloud.sh.
#
# ONE-TIME: put your Together key in ~/.together-key  (get it at
#           https://api.together.ai/settings/api-keys):
#     printf '%s' 'YOUR_TOGETHER_KEY' > ~/.together-key && chmod 600 ~/.together-key
#
# THEN:
#   ./glm-opencode-together.sh                 # interactive TUI
#   ./glm-opencode-together.sh --continue      # resume last session
#   ./glm-opencode-together.sh run "message"   # headless
#
# Override the model with:  TOGETHER_MODEL=zai-org/GLM-5.2 ./glm-opencode-together.sh
set -euo pipefail
KEY="${TOGETHER_API_KEY:-$(cat "$HOME/.together-key" 2>/dev/null || true)}"
[ -n "$KEY" ] || {
  echo "No Together key. Put it in ~/.together-key (chmod 600) or export TOGETHER_API_KEY." >&2
  echo "  Get one at https://api.together.ai/settings/api-keys" >&2
  exit 1
}
exec env \
  CLOUD_BASE_URL="https://api.together.xyz/v1" \
  CLOUD_MODEL="${TOGETHER_MODEL:-zai-org/GLM-5.2}" \
  CLOUD_API_KEY="$KEY" \
  "$(dirname "$(readlink -f "$0")")/glm-opencode-cloud.sh" "$@"
