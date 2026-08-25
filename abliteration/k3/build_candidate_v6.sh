#!/usr/bin/env bash
# Locked rank-10 K3 v6 counterfactual-reflection candidate. This wrapper pins
# the tested engine/runtime closure and all construction methodology before the
# generic non-deploying reflink builder can open an output payload for writing.
set -euo pipefail
umask 077

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
IK_DIR=/home/chuck/ik_llama.cpp-v6
BUILD_DIR=$IK_DIR/build-reflection
DIRECTION=/models/.abliteration/k3/run/k3-refusal-direction.gguf
DIAGNOSTIC_DIRECTION=/models/.abliteration/k3/run/k3-refusal-direction-q5-reference.gguf
VALIDATION_DIRECTION=/models/.abliteration/k3/run/k3-refusal-direction-validation.gguf
V2_HOLDOUT_DIR=/models/.abliteration/k3/v2-holdout
V3_HOLDOUT_DIR=/models/.abliteration/k3/v3-holdout
V4_HOLDOUT_DIR=/models/.abliteration/k3/v4-holdout
ENGINE_EVIDENCE=/models/.abliteration/k3/v6-engine-tests

die() { echo "build_candidate_v6: $*" >&2; exit 1; }

check_hash() {
    local expected=$1 path=$2 actual
    [ -r "$path" ] || die "missing locked artifact: $path"
    actual=$(sha256sum "$path" | awk '{print $1}')
    [ "$actual" = "$expected" ] \
        || die "hash mismatch for $path: $actual != $expected"
}

[ "$(git -C "$IK_DIR" rev-parse HEAD)" = \
    9e5ed956741223ca7603903e646b8301a73224ce ] \
    || die "engine commit changed"
git -C "$IK_DIR" diff --quiet -- || die "engine worktree has unstaged source changes"
git -C "$IK_DIR" diff --cached --quiet -- \
    || die "engine worktree has staged source changes"

# Tested remote engine and runtime closure.
check_hash f2e7874bb8242c14b0a32ad916a9d0940867099ab33fe4331fd2ebc5b6792b17 \
    "$BUILD_DIR/bin/llama-quantize"
check_hash f5543d582266dfdf5dfadb3e9a7491f62be4f8f1944e62534e8617a5b698bf75 \
    "$BUILD_DIR/bin/llama-cvector-generator"
check_hash 1d56d7390f32f8f42b2e6cf5c6b0404856400bc2658ace4fd8e64fd9cf15393c \
    "$BUILD_DIR/src/libllama.so"
check_hash 034116ef0a154754d426bc4e1f90b6d9f8e1d64f8b8e1c47ed19d9a6c06523eb \
    "$BUILD_DIR/ggml/src/libggml.so"
check_hash ef8bbb5cc0f4e76becfe20e7b8d645cef77750106c070ad4245ea8cc3fad5178 \
    "$BUILD_DIR/bin/test-direction-projection"

# Baseline closure and independently decoded remote regression evidence.
check_hash 02ba5e46dc67d4bcb5b154638c29cad7540347c17ef34713e23da56d498f589d \
    /home/chuck/ik_llama.cpp-v5/build-abliteration/bin/llama-quantize
check_hash 02eb90039909f1b8cf0caf887bb457601ab2dff4824280f287720d3195cf1eff \
    /home/chuck/ik_llama.cpp-v5/build-abliteration/src/libllama.so
check_hash ed9d2caa94bed72fc678d24c5de510ffa31387703940bf8bebba8305aade974d \
    /home/chuck/ik_llama.cpp-v5/build-abliteration/ggml/src/libggml.so
check_hash b428961c85929e6e7c968919c40ed7ecba649c7a78d4d7e409c0e7b5456359da \
    "$ENGINE_EVIDENCE/stories260K-one-q8.gguf"
check_hash 3723d3a18be4af06b2ea9c1494ebc98762a28cb1a7a1493db7ab0de8292a9506 \
    "$ENGINE_EVIDENCE/remote-end-to-end.log"
check_hash 8b215a58ab0942f2d4e9023ee923b2033f47682769bbb1fc3972c8045afc3576 \
    "$ENGINE_EVIDENCE/remote-direction-ctest.log"

# Exact v2 basis input and independent geometry cross-checks.
check_hash 7ce9aee3339ee267fa3de8017bba933168467a3d5a59f6d4c7da080b0b0588ad \
    "$DIRECTION"
check_hash 57c79d69b275dab8f801d6e08694c5a0471d99f3de52fa18c9f06ed5e9fffdce \
    "$DIAGNOSTIC_DIRECTION"
check_hash 7e94d9256a9f55779252e9c2fd8cab67cafeeee78be1623f8a5c8849e6592246 \
    "$VALIDATION_DIRECTION"

# Locked construction/verifier implementation. The generic builder separately
# records this closure plus the wrapper, protocol, engine, and holdouts in its
# output-side provenance manifest.
check_hash 5feded021cd08327de92670a1320f38af89edcb559ccdb2f764201894133966d \
    "$SCRIPT_DIR/build_candidate.sh"
check_hash 94c534ddf6fb6ad54b67bc6e966d0804a0fdfe94c525f82b63fba09284471261 \
    "$SCRIPT_DIR/verify_model.py"
check_hash 73a94dbbbccfda15a1837240143701b8d513116867f36f30736ebd73cbe95a8b \
    "$SCRIPT_DIR/analyze_direction.py"
check_hash 6255a55af81c9620cf938eb4ac4c43f74273a1f99f1b3098cd3025e25847ca3f \
    "$SCRIPT_DIR/compare_directions.py"
check_hash 613776297ff4bc2b4c4a8bb8c562816f9ddb608c654395a7ddd273c2fb132f77 \
    "$SCRIPT_DIR/compare_subspaces.py"
check_hash c2c89fd979da8b307accce07e315feb4aac3d2a005b8723b02e32db45e363c34 \
    "$SCRIPT_DIR/prepare_prompts.py"
check_hash f3177175104b25dcdea744b9125fc6076189858cd584f0d710984da149910c05 \
    "$SCRIPT_DIR/prepare_validation_prompts.py"
check_hash 8de3d5ea13d60ed563449dc4d281b7f45c8a2e1a9423485c7381413bdba51925 \
    "$SCRIPT_DIR/verify_prompts.py"
check_hash d749f445a325a194df54be01c3bc7095979665a4d7f700ff9613fc60494499a1 \
    "$SCRIPT_DIR/verify_v2_holdout.py"
check_hash 6f3b3b304dd51b3a632198a8de0dde0435cab12b3c3b6e40856217ad5a865539 \
    "$SCRIPT_DIR/verify_v3_holdout.py"
check_hash 9e04c6c0306bd3daa43e81805954ef1e69440eeb1ef84a3613cf7f575f4afe94 \
    "$SCRIPT_DIR/verify_v4_holdout.py"

"$SCRIPT_DIR/verify_v2_holdout.py" "$V2_HOLDOUT_DIR"
"$SCRIPT_DIR/verify_v3_holdout.py" "$V3_HOLDOUT_DIR"
"$SCRIPT_DIR/verify_v4_holdout.py" "$V4_HOLDOUT_DIR"

export DIRECTION DIAGNOSTIC_DIRECTION VALIDATION_DIRECTION
export IK_DIR BUILD_DIR V2_HOLDOUT_DIR V3_HOLDOUT_DIR V4_HOLDOUT_DIR
export REUSE_DIRECTION=1
export SUBSPACE_RANK=10
export PATCH_EXISTING=1
export LOCKED_PROTOCOL_VERSION=v6
export ORTHOGONALIZE_SCALE=2.0
export QUANT_PASSES=64
export QUANT_CORRECTION=0.0625
export MAX_RESIDUAL=0.019
export OUTPUT_DIR=/models/Kimi-K3-Q5attn-Abliterated-V6
export OUTPUT_PREFIX=Kimi-K3-Q5attn-Abliterated-V6
export ARTIFACT_DIR=/models/.abliteration/k3/v6

exec "$SCRIPT_DIR/build_candidate.sh"
