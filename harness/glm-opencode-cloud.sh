#!/usr/bin/env bash
# opencode against a CLOUD GLM-5.2 provider (OpenRouter by default) for HIGH THROUGHPUT.
# ~190-350 tok/s per stream vs ~10 on the local CPU box -> 20-35x faster, and cheap.
# TRADE-OFF: NOT private -- your code/prompts go to the provider. Use the LOCAL
# ./glm-opencode.sh for sensitive or audit work; use THIS for fast everyday coding.
#
#   ./glm-opencode-cloud.sh                 # interactive TUI (new session)
#   ./glm-opencode-cloud.sh --continue      # resume the last session (-c)
#   ./glm-opencode-cloud.sh -s <id>         # resume a specific session
#   ./glm-opencode-cloud.sh run "message"   # headless one-shot (add -c to continue)
#
# SETUP (OpenRouter): get a key at https://openrouter.ai/keys, then:
#   export CLOUD_API_KEY=sk-or-...
#   ./glm-opencode-cloud.sh
#
# Env / routing knobs:
#   CLOUD_API_KEY   REQUIRED  (OpenRouter sk-or-...  or any provider's key)
#   CLOUD_BASE_URL  default https://openrouter.ai/api/v1  (any OpenAI-compatible endpoint;
#                   point at DeepInfra/Together/Z.ai/etc. to switch provider)
#   CLOUD_MODEL     default z-ai/glm-5.2        -> cheapest auto-route (fp4, ~$0.41/1M in)
#                   z-ai/glm-5.2:nitro          -> fastest route (~346 tok/s, ~$0.93/1M in)
#                   z-ai/glm-5.2[1m]            -> 1M-context variant
#   CLOUD_CTX       default 131072  (cloud is fast, so big context is fine -- it just costs more)
#
# PROVIDER CHEAT-SHEET  (export CLOUD_BASE_URL / CLOUD_MODEL / CLOUD_API_KEY):
#   OpenRouter (default) https://openrouter.ai/api/v1          z-ai/glm-5.2      key sk-or-...
#   Together AI          https://api.together.xyz/v1           zai-org/GLM-5.2   (~346 tps, fastest)
#   DeepInfra            https://api.deepinfra.com/v1/openai   zai-org/GLM-5.2   (cheap, fp4)
#   Z.ai (first-party)   https://api.z.ai/api/paas/v4          glm-5.2           (fp8, list price)
#   Surplus Intelligence https://api.surplusintelligence.ai/v1 <VERIFY slug>    key inf_...
#       ^ marketplace reselling "surplus capacity". VERIFY GLM-5.2 is actually in its catalog
#         (landing page only showed Opus/GPT) and note prompts route to UNKNOWN resold backends
#         -- fine for throwaway coding, NOT for sensitive code (use the local ./glm-opencode.sh).
#
# NOTE ON "1k-10k tps": that's AGGREGATE across many concurrent requests. A single opencode
# session is ONE stream (~190-350 tok/s). You only approach 1k+ by fanning out many parallel
# requests (heavy subagent use / batch jobs). For interactive coding, per-stream is the number.
set -euo pipefail
: "${CLOUD_API_KEY:?export CLOUD_API_KEY (OpenRouter sk-or-... -> https://openrouter.ai/keys)}"
CLOUD_BASE_URL="${CLOUD_BASE_URL:-https://openrouter.ai/api/v1}"
CLOUD_MODEL="${CLOUD_MODEL:-z-ai/glm-5.2}"
CLOUD_CTX="${CLOUD_CTX:-131072}"
CFG="${GLM_CLOUD_XDG:-$HOME/.glm-cloud-config}"
command -v opencode >/dev/null || { echo "opencode not found"; exit 1; }

mkdir -p "$CFG/opencode"
cat > "$CFG/opencode/opencode.json" <<EOF
{
  "\$schema": "https://opencode.ai/config.json",
  "provider": {
    "cloud": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "GLM-5.2 (cloud)",
      "options": {
        "baseURL": "$CLOUD_BASE_URL",
        "apiKey": "{env:CLOUD_API_KEY}",
        "headers": { "HTTP-Referer": "https://github.com/TheChuckster/glm-cpu-kit", "X-Title": "opencode-glm" }
      },
      "models": {
        "$CLOUD_MODEL": { "name": "GLM-5.2 ($CLOUD_MODEL)", "limit": { "context": $CLOUD_CTX, "output": 32000 } }
      }
    }
  }
}
EOF

exec env \
  XDG_CONFIG_HOME="$CFG" \
  CLOUD_API_KEY="$CLOUD_API_KEY" \
  DO_NOT_TRACK=1 DISABLE_TELEMETRY=1 \
  opencode --model "cloud/$CLOUD_MODEL" "$@"
