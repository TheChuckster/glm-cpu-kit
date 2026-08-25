#!/usr/bin/env bash
# Reuse the rejected run's immutable source activations and capture only Q5 at
# the geometry-locked v5-r2 layer. This never changes model weights.
set -euo pipefail
umask 077

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
IK_DIR=${IK_DIR:-/home/chuck/ik_llama.cpp-v5}
BUILD_DIR=${BUILD_DIR:-$IK_DIR/build-abliteration}
PYTHON=${PYTHON:-/models/.abliteration/k3/v5-env/bin/python}
WHEELHOUSE=${WHEELHOUSE:-/models/.abliteration/k3/v5-wheelhouse}
PROMPTS_DIR=${PROMPTS_DIR:-/models/.abliteration/k3/v5-prompts}
SOURCE_CAPTURE=${SOURCE_CAPTURE:-/models/.abliteration/k3/v5-capture/source-activations.gguf}
DIAGNOSTIC=${DIAGNOSTIC:-/models/.abliteration/k3/v5-spectral-diagnostic1.json}
CAPTURE_DIR=${CAPTURE_DIR:-/models/.abliteration/k3/v5-spectral-capture}
DIRECTION_DIR=${DIRECTION_DIR:-/models/.abliteration/k3/v5-spectral-directions}
Q5_MODEL=${Q5_MODEL:-/models/Kimi-K3-Q5attn/Kimi-K3-Q5attn-00001-of-00019.gguf}
THREADS=${THREADS:-64}
CVECTOR=$BUILD_DIR/bin/llama-cvector-generator
GGUF_PY=$IK_DIR/gguf-py
ENGINE_COMMIT=dd0bf0177f78657960364493d0220350a82548fb
CVECTOR_SHA256=47e921423d579806ce455aeedd366d8c471cb73eb5826540d1116471ba7a04b5
SOURCE_CAPTURE_SHA256=9a47478af8370ffe539c14de61f442451cd3240579c902d1e227df0eabd0559f
DIAGNOSTIC_SHA256=267d841e23036a5db48293d73e2627d444342d14cbc5fef36be489e6937545e2
NUMPY_ARCHIVE=numpy-2.2.4-cp312-cp312-manylinux_2_17_x86_64.manylinux2014_x86_64.whl
NUMPY_ARCHIVE_SHA256=4f92084defa704deadd4e0a5ab1dc52d8ac9e8a8ef617f3fbb853e79b0ea3592
MINISOM_ARCHIVE=minisom-2.3.5.tar.gz
MINISOM_ARCHIVE_SHA256=c4e65e0a6a50170c163e9c0408f77464871e7b3007ad0cd87e178cdaf3db2ce3
MINISOM_WHEEL=minisom-2.3.5-py3-none-any.whl
MINISOM_WHEEL_SHA256=0b8e4e414e3ceabd97f221e0d90f9bc0b3996e3b7eee4aa728196862d6f457f3
PYYAML_WHEEL=PyYAML-6.0.2-cp312-cp312-manylinux_2_17_x86_64.manylinux2014_x86_64.whl
PYYAML_WHEEL_SHA256=80bab7bfc629882493af4aa31a4cfa43a4c57c83813253626916b8c7ada83476
TQDM_WHEEL=tqdm-4.67.1-py3-none-any.whl
TQDM_WHEEL_SHA256=26445eca388f82e72884e0d580d5464cd801a3ea01e63e5601bdff9ba6a48de2

die() { echo "capture_v5_spectral_directions: $*" >&2; exit 1; }
file_hash() { sha256sum "$1" | awk '{print $1}'; }

pgrep -f '/llama-(server|perplexity|cvector-generator|quantize)([[:space:]]|$)' \
    >/dev/null 2>&1 && die "another llama model workload is running"
[ "$(git -C "$IK_DIR" rev-parse HEAD)" = "$ENGINE_COMMIT" ] \
    || die "engine commit changed"
[ -x "$CVECTOR" ] || die "missing cvector generator"
[ "$(file_hash "$CVECTOR")" = "$CVECTOR_SHA256" ] || die "cvector hash changed"
[ -x "$PYTHON" ] || die "missing locked v5 Python"
[ "$(file_hash "$WHEELHOUSE/$NUMPY_ARCHIVE")" = "$NUMPY_ARCHIVE_SHA256" ] \
    || die "NumPy archive hash changed"
[ "$(file_hash "$WHEELHOUSE/$MINISOM_ARCHIVE")" = "$MINISOM_ARCHIVE_SHA256" ] \
    || die "MiniSom archive hash changed"
[ "$(file_hash "$WHEELHOUSE/$MINISOM_WHEEL")" = "$MINISOM_WHEEL_SHA256" ] \
    || die "MiniSom wheel hash changed"
[ "$(file_hash "$WHEELHOUSE/$PYYAML_WHEEL")" = "$PYYAML_WHEEL_SHA256" ] \
    || die "PyYAML wheel hash changed"
[ "$(file_hash "$WHEELHOUSE/$TQDM_WHEEL")" = "$TQDM_WHEEL_SHA256" ] \
    || die "tqdm wheel hash changed"
"$PYTHON" -c \
    'import importlib.metadata,numpy; assert numpy.__version__ == "2.2.4"; assert importlib.metadata.version("MiniSom") == "2.3.5"; assert importlib.metadata.version("PyYAML") == "6.0.2"; assert importlib.metadata.version("tqdm") == "4.67.1"' \
    || die "locked Python dependencies changed"
[ "$(file_hash "$SOURCE_CAPTURE")" = "$SOURCE_CAPTURE_SHA256" ] \
    || die "rejected-run source capture hash changed"
[ "$(file_hash "$DIAGNOSTIC")" = "$DIAGNOSTIC_SHA256" ] \
    || die "spectral diagnostic hash changed"
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
    "$WHEELHOUSE/$NUMPY_ARCHIVE" "$WHEELHOUSE/$MINISOM_ARCHIVE" \
    "$WHEELHOUSE/$MINISOM_WHEEL" "$WHEELHOUSE/$PYYAML_WHEEL" \
    "$WHEELHOUSE/$TQDM_WHEEL" "$SOURCE_CAPTURE" "$DIAGNOSTIC" \
    "$SCRIPT_DIR/capture_v5_spectral_directions.sh" \
    "$SCRIPT_DIR/diagnose_v5_spectral.py" \
    "$SCRIPT_DIR/generate_v5_spectral_directions.py" \
    "$SCRIPT_DIR/verify_v5_spectral_directions.py" \
    "$SCRIPT_DIR/verify_v5_prompts.py" "$SCRIPT_DIR/v5-requirements.txt" \
    > "$CAPTURE_DIR/engine-and-method.sha256"
"$PYTHON" -m pip freeze --all > "$CAPTURE_DIR/python.freeze"

PYTHONPATH=$SCRIPT_DIR "$PYTHON" "$SCRIPT_DIR/generate_v5_spectral_directions.py" source \
    "$SOURCE_CAPTURE" "$DIRECTION_DIR" --diagnostic "$DIAGNOSTIC" --gguf-py "$GGUF_PY"

"$CVECTOR" --model "$Q5_MODEL" \
    --method mean-last --apply-chat-template --jinja \
    --reasoning-format deepseek \
    --chat-template-kwargs '{"thinking_effort":"low"}' \
    --ctx-size 2048 --batch-size 2048 --ubatch-size 2048 \
    --threads "$THREADS" --threads-batch "$THREADS" -fa on \
    --positive-file "$PROMPTS_DIR/train.harmful.txt" \
    --negative-file "$PROMPTS_DIR/train.harmless.txt" \
    --output "$CAPTURE_DIR/q5-mean.gguf" \
    --activations-output "$CAPTURE_DIR/q5-activations.gguf" \
    --activations-layers 61 \
    2>&1 | tee "$CAPTURE_DIR/q5.log"

PYTHONPATH=$SCRIPT_DIR "$PYTHON" "$SCRIPT_DIR/generate_v5_spectral_directions.py" q5 \
    "$CAPTURE_DIR/q5-activations.gguf" "$DIRECTION_DIR" \
    --diagnostic "$DIAGNOSTIC" --gguf-py "$GGUF_PY"
PYTHONPATH=$SCRIPT_DIR "$PYTHON" "$SCRIPT_DIR/verify_v5_spectral_directions.py" \
    "$DIRECTION_DIR" "$SOURCE_CAPTURE" "$CAPTURE_DIR/q5-activations.gguf" \
    "$DIAGNOSTIC" --json "$DIRECTION_DIR/verification.json"

sha256sum "$CAPTURE_DIR"/* "$DIRECTION_DIR"/* \
    > "$CAPTURE_DIR/all-artifacts.sha256"
echo "v5-r2 spectral source/Q5 directions complete; no weights changed"
