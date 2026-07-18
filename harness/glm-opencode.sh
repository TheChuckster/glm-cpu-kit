#!/usr/bin/env bash
# Launch opencode against GLM-5.2 DIRECT to the ik_llama.cpp server (no litellm needed).
# Uses an isolated config dir so global plugins/config don't bloat the system prompt.
#
#   ./glm-opencode.sh                  # interactive TUI (new session)
#   ./glm-opencode.sh --continue       # RESUME the last session in this dir (-c)
#   ./glm-opencode.sh -s <session-id>  # resume a specific session (--fork to branch)
#   ./glm-opencode.sh session          # list / manage sessions
#   ./glm-opencode.sh run "message"    # headless one-shot   (add -c to continue last)
#
# Sessions persist in ~/.local/share/opencode/opencode.db (NOT the config dir), so resume works
# across launches. Resuming a large session re-processes its context on the first message unless the
# server's prompt cache is still warm -> DON'T bounce glm-server mid-audit if you want fast resume.
#
# LARGE-CONTEXT / AUDIT MODE: for batch whole-codebase security audits, run the server with
# CTX=131072 (128K) and set opencode's limit.context to 120000 (see opencode.json). Expect a
# ~2-3 HOUR first token; treat it as a batch job, then ask follow-ups against the warm cache.
#
# SETUP: put this kit's opencode.json at "$CFG_HOME/opencode/opencode.json" (edit SERVER_IP), then
#        export GLM_API_KEY  (the contents of ~/.glm-api-key on the server).
set -euo pipefail
CFG_HOME="${GLM_OPENCODE_XDG:-$HOME/.glm-opencode-config}"
MODEL="${GLM_OPENCODE_MODEL:-local/glm-5.2}"
OPENCODE="${OPENCODE_BIN:-opencode}"
: "${GLM_API_KEY:?export GLM_API_KEY (the server's ~/.glm-api-key contents)}"
[ -f "$CFG_HOME/opencode/opencode.json" ] || { echo "put opencode.json at $CFG_HOME/opencode/opencode.json"; exit 1; }
command -v "$OPENCODE" >/dev/null || { echo "opencode not found (set OPENCODE_BIN)"; exit 1; }

exec env \
  XDG_CONFIG_HOME="$CFG_HOME" \
  GLM_API_KEY="$GLM_API_KEY" \
  DO_NOT_TRACK=1 DISABLE_TELEMETRY=1 \
  "$OPENCODE" --model "$MODEL" "$@"
