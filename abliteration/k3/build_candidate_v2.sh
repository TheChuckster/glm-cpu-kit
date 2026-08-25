#!/usr/bin/env bash
# Locked rank-10 K3 v2 experiment. The underlying builder remains reusable;
# this wrapper fixes every intervention choice and verifies the accepted v1
# direction artifacts before any model payload can be opened for writing.
set -euo pipefail
umask 077

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
DIRECTION=/models/.abliteration/k3/run/k3-refusal-direction.gguf
DIAGNOSTIC_DIRECTION=/models/.abliteration/k3/run/k3-refusal-direction-q5-reference.gguf
VALIDATION_DIRECTION=/models/.abliteration/k3/run/k3-refusal-direction-validation.gguf

check_hash() {
    local expected=$1 path=$2 actual
    [ -r "$path" ] || { echo "build_candidate_v2: missing locked artifact: $path" >&2; exit 1; }
    actual=$(sha256sum "$path" | awk '{print $1}')
    [ "$actual" = "$expected" ] || {
        echo "build_candidate_v2: hash mismatch for $path: $actual != $expected" >&2
        exit 1
    }
}

check_hash 7ce9aee3339ee267fa3de8017bba933168467a3d5a59f6d4c7da080b0b0588ad "$DIRECTION"
check_hash 57c79d69b275dab8f801d6e08694c5a0471d99f3de52fa18c9f06ed5e9fffdce "$DIAGNOSTIC_DIRECTION"
check_hash 7e94d9256a9f55779252e9c2fd8cab67cafeeee78be1623f8a5c8849e6592246 "$VALIDATION_DIRECTION"

export DIRECTION DIAGNOSTIC_DIRECTION VALIDATION_DIRECTION
export IK_DIR=/home/chuck/ik_llama.cpp-v2
export BUILD_DIR=/home/chuck/ik_llama.cpp-v2/build-abliteration
export REUSE_DIRECTION=1
export SUBSPACE_RANK=10
export PATCH_EXISTING=1
export OUTPUT_DIR=/models/Kimi-K3-Q5attn-Abliterated-V2
export OUTPUT_PREFIX=Kimi-K3-Q5attn-Abliterated-V2
export ARTIFACT_DIR=/models/.abliteration/k3/v2

exec "$SCRIPT_DIR/build_candidate.sh"
