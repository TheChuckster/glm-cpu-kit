#!/usr/bin/env bash
# Reproducible PP/TG sample against the model already resident in llama-server.
# This avoids loading a second several-hundred-GB copy just to run llama-bench.
#
#   ./benchmark-live.sh
#   BASE_URL=http://server:8080 API_KEY_FILE=~/.glm-api-key ./benchmark-live.sh
#
# Environment overrides: BASE_URL, API_KEY_FILE, EXPECTED_MODEL, TG_TOKENS.
set -euo pipefail

BASE_URL="${BASE_URL:-http://127.0.0.1:8080}"
BASE_URL="${BASE_URL%/}"
API_KEY_FILE="${API_KEY_FILE:-$HOME/.glm-api-key}"
EXPECTED_MODEL="${EXPECTED_MODEL:-}"
TG_TOKENS="${TG_TOKENS:-128}"

[ -r "$API_KEY_FILE" ] || { echo "cannot read API key: $API_KEY_FILE" >&2; exit 1; }
command -v curl >/dev/null || { echo "curl is required" >&2; exit 1; }
command -v jq >/dev/null || { echo "jq is required" >&2; exit 1; }

API_KEY=$(cat "$API_KEY_FILE")
CURL=(curl --fail --silent --show-error --max-time 1200
      -H "Authorization: Bearer $API_KEY")

loaded=$("${CURL[@]}" "$BASE_URL/v1/models" | jq -r '.data[0].id // empty')
[ -n "$loaded" ] || { echo "server returned no loaded model" >&2; exit 1; }
if [ -n "$EXPECTED_MODEL" ] && [ "$loaded" != "$EXPECTED_MODEL" ]; then
    echo "expected '$EXPECTED_MODEL', but server reports '$loaded'" >&2
    exit 1
fi
echo "loaded model: $loaded"

completion() {
    "${CURL[@]}" -H 'Content-Type: application/json' --data-binary @- "$BASE_URL/completion"
}

# Warm kernels and page mappings. Do not mix this short sample into the mean.
jq -nc '{prompt:"A CPU inference warmup.",n_predict:8,ignore_eos:true,cache_prompt:false,seed:20260822,temperature:0.8}' \
    | completion >/dev/null

# The generated prompt is 897 tokens with Kimi K3's tokenizer. n_predict=1
# isolates prompt processing; its timing is intentionally not reported as TG.
long_prompt=$(seq -f 'benchmark_item_%04g contains a deterministic sentence for CPU prompt processing.' 1 64 | tr '\n' ' ')
pp=$(jq -nc --arg prompt "$long_prompt" \
        '{prompt:$prompt,n_predict:1,ignore_eos:true,cache_prompt:false,seed:20260822,temperature:0.8}' \
     | completion)
echo "$pp" | jq -r '"PP: \(.timings.prompt_n) tokens in \(.timings.prompt_ms / 1000)s = \(.timings.prompt_per_second) tok/s"'

rates=()
for seed in 20260822 20260823 20260824; do
    sample=$(jq -nc --argjson seed "$seed" --argjson n "$TG_TOKENS" \
        '{prompt:"Continue with a detailed technical explanation of CPU inference benchmarking:",n_predict:$n,ignore_eos:true,cache_prompt:false,seed:$seed,temperature:0.8}' \
        | completion)
    rate=$(echo "$sample" | jq -r '.timings.predicted_per_second')
    rates+=("$rate")
    echo "$sample" | jq -r --arg seed "$seed" \
        '"TG seed=" + $seed + ": \(.timings.predicted_n) tokens in \(.timings.predicted_ms / 1000)s = \(.timings.predicted_per_second) tok/s"'
done

mean=$(printf '%s\n' "${rates[@]}" | awk '{sum += $1} END {printf "%.3f", sum / NR}')
echo "TG mean: $mean tok/s (${#rates[@]} x $TG_TOKENS tokens)"
