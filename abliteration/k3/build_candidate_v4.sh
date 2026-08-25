#!/usr/bin/env bash
# Locked K3 v4 experiment: union the complete v3 activation span with one
# independently recovered public K3 ablation direction.  This wrapper verifies
# every prepared input before delegating to the generic, non-deploying builder.
set -euo pipefail
umask 077

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
SOURCE_DIRECTION=/models/.abliteration/k3/run/k3-refusal-direction.gguf
SOURCE_Q5_DIRECTION=/models/.abliteration/k3/run/k3-refusal-direction-q5-reference.gguf
SOURCE_VALIDATION_DIRECTION=/models/.abliteration/k3/run/k3-refusal-direction-validation.gguf
V4_DONOR_DIR=/models/.abliteration/k3/v4-donor
V4_DIRECTIONS_DIR=/models/.abliteration/k3/v4-directions
V4_HOLDOUT_DIR=/models/.abliteration/k3/v4-holdout
DIRECTION=$V4_DIRECTIONS_DIR/train.gguf
DIAGNOSTIC_DIRECTION=$V4_DIRECTIONS_DIR/q5.gguf
VALIDATION_DIRECTION=$V4_DIRECTIONS_DIR/validation.gguf
IK_DIR=/home/chuck/ik_llama.cpp-v3r2
BUILD_DIR=$IK_DIR/build-abliteration

check_hash() {
    local expected=$1 path=$2 actual
    [ -r "$path" ] || { echo "build_candidate_v4: missing locked artifact: $path" >&2; exit 1; }
    actual=$(sha256sum "$path" | awk '{print $1}')
    [ "$actual" = "$expected" ] || {
        echo "build_candidate_v4: hash mismatch for $path: $actual != $expected" >&2
        exit 1
    }
}

check_hash 7ce9aee3339ee267fa3de8017bba933168467a3d5a59f6d4c7da080b0b0588ad "$SOURCE_DIRECTION"
check_hash 57c79d69b275dab8f801d6e08694c5a0471d99f3de52fa18c9f06ed5e9fffdce "$SOURCE_Q5_DIRECTION"
check_hash 7e94d9256a9f55779252e9c2fd8cab67cafeeee78be1623f8a5c8849e6592246 "$SOURCE_VALIDATION_DIRECTION"
check_hash 84d7fd6ac161bb1654e926b9352de0375df62ccbec73f3db27cbec2d1e82a8d9 "$V4_DONOR_DIR/donor-direction.npy"
check_hash 44ad63ccc1f5fc73cb92841eb277b3cd849644aa1918872d53ec29ee1fe6cdf0 "$V4_DONOR_DIR/layer56-direction.npy"
check_hash 97258060dfe950d6f7085919ee8a5a4fd01b9359766945e54ee3ff2a2b7c76ee "$V4_DONOR_DIR/layer70-direction.npy"
check_hash af03e2152deb9f05897159acb893860a1cefb82a4929f515902691ea07204eaa "$V4_DONOR_DIR/manifest.json"
check_hash 1f4767980b4ca9eb4b9835120e848b9a97a0d3e85c114ecc33b250966093ccc3 "$DIRECTION"
check_hash e54bf9dc3f7740e633be551c61996185577ca36ca0a95d239abe9d9ceb6d638b "$V4_DIRECTIONS_DIR/train.manifest.json"
check_hash a6332dd2c52b1e92771ec3ca7b6cb1314d7bef600d5ac58aab0b1fd3b25af234 "$DIAGNOSTIC_DIRECTION"
check_hash 527fa1c807f1cf4cf8b86c3ec7ad9e5cd0b8e096baed2dc25380dbe1eba5d9a2 "$V4_DIRECTIONS_DIR/q5.manifest.json"
check_hash f2f563af8c32949d57917ae4e00327a74d77a8eccdaba1b4e9fc2a906f22c9c5 "$VALIDATION_DIRECTION"
check_hash 317c6814462a1ad86f253fbe47d90e963852f49afc91aa9cbae3085199f2cdea "$V4_DIRECTIONS_DIR/validation.manifest.json"
check_hash 0b224a7a3cc31656125c2d10627b0a206efad57c890603953fe35d9ef04d7ec2 "$V4_DIRECTIONS_DIR/verification.json"

[ "$(git -C "$IK_DIR" rev-parse HEAD)" = edce2ac567a78ddd80ba565fd2f39717c8091bd0 ] \
    || { echo "build_candidate_v4: engine commit changed" >&2; exit 1; }
check_hash ba946efae1637ea0cc82ac591763cd05e274d18f13b2c568795942ad21118c02 \
    "$BUILD_DIR/bin/llama-quantize"

"$SCRIPT_DIR/verify_v4_holdout.py" "$V4_HOLDOUT_DIR"
"$SCRIPT_DIR/verify_v4_directions.py" \
    "$V4_DONOR_DIR/manifest.json" "$V4_DONOR_DIR/donor-direction.npy" \
    "$SOURCE_DIRECTION" "$SOURCE_Q5_DIRECTION" "$SOURCE_VALIDATION_DIRECTION" \
    "$DIRECTION" "$DIAGNOSTIC_DIRECTION" "$VALIDATION_DIRECTION"

export DIRECTION DIAGNOSTIC_DIRECTION VALIDATION_DIRECTION
export V4_DONOR_DIR V4_DIRECTIONS_DIR V4_HOLDOUT_DIR
export IK_DIR BUILD_DIR
export REUSE_DIRECTION=1
export DIRECTION_EXPECTED_LAYERS=19
export LAYER_START=1
export LAYER_END=19
export SUBSPACE_RANK=19
export PATCH_EXISTING=1
export LOCKED_PROTOCOL_VERSION=v4
export QUANT_PASSES=64
export QUANT_CORRECTION=0.0625
export MAX_RESIDUAL=0.019
export OUTPUT_DIR=/models/Kimi-K3-Q5attn-Abliterated-V4
export OUTPUT_PREFIX=Kimi-K3-Q5attn-Abliterated-V4
export ARTIFACT_DIR=/models/.abliteration/k3/v4

exec "$SCRIPT_DIR/build_candidate.sh"
