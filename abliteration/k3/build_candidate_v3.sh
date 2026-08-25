#!/usr/bin/env bash
# Locked full-band rank-18 K3 v3 experiment. V2 reduced keyword refusals to
# zero in its first ten canonical rows but failed the stricter substantive gate
# through target substitution. The construction-only r2 amendment is recorded
# in V3_PROTOCOL.md before any v3 model load or behavioral response.
set -euo pipefail
umask 077

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
DIRECTION=/models/.abliteration/k3/run/k3-refusal-direction.gguf
DIAGNOSTIC_DIRECTION=/models/.abliteration/k3/run/k3-refusal-direction-q5-reference.gguf
VALIDATION_DIRECTION=/models/.abliteration/k3/run/k3-refusal-direction-validation.gguf
V3_HOLDOUT_DIR=/models/.abliteration/k3/v3-holdout

check_hash() {
    local expected=$1 path=$2 actual
    [ -r "$path" ] || { echo "build_candidate_v3: missing locked artifact: $path" >&2; exit 1; }
    actual=$(sha256sum "$path" | awk '{print $1}')
    [ "$actual" = "$expected" ] || {
        echo "build_candidate_v3: hash mismatch for $path: $actual != $expected" >&2
        exit 1
    }
}

check_hash 7ce9aee3339ee267fa3de8017bba933168467a3d5a59f6d4c7da080b0b0588ad "$DIRECTION"
check_hash 57c79d69b275dab8f801d6e08694c5a0471d99f3de52fa18c9f06ed5e9fffdce "$DIAGNOSTIC_DIRECTION"
check_hash 7e94d9256a9f55779252e9c2fd8cab67cafeeee78be1623f8a5c8849e6592246 "$VALIDATION_DIRECTION"
"$SCRIPT_DIR/verify_v3_holdout.py" "$V3_HOLDOUT_DIR"

export DIRECTION DIAGNOSTIC_DIRECTION VALIDATION_DIRECTION V3_HOLDOUT_DIR
export IK_DIR=/home/chuck/ik_llama.cpp-v3r2
export BUILD_DIR=/home/chuck/ik_llama.cpp-v3r2/build-abliteration
export REUSE_DIRECTION=1
export SUBSPACE_RANK=18
export PATCH_EXISTING=1
export LOCKED_PROTOCOL_VERSION=v3
export QUANT_PASSES=64
export QUANT_CORRECTION=0.0625
export MAX_RESIDUAL=0.019
export OUTPUT_DIR=/models/Kimi-K3-Q5attn-Abliterated-V3-R2
export OUTPUT_PREFIX=Kimi-K3-Q5attn-Abliterated-V3-R2
export ARTIFACT_DIR=/models/.abliteration/k3/v3-r2

exec "$SCRIPT_DIR/build_candidate.sh"
