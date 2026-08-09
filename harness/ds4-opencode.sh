#!/usr/bin/env bash
# Launch OpenCode against DeepSeek-V4-Flash on $GLM_SERVER_HOST, via the local
# litellm proxy. Same isolated-config trick as glm-opencode.sh and
# kimi-opencode.sh: a clean XDG_CONFIG_HOME so the global oh-my-openagent plugin
# is NOT loaded, which keeps the system prompt small enough that first-token
# latency is bearable on CPU.
#
#   ./ds4-opencode.sh                  # interactive TUI (new session)
#   ./ds4-opencode.sh --continue       # resume the last session in this dir
#   ./ds4-opencode.sh run "message"    # headless one-shot
#
# USE THIS ONE. Of the three models this box serves, DeepSeek-V4 is the one to
# point a coding harness at. On the "read util.py and say what it does" task:
#
#   DeepSeek-V4  41s,  invoked the tool, correct answer
#   GLM-5.2      67s,  chained Glob then Read, correct answer
#   Kimi K3      234s, chained Glob then Read, correct answer
#
# Those three are one measurement each from the K3 work. Re-measured through THIS
# script on a freshly switched server it took 105s, chaining Read and answering
# correctly - so treat 41s as the warm-cache best case and ~100s as what a cold
# session actually costs. The ordering is the durable part, not the seconds.
#
# DS4 is also the cheapest to run: only 6 of 256 experts fire per token and DSA
# keeps long-context attention sparse, so it holds ~28.6 tok/s generation and
# ~365 tok/s prompt processing where GLM manages 12.4 and K3 4.3.
#
# FIRST RUN AFTER `glm-model use` CAN BE MUCH SLOWER. The first attempt here ran
# past 10 minutes on a server that had just switched models; re-running the same
# task could not reproduce it, and the server log shows normal 371 tok/s prefill
# throughout. Most likely the 130 GB of weights were still being faulted in
# behind an already-healthy /health. If a first invocation seems hung, check
# `ssh $GLM_SERVER_HOST 'sudo journalctl -fu glm-server'` for prompt-eval lines
# before concluding anything is broken - and note that piping this script's
# output through `tail` hides all progress while you wait.
#
# TOOL CALLS ARE FIXED. This needs saying because the registry warned about it
# for weeks: without ik #2242, ik fell back to the autoparser, which forces
# string="true" on every argument. That diverges from what the template renders
# (so it breaks prompt caching) and loses parallel tool calls. The fix is in the
# engine as of 2026-08-07 and all three DS4 rows measure 5/5 on tool calls.
#
# DS4 ALWAYS REASONS. There is no enable_thinking kwarg, so GLM's flag is
# meaningless here - the server passes --reasoning-format deepseek from the
# registry's opts, which routes thoughts into reasoning_content instead of into
# the visible answer. If reasoning starts appearing in content, the server was
# started without that flag, not the model misbehaving.
#
# SAMPLING is fixed by the model, not by taste: DeepSeek's generation_config
# asserts temperature 1.0 / top_p 1.0 and the registry passes exactly that. Do
# not "improve" it here.
#
# DS4 must be the resident model - only one is, and they are 130-860 GB mlocked:
#   ssh $GLM_SERVER_HOST 'sudo glm-model use ds4-flash-q5attn'   # the fast one
#   ssh $GLM_SERVER_HOST 'glm-model status'                      # confirm first
# (GLM_SERVER_HOST defaults to chuckdancer)
#
# NOTE ON THE ALIAS CHECK BELOW: ds4-flash and ds4-flash-q5attn deliberately
# share the alias `deepseek-v4-flash-0731`, because the Q5attn build is the same
# model with its always-read tensors requantized Q8_0 -> Q5_K (+16-17% TG at
# unchanged perplexity) - a drop-in, not a different model. So a passing check
# means "some DS4 is resident", not which one. `glm-model status` names the
# variant if you need to know.
set -euo pipefail

BASE="${OPENCODE_BASE_URL:-http://127.0.0.1:4000/v1}"
# DS4_OPENCODE_MODEL selects which DS4 row you are talking to:
#   local/deepseek-v4-flash-0731      the reference / Q5attn sibling (they share
#                                     this alias deliberately - drop-in equivalents)
#   local/deepseek-v4-flash-0731-abl  huihui-ai's abliterated 0731 MXFP4
#   local/deepseek-v4-flash-0731-mix  antirez's mixed-precision build
# The abliterated row gets its OWN alias on purpose: it is not a drop-in
# equivalent, and you must be able to tell which model answered you.
MODEL="${DS4_OPENCODE_MODEL:-local/deepseek-v4-flash-0731}"
CFG_HOME="${GLM_OPENCODE_XDG:-$HOME/.glm-opencode-config}"

OPENCODE="${OPENCODE_BIN:-/usr/bin/opencode}"
[ -x "$OPENCODE" ] || { echo "opencode CLI not found at $OPENCODE" >&2; exit 1; }

MODEL_ID="${MODEL#*/}"
if ! curl -s -m 2 "$BASE/models" -H "Authorization: Bearer sk-litellm-not-needed" \
     | grep -q "$MODEL_ID"; then
  echo "litellm proxy unreachable or model '$MODEL_ID' not registered at $BASE" >&2
  echo "Start it with: ~/Projects_new/ai/proxy.sh" >&2
  exit 1
fi

# llama-server serves whatever is loaded regardless of the name asked for, so a
# request for deepseek-v4-flash-0731 while K3 is resident silently answers as K3.
# Warn rather than fail: the check needs SSH, which may not be available.
GLM_HOST="${GLM_SERVER_HOST:-chuckdancer}"

SERVED=$(ssh -o BatchMode=yes -o ConnectTimeout=4 "$GLM_HOST" \
           'glm-model status 2>/dev/null | sed -n "s/^serving alias *: //p"' 2>/dev/null || true)
if [ -n "$SERVED" ] && [ "$SERVED" != "$MODEL_ID" ]; then
  echo "WARNING: $GLM_HOST is serving '$SERVED', not $MODEL_ID." >&2
  echo "         Your requests will be answered by that model instead." >&2
  case "$MODEL_ID" in
    *-abl) echo "         Switch with: ssh $GLM_HOST 'sudo glm-model use ds4-flash-abl'" >&2 ;;
    *-mix) echo "         Switch with: ssh $GLM_HOST 'sudo glm-model use ds4-flash-mix'" >&2 ;;
    *)     echo "         Switch with: ssh $GLM_HOST 'sudo glm-model use ds4-flash-q5attn'" >&2 ;;
  esac
  echo >&2
fi

exec env \
  XDG_CONFIG_HOME="$CFG_HOME" \
  LITELLM_API_KEY="${LITELLM_API_KEY:-sk-litellm-not-needed}" \
  DO_NOT_TRACK=1 \
  DISABLE_TELEMETRY=1 \
  "$OPENCODE" --model "$MODEL" "$@"
