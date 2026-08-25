#!/usr/bin/env bash
# Capture K3 v5 raw activations and derive source/bootstrap/Q5 SOM directions.
# This never changes weights or selects a live model. The caller must stop and
# restore production explicitly around it.
set -euo pipefail
umask 077

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
IK_DIR=${IK_DIR:-/home/chuck/ik_llama.cpp-v5}
BUILD_DIR=${BUILD_DIR:-$IK_DIR/build-abliteration}
PYTHON=${PYTHON:-/models/.abliteration/k3/v5-env/bin/python}
PROMPTS_DIR=${PROMPTS_DIR:-/models/.abliteration/k3/v5-prompts}
CAPTURE_DIR=${CAPTURE_DIR:-/models/.abliteration/k3/v5-capture}
DIRECTION_DIR=${DIRECTION_DIR:-/models/.abliteration/k3/v5-directions}
SOURCE_MODEL=${SOURCE_MODEL:-/models/Kimi-K3-UD-Q2_K_XL/Kimi-K3-UD-Q2_K_XL-00001-of-00019.gguf}
Q5_MODEL=${Q5_MODEL:-/models/Kimi-K3-Q5attn/Kimi-K3-Q5attn-00001-of-00019.gguf}
THREADS=${THREADS:-64}
CVECTOR=$BUILD_DIR/bin/llama-cvector-generator
GGUF_PY=$IK_DIR/gguf-py
ENGINE_COMMIT=dd0bf0177f78657960364493d0220350a82548fb

die() { echo "capture_v5_directions: $*" >&2; exit 1; }

pgrep -f '/llama-(server|perplexity|cvector-generator|quantize)([[:space:]]|$)' \
    >/dev/null 2>&1 && die "another llama model workload is running"
[ "$(git -C "$IK_DIR" rev-parse HEAD)" = "$ENGINE_COMMIT" ] \
    || die "engine commit changed"
[ -x "$CVECTOR" ] || die "missing cvector generator: $CVECTOR"
[ -x "$PYTHON" ] || die "missing locked v5 Python: $PYTHON"
[ -r "$SOURCE_MODEL" ] || die "missing source model"
[ -r "$Q5_MODEL" ] || die "missing Q5 model"
[ -d "$GGUF_PY/gguf" ] || die "missing engine GGUF Python package"
[[ "$THREADS" =~ ^[1-9][0-9]*$ ]] || die "THREADS must be positive"
"$SCRIPT_DIR/verify_v5_prompts.py" "$PROMPTS_DIR"

for directory in "$CAPTURE_DIR" "$DIRECTION_DIR"; do
    if [ -d "$directory" ] && find "$directory" -mindepth 1 -maxdepth 1 -print -quit | grep -q .; then
        die "refusing to overwrite non-empty $directory"
    fi
done
mkdir -p "$CAPTURE_DIR" "$DIRECTION_DIR"

mapfile -t RUNTIME_PATHS < <(
    ldd "$CVECTOR" |
        awk '{ for (i = 1; i <= NF; ++i) if ($i ~ /^\//) { print $i; break } }' |
        sort -u
)
[ "${#RUNTIME_PATHS[@]}" -gt 0 ] || die "could not resolve cvector runtime"
sha256sum \
    "$CVECTOR" "${RUNTIME_PATHS[@]}" \
    "$SCRIPT_DIR/capture_v5_directions.sh" \
    "$SCRIPT_DIR/generate_v5_directions.py" \
    "$SCRIPT_DIR/verify_v5_directions.py" \
    "$SCRIPT_DIR/verify_v5_prompts.py" \
    "$SCRIPT_DIR/v5-requirements.txt" \
    > "$CAPTURE_DIR/engine-and-method.sha256"

COMMON_ARGS=(
    --method mean-last --apply-chat-template --jinja
    --reasoning-format deepseek
    --chat-template-kwargs '{"thinking_effort":"low"}'
    --ctx-size 2048 --batch-size 2048 --ubatch-size 2048
    --threads "$THREADS" --threads-batch "$THREADS" -fa on
    --positive-file "$PROMPTS_DIR/train.harmful.txt"
    --negative-file "$PROMPTS_DIR/train.harmless.txt"
)

"$CVECTOR" --model "$SOURCE_MODEL" "${COMMON_ARGS[@]}" \
    --output "$CAPTURE_DIR/source-mean.gguf" \
    --activations-output "$CAPTURE_DIR/source-activations.gguf" \
    --activations-layers 56-73 \
    2>&1 | tee "$CAPTURE_DIR/source.log"

"$PYTHON" "$SCRIPT_DIR/generate_v5_directions.py" source \
    "$CAPTURE_DIR/source-activations.gguf" "$DIRECTION_DIR" \
    --gguf-py "$GGUF_PY"

selected_layer=$(
    "$PYTHON" -c \
        'import json,sys; print(json.load(open(sys.argv[1]))["k3_adaptation"]["selected_layer"])' \
        "$DIRECTION_DIR/train.manifest.json"
)
[[ "$selected_layer" =~ ^(5[6-9]|6[0-9]|7[0-3])$ ]] \
    || die "invalid selected layer: $selected_layer"

"$CVECTOR" --model "$Q5_MODEL" "${COMMON_ARGS[@]}" \
    --output "$CAPTURE_DIR/q5-mean.gguf" \
    --activations-output "$CAPTURE_DIR/q5-activations.gguf" \
    --activations-layers "$selected_layer" \
    2>&1 | tee "$CAPTURE_DIR/q5.log"

PYTHONPATH=$SCRIPT_DIR "$PYTHON" "$SCRIPT_DIR/generate_v5_directions.py" q5 \
    "$CAPTURE_DIR/q5-activations.gguf" "$DIRECTION_DIR" \
    --gguf-py "$GGUF_PY"

PYTHONPATH=$SCRIPT_DIR "$PYTHON" "$SCRIPT_DIR/verify_v5_directions.py" \
    "$DIRECTION_DIR" \
    "$CAPTURE_DIR/source-activations.gguf" \
    "$CAPTURE_DIR/q5-activations.gguf" \
    --json "$DIRECTION_DIR/verification.json"

sha256sum "$CAPTURE_DIR"/* "$DIRECTION_DIR"/* \
    > "$CAPTURE_DIR/all-artifacts.sha256"
echo "v5 activation capture and directions complete; no weights changed"
