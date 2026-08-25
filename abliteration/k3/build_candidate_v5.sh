#!/usr/bin/env bash
# Locked K3 v5-r2 spectral candidate. Every derived input is hash-bound before
# delegating to the generic non-deploying reflink builder.
set -euo pipefail
umask 077

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
IK_DIR=/home/chuck/ik_llama.cpp-v5
BUILD_DIR=$IK_DIR/build-abliteration
PYTHON=/models/.abliteration/k3/v5-env/bin/python
SOURCE_CAPTURE=/models/.abliteration/k3/v5-capture/source-activations.gguf
DIAGNOSTIC=/models/.abliteration/k3/v5-spectral-diagnostic1.json
V5_CAPTURE_DIR=/models/.abliteration/k3/v5-spectral-capture
V5_DIRECTIONS_DIR=/models/.abliteration/k3/v5-spectral-directions
DIRECTION=$V5_DIRECTIONS_DIR/source.gguf
DIAGNOSTIC_DIRECTION=$V5_DIRECTIONS_DIR/q5.gguf
VALIDATION_DIRECTION=$DIRECTION
V2_HOLDOUT_DIR=/models/.abliteration/k3/v2-holdout
V3_HOLDOUT_DIR=/models/.abliteration/k3/v3-holdout
V4_HOLDOUT_DIR=/models/.abliteration/k3/v4-holdout

check_hash() {
    local expected=$1 path=$2 actual
    [ -r "$path" ] || { echo "build_candidate_v5: missing locked artifact: $path" >&2; exit 1; }
    actual=$(sha256sum "$path" | awk '{print $1}')
    [ "$actual" = "$expected" ] || {
        echo "build_candidate_v5: hash mismatch for $path: $actual != $expected" >&2
        exit 1
    }
}

[ "$(git -C "$IK_DIR" rev-parse HEAD)" = dd0bf0177f78657960364493d0220350a82548fb ] \
    || { echo "build_candidate_v5: engine commit changed" >&2; exit 1; }
check_hash 47e921423d579806ce455aeedd366d8c471cb73eb5826540d1116471ba7a04b5 \
    "$BUILD_DIR/bin/llama-cvector-generator"
check_hash 02ba5e46dc67d4bcb5b154638c29cad7540347c17ef34713e23da56d498f589d \
    "$BUILD_DIR/bin/llama-quantize"
check_hash 9a47478af8370ffe539c14de61f442451cd3240579c902d1e227df0eabd0559f \
    "$SOURCE_CAPTURE"
check_hash 267d841e23036a5db48293d73e2627d444342d14cbc5fef36be489e6937545e2 \
    "$DIAGNOSTIC"
check_hash 88e7f3432d0c23356868397d42f7f706db6f15ba97b3011df47d492bbf2002e2 \
    "$V5_CAPTURE_DIR/engine-and-method.sha256"
check_hash e097ffb5be47316aee396d5b4777fbb232ce648b8e78d9736eac90e9ac9b92d3 \
    "$V5_CAPTURE_DIR/python.freeze"
check_hash bea26596b2f01e4cf964015c4d95c52a1f11f876093b6e5a05bbf4e85fa27051 \
    "$V5_CAPTURE_DIR/q5-activations.gguf"
check_hash 1f4e6d9e2f036200348df8801c687a524f662f5b9273381c0d382700b6e6c72f \
    "$V5_CAPTURE_DIR/q5-mean.gguf"
check_hash d7e74409092951ad41572bcd007af9daba159bfd8496fe4e7162c8aad86247c7 \
    "$V5_CAPTURE_DIR/q5.log"
check_hash fb0bf4e7d1dcb93c9df74f9ffc7319995c7123299352b02e1f1e603e2dfd805d \
    "$V5_CAPTURE_DIR/all-artifacts.sha256"
check_hash 1c9a6fd36433154e3392e1f6168b78fd7983158543b755bc987ed739d5e1ced5 \
    "$DIRECTION"
check_hash 214e641e951fe529ba3c1e9f102d08d1db21ef4edf6da7539be697c84358919c \
    "$V5_DIRECTIONS_DIR/source.manifest.json"
check_hash 3efcac932b42538b862e1b6b4e454f6ee7930737c7fb6cb794c0ab5d7869c7c9 \
    "$DIAGNOSTIC_DIRECTION"
check_hash ce50085977c539c296cb5695df4cc4e6a65f07b8769be69973450d627973dab8 \
    "$V5_DIRECTIONS_DIR/q5.manifest.json"
check_hash 0ada1e63a2ec3c771358891f0e378f53f0c44b6aeed0d56a67127374d7c9dbd0 \
    "$V5_DIRECTIONS_DIR/verification.json"

sha256sum -c "$V5_CAPTURE_DIR/engine-and-method.sha256" >/dev/null
sha256sum -c "$V5_CAPTURE_DIR/all-artifacts.sha256" >/dev/null
"$SCRIPT_DIR/verify_v5_prompts.py" /models/.abliteration/k3/v5-prompts
PYTHONPATH=$SCRIPT_DIR "$PYTHON" "$SCRIPT_DIR/verify_v5_spectral_directions.py" \
    "$V5_DIRECTIONS_DIR" "$SOURCE_CAPTURE" \
    "$V5_CAPTURE_DIR/q5-activations.gguf" "$DIAGNOSTIC"
"$SCRIPT_DIR/verify_v2_holdout.py" "$V2_HOLDOUT_DIR"
"$SCRIPT_DIR/verify_v3_holdout.py" "$V3_HOLDOUT_DIR"
"$SCRIPT_DIR/verify_v4_holdout.py" "$V4_HOLDOUT_DIR"

export DIRECTION DIAGNOSTIC_DIRECTION VALIDATION_DIRECTION
export IK_DIR BUILD_DIR V2_HOLDOUT_DIR V3_HOLDOUT_DIR V4_HOLDOUT_DIR
export V5_CAPTURE_DIR V5_DIRECTIONS_DIR
export V5_DIAGNOSTIC=$DIAGNOSTIC
export V5_METHOD_VARIANT=spectral
export REUSE_DIRECTION=1
export DIRECTION_GEOMETRY=spectral-basis
export DIRECTION_EXPECTED_LAYERS=7
export LAYER_START=1
export LAYER_END=7
export SUBSPACE_RANK=7
export PATCH_EXISTING=1
export LOCKED_PROTOCOL_VERSION=v5
export QUANT_PASSES=64
export QUANT_CORRECTION=0.0625
export MAX_RESIDUAL=0.019
export OUTPUT_DIR=/models/Kimi-K3-Q5attn-Abliterated-V5-R2
export OUTPUT_PREFIX=Kimi-K3-Q5attn-Abliterated-V5-R2
export ARTIFACT_DIR=/models/.abliteration/k3/v5-r2

exec "$SCRIPT_DIR/build_candidate.sh"
