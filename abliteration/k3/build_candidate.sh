#!/usr/bin/env bash
# Build a separate K3 Q5-attention abliterated candidate. This never stops,
# replaces, or selects the live model; service downtime must be explicit.
set -euo pipefail
umask 077

if [ -d "$HOME/ik_llama.cpp-abliteration" ]; then
    DEFAULT_IK_DIR="$HOME/ik_llama.cpp-abliteration"
else
    DEFAULT_IK_DIR="$HOME/ik_llama.cpp"
fi
IK_DIR="${IK_DIR:-$DEFAULT_IK_DIR}"
BUILD_DIR="${BUILD_DIR:-$IK_DIR/build-abliteration}"
SOURCE_DIR="${SOURCE_DIR:-/models/Kimi-K3-UD-Q2_K_XL}"
SOURCE_PREFIX="${SOURCE_PREFIX:-Kimi-K3-UD-Q2_K_XL}"
REFERENCE_DIR="${REFERENCE_DIR:-/models/Kimi-K3-Q5attn}"
REFERENCE_PREFIX="${REFERENCE_PREFIX:-Kimi-K3-Q5attn}"
OUTPUT_DIR="${OUTPUT_DIR:-/models/Kimi-K3-Q5attn-Abliterated}"
OUTPUT_PREFIX="${OUTPUT_PREFIX:-Kimi-K3-Q5attn-Abliterated}"
PROMPTS_DIR="${PROMPTS_DIR:-/models/.abliteration/k3/prompts-canonical}"
ARTIFACT_DIR="${ARTIFACT_DIR:-/models/.abliteration/k3/run}"
THREADS="${THREADS:-64}"
LAYER_START="${LAYER_START:-56}"
LAYER_END="${LAYER_END:-73}"
MAX_RESIDUAL="${MAX_RESIDUAL:-0.02}"
QUANT_PASSES="${QUANT_PASSES:-16}"
QUANT_CORRECTION="${QUANT_CORRECTION:-0.25}"
ORTHOGONALIZE_SCALE="${ORTHOGONALIZE_SCALE:-1.0}"
REUSE_DIRECTION="${REUSE_DIRECTION:-0}"
SUBSPACE_RANK="${SUBSPACE_RANK:-0}"
PATCH_EXISTING="${PATCH_EXISTING:-0}"
LOCKED_PROTOCOL_VERSION="${LOCKED_PROTOCOL_VERSION:-v2}"
DIRECTION_EXPECTED_LAYERS="${DIRECTION_EXPECTED_LAYERS:-92}"
DIRECTION_GEOMETRY="${DIRECTION_GEOMETRY:-layer-band}"

CVECTOR="$BUILD_DIR/bin/llama-cvector-generator"
QUANTIZE="$BUILD_DIR/bin/llama-quantize"
SOURCE_MODEL="$SOURCE_DIR/$SOURCE_PREFIX-00001-of-00019.gguf"
REFERENCE_MODEL="$REFERENCE_DIR/$REFERENCE_PREFIX-00001-of-00019.gguf"
OUTPUT_MODEL="$OUTPUT_DIR/$OUTPUT_PREFIX.gguf"
DIRECTION="${DIRECTION:-$ARTIFACT_DIR/k3-refusal-direction.gguf}"
DIAGNOSTIC_DIRECTION="${DIAGNOSTIC_DIRECTION:-$ARTIFACT_DIR/k3-refusal-direction-q5-reference.gguf}"
VALIDATION_DIRECTION="${VALIDATION_DIRECTION:-$ARTIFACT_DIR/k3-refusal-direction-validation.gguf}"
VALIDATION_HARMFUL="$ARTIFACT_DIR/validation.harmful.txt"
VALIDATION_HARMLESS="$ARTIFACT_DIR/validation.harmless.txt"
QUANT_LOG="$ARTIFACT_DIR/quantize.log"
ENGINE_MANIFEST="$ARTIFACT_DIR/build-engine-and-tools.sha256"
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)

die() { echo "build_candidate: $*" >&2; exit 1; }

# This guard deliberately runs before every tool/model preflight. A missing or
# stale staging artifact must never mask an active production or offline model
# workload; two K3-sized jobs would contend for roughly a terabyte of RAM.
if pgrep -f '/llama-(server|perplexity|cvector-generator|quantize)([[:space:]]|$)' \
        >/dev/null 2>&1; then
    die "another llama model workload is running; stop it before candidate construction"
fi

[ "$SOURCE_DIR" != "$OUTPUT_DIR" ] || die "source and output directories must differ"
[ "$REFERENCE_DIR" != "$OUTPUT_DIR" ] || die "reference and output directories must differ"
[ "$REFERENCE_DIR" != "$SOURCE_DIR" ] || die "reference and source directories must differ"
[ -r "$SOURCE_MODEL" ] || die "source model missing: $SOURCE_MODEL"
[ -r "$REFERENCE_MODEL" ] || die "reference Q5-attention model is missing: $REFERENCE_MODEL"
[ -r "$PROMPTS_DIR/train.harmful.txt" ] || die "missing prepared harmful prompts"
[ -r "$PROMPTS_DIR/train.harmless.txt" ] || die "missing prepared harmless prompts"
[ -x "$CVECTOR" ] || die "missing patched cvector generator: $CVECTOR"
[ -x "$QUANTIZE" ] || die "missing patched quantizer: $QUANTIZE"
[ -x "$SCRIPT_DIR/analyze_direction.py" ] || die "missing direction analyzer"
[ -x "$SCRIPT_DIR/compare_directions.py" ] || die "missing direction comparator"
[ -x "$SCRIPT_DIR/compare_subspaces.py" ] || die "missing subspace comparator"
[ -x "$SCRIPT_DIR/prepare_prompts.py" ] || die "missing prompt helper"
[ -x "$SCRIPT_DIR/prepare_validation_prompts.py" ] || die "missing validation prompt materializer"
[ -x "$SCRIPT_DIR/verify_model.py" ] || die "missing model verifier"
[ -x "$SCRIPT_DIR/verify_prompts.py" ] || die "missing prompt verifier"
[[ "$THREADS" =~ ^[1-9][0-9]*$ ]] || die "THREADS must be a positive integer"
[[ "$QUANT_PASSES" =~ ^[1-9][0-9]*$ ]] && [ "$QUANT_PASSES" -le 64 ] \
    || die "QUANT_PASSES must be an integer in [1, 64]"
awk -v value="$QUANT_CORRECTION" \
    'BEGIN { exit !(value ~ /^[0-9]+([.][0-9]+)?$/ && value > 0 && value <= 1) }' \
    || die "QUANT_CORRECTION must be a decimal in (0, 1]"
awk -v value="$ORTHOGONALIZE_SCALE" \
    'BEGIN { exit !(value ~ /^[0-9]+([.][0-9]+)?$/ && value > 0 && value <= 2) }' \
    || die "ORTHOGONALIZE_SCALE must be a decimal in (0, 2]"
[[ "$REUSE_DIRECTION" == 0 || "$REUSE_DIRECTION" == 1 ]] \
    || die "REUSE_DIRECTION must be 0 or 1"
[[ "$SUBSPACE_RANK" =~ ^[0-9]+$ ]] || die "SUBSPACE_RANK must be a non-negative integer"
[[ "$DIRECTION_EXPECTED_LAYERS" =~ ^[1-9][0-9]*$ ]] \
    || die "DIRECTION_EXPECTED_LAYERS must be a positive integer"
case "$DIRECTION_GEOMETRY" in
    layer-band|spectral-basis) ;;
    *) die "DIRECTION_GEOMETRY must be layer-band or spectral-basis" ;;
esac
[[ "$PATCH_EXISTING" == 0 || "$PATCH_EXISTING" == 1 ]] \
    || die "PATCH_EXISTING must be 0 or 1"
if [ "$PATCH_EXISTING" = 1 ]; then
    [ "$SUBSPACE_RANK" -gt 0 ] \
        || die "PATCH_EXISTING is reserved for an explicit subspace candidate"
    command -v cp >/dev/null || die "cp is required for reflink construction"
fi
if [ "$SUBSPACE_RANK" -gt 0 ]; then
    case "$LOCKED_PROTOCOL_VERSION" in
        v2|v3|v4|v5|v6) ;;
        *) die "LOCKED_PROTOCOL_VERSION must be v2, v3, v4, v5, or v6 for a subspace candidate" ;;
    esac
fi
if [ "$DIRECTION_GEOMETRY" = spectral-basis ]; then
    [ "$SUBSPACE_RANK" -gt 0 ] \
        || die "spectral-basis geometry requires an explicit subspace rank"
    [ "$LOCKED_PROTOCOL_VERSION" = v5 ] \
        || die "spectral-basis geometry is currently locked to protocol v5"
fi

if [ -d "$OUTPUT_DIR" ] && find "$OUTPUT_DIR" -mindepth 1 -maxdepth 1 -print -quit | grep -q .; then
    die "refusing to overwrite non-empty candidate directory: $OUTPUT_DIR"
fi
"$SCRIPT_DIR/verify_prompts.py" "$PROMPTS_DIR"
mkdir -p "$OUTPUT_DIR" "$ARTIFACT_DIR"

# Pin the actual executable/runtime closure and methodology scripts. The build
# tools link libllama dynamically, so hashing only their front-end executables
# would not identify the engine that generated the direction and candidate.
mapfile -t BUILD_RUNTIME_PATHS < <(
    { ldd "$CVECTOR"; ldd "$QUANTIZE"; } |
        awk '{ for (i = 1; i <= NF; ++i) if ($i ~ /^\//) { print $i; break } }' |
        sort -u
)
[ "${#BUILD_RUNTIME_PATHS[@]}" -gt 0 ] || die "could not resolve build-tool runtime libraries"
BUILD_PROVENANCE_INPUTS=(
    "$CVECTOR" "$QUANTIZE" "${BUILD_RUNTIME_PATHS[@]}"
    "$SCRIPT_DIR/build_candidate.sh"
    "$SCRIPT_DIR/analyze_direction.py"
    "$SCRIPT_DIR/compare_directions.py"
    "$SCRIPT_DIR/prepare_prompts.py"
    "$SCRIPT_DIR/prepare_validation_prompts.py"
    "$SCRIPT_DIR/verify_model.py"
    "$SCRIPT_DIR/verify_prompts.py"
)
if [ "$SUBSPACE_RANK" -gt 0 ]; then
    BUILD_PROVENANCE_INPUTS+=("$SCRIPT_DIR/compare_subspaces.py")
    case "$LOCKED_PROTOCOL_VERSION" in
        v2)
            BUILD_PROVENANCE_INPUTS+=(
                "$SCRIPT_DIR/build_candidate_v2.sh"
                "$SCRIPT_DIR/V2_PROTOCOL.md"
                "$SCRIPT_DIR/prepare_v2_holdout.py"
                "$SCRIPT_DIR/verify_v2_holdout.py"
            )
            ;;
        v3)
            V3_HOLDOUT_DIR="${V3_HOLDOUT_DIR:-/models/.abliteration/k3/v3-holdout}"
            BUILD_PROVENANCE_INPUTS+=(
                "$SCRIPT_DIR/build_candidate_v3.sh"
                "$SCRIPT_DIR/V3_PROTOCOL.md"
                "$SCRIPT_DIR/prepare_v3_holdout.py"
                "$SCRIPT_DIR/verify_v3_holdout.py"
                "$V3_HOLDOUT_DIR/manifest.json"
                "$V3_HOLDOUT_DIR/test.strongreject.jsonl"
            )
            ;;
        v4)
            V4_HOLDOUT_DIR="${V4_HOLDOUT_DIR:-/models/.abliteration/k3/v4-holdout}"
            V4_DONOR_DIR="${V4_DONOR_DIR:-/models/.abliteration/k3/v4-donor}"
            V4_DIRECTIONS_DIR="${V4_DIRECTIONS_DIR:-/models/.abliteration/k3/v4-directions}"
            BUILD_PROVENANCE_INPUTS+=(
                "$SCRIPT_DIR/build_candidate_v4.sh"
                "$SCRIPT_DIR/V4_PROTOCOL.md"
                "$SCRIPT_DIR/recover_v4_donor.py"
                "$SCRIPT_DIR/compose_v4_direction.py"
                "$SCRIPT_DIR/verify_v4_directions.py"
                "$SCRIPT_DIR/prepare_v4_holdout.py"
                "$SCRIPT_DIR/verify_v4_holdout.py"
                "$V4_DONOR_DIR/manifest.json"
                "$V4_DONOR_DIR/donor-direction.npy"
                "$V4_DIRECTIONS_DIR/train.manifest.json"
                "$V4_DIRECTIONS_DIR/q5.manifest.json"
                "$V4_DIRECTIONS_DIR/validation.manifest.json"
                "$V4_HOLDOUT_DIR/manifest.json"
                "$V4_HOLDOUT_DIR/test.strongreject.jsonl"
            )
            ;;
        v5)
            V2_HOLDOUT_DIR="${V2_HOLDOUT_DIR:-/models/.abliteration/k3/v2-holdout}"
            V3_HOLDOUT_DIR="${V3_HOLDOUT_DIR:-/models/.abliteration/k3/v3-holdout}"
            V4_HOLDOUT_DIR="${V4_HOLDOUT_DIR:-/models/.abliteration/k3/v4-holdout}"
            V5_METHOD_VARIANT="${V5_METHOD_VARIANT:-som}"
            case "$V5_METHOD_VARIANT" in
                som)
                    V5_CAPTURE_DIR="${V5_CAPTURE_DIR:-/models/.abliteration/k3/v5-capture}"
                    V5_DIRECTIONS_DIR="${V5_DIRECTIONS_DIR:-/models/.abliteration/k3/v5-directions}"
                    BUILD_PROVENANCE_INPUTS+=(
                        "$SCRIPT_DIR/build_candidate_v5.sh"
                        "$SCRIPT_DIR/V5_PROTOCOL.md"
                        "$SCRIPT_DIR/capture_v5_directions.sh"
                        "$SCRIPT_DIR/generate_v5_directions.py"
                        "$SCRIPT_DIR/verify_v5_directions.py"
                        "$SCRIPT_DIR/prepare_v5_prompts.py"
                        "$SCRIPT_DIR/verify_v5_prompts.py"
                        "$SCRIPT_DIR/v5-requirements.txt"
                        "$V5_CAPTURE_DIR/engine-and-method.sha256"
                        "$V5_DIRECTIONS_DIR/train.manifest.json"
                        "$V5_DIRECTIONS_DIR/q5.manifest.json"
                        "$V5_DIRECTIONS_DIR/verification.json"
                    )
                    ;;
                spectral)
                    V5_CAPTURE_DIR="${V5_CAPTURE_DIR:-/models/.abliteration/k3/v5-spectral-capture}"
                    V5_DIRECTIONS_DIR="${V5_DIRECTIONS_DIR:-/models/.abliteration/k3/v5-spectral-directions}"
                    V5_DIAGNOSTIC="${V5_DIAGNOSTIC:-/models/.abliteration/k3/v5-spectral-diagnostic1.json}"
                    BUILD_PROVENANCE_INPUTS+=(
                        "$SCRIPT_DIR/build_candidate_v5.sh"
                        "$SCRIPT_DIR/V5_PROTOCOL.md"
                        "$SCRIPT_DIR/capture_v5_spectral_directions.sh"
                        "$SCRIPT_DIR/diagnose_v5_spectral.py"
                        "$SCRIPT_DIR/generate_v5_spectral_directions.py"
                        "$SCRIPT_DIR/verify_v5_spectral_directions.py"
                        "$SCRIPT_DIR/prepare_v5_prompts.py"
                        "$SCRIPT_DIR/verify_v5_prompts.py"
                        "$SCRIPT_DIR/v5-requirements.txt"
                        "$V5_DIAGNOSTIC"
                        "$V5_CAPTURE_DIR/engine-and-method.sha256"
                        "$V5_CAPTURE_DIR/all-artifacts.sha256"
                        "$V5_DIRECTIONS_DIR/source.manifest.json"
                        "$V5_DIRECTIONS_DIR/q5.manifest.json"
                        "$V5_DIRECTIONS_DIR/verification.json"
                    )
                    ;;
                *) die "V5_METHOD_VARIANT must be som or spectral" ;;
            esac
            BUILD_PROVENANCE_INPUTS+=(
                "$V2_HOLDOUT_DIR/manifest.json"
                "$V2_HOLDOUT_DIR/test.strongreject.jsonl"
                "$V3_HOLDOUT_DIR/manifest.json"
                "$V3_HOLDOUT_DIR/test.strongreject.jsonl"
                "$V4_HOLDOUT_DIR/manifest.json"
                "$V4_HOLDOUT_DIR/test.strongreject.jsonl"
            )
            ;;
        v6)
            V2_HOLDOUT_DIR="${V2_HOLDOUT_DIR:-/models/.abliteration/k3/v2-holdout}"
            V3_HOLDOUT_DIR="${V3_HOLDOUT_DIR:-/models/.abliteration/k3/v3-holdout}"
            V4_HOLDOUT_DIR="${V4_HOLDOUT_DIR:-/models/.abliteration/k3/v4-holdout}"
            BUILD_PROVENANCE_INPUTS+=(
                "$SCRIPT_DIR/build_candidate_v6.sh"
                "$SCRIPT_DIR/V6_PROTOCOL.md"
                "$SCRIPT_DIR/prepare_v2_holdout.py"
                "$SCRIPT_DIR/verify_v2_holdout.py"
                "$SCRIPT_DIR/prepare_v3_holdout.py"
                "$SCRIPT_DIR/verify_v3_holdout.py"
                "$SCRIPT_DIR/prepare_v4_holdout.py"
                "$SCRIPT_DIR/verify_v4_holdout.py"
                "$V2_HOLDOUT_DIR/manifest.json"
                "$V2_HOLDOUT_DIR/test.strongreject.jsonl"
                "$V3_HOLDOUT_DIR/manifest.json"
                "$V3_HOLDOUT_DIR/test.strongreject.jsonl"
                "$V4_HOLDOUT_DIR/manifest.json"
                "$V4_HOLDOUT_DIR/test.strongreject.jsonl"
            )
            ;;
    esac
fi
if [ -e "$ENGINE_MANIFEST" ]; then
    [ "$REUSE_DIRECTION" = 1 ] \
        || die "build provenance already exists; inspect it and set REUSE_DIRECTION=1"
    sha256sum -c "$ENGINE_MANIFEST" >/dev/null \
        || die "engine or methodology changed since the reusable direction was generated"
else
    sha256sum "${BUILD_PROVENANCE_INPUTS[@]}" > "$ENGINE_MANIFEST"
fi

mapfile -t SOURCE_SHARDS < <(find "$SOURCE_DIR" -maxdepth 1 -type f \
    -name "$SOURCE_PREFIX-*.gguf" -print | sort)
[ "${#SOURCE_SHARDS[@]}" -eq 19 ] \
    || die "expected 19 source shards, found ${#SOURCE_SHARDS[@]}"
mapfile -t REFERENCE_SHARDS < <(find "$REFERENCE_DIR" -maxdepth 1 -type f \
    -name "$REFERENCE_PREFIX-*.gguf" -print | sort)
[ "${#REFERENCE_SHARDS[@]}" -eq 19 ] \
    || die "expected 19 reference shards, found ${#REFERENCE_SHARDS[@]}"
INPUT_SHARDS=("${SOURCE_SHARDS[@]}" "${REFERENCE_SHARDS[@]}")
INPUT_STAT_BEFORE="$ARTIFACT_DIR/input-shards.before.stat"
INPUT_STAT_AFTER="$ARTIFACT_DIR/input-shards.after.stat"
stat -c $'%n\t%s\t%Y\t%Z' "${INPUT_SHARDS[@]}" > "$INPUT_STAT_BEFORE"
input_guard() {
    stat -c $'%n\t%s\t%Y\t%Z' "${INPUT_SHARDS[@]}" > "$INPUT_STAT_AFTER" || return 1
    cmp -s "$INPUT_STAT_BEFORE" "$INPUT_STAT_AFTER" || {
        echo "build_candidate: input shard size/mtime/ctime changed" >&2
        diff -u "$INPUT_STAT_BEFORE" "$INPUT_STAT_AFTER" >&2 || true
        return 1
    }
}
on_exit() {
    local status=$?
    trap - EXIT
    input_guard || status=1
    exit "$status"
}
trap on_exit EXIT

# A normal build streams the whole 788 GiB model. Patch-existing mode starts
# from an XFS copy-on-write clone and rewrites only the 10.6 GiB selected
# payloads, but retains ample headroom for temporary quantization buffers.
available=$(df -PB1 "$(dirname "$OUTPUT_DIR")" | awk 'NR == 2 {print $4}')
if [ "$PATCH_EXISTING" = 1 ]; then
    required=$((32 * 1024 * 1024 * 1024))
else
    required=$((850 * 1024 * 1024 * 1024))
fi
[ "${available:-0}" -ge "$required" ] \
    || die "need at least $((required / 1024 / 1024 / 1024)) GiB free; have $((available / 1024 / 1024 / 1024)) GiB"

"$SCRIPT_DIR/prepare_validation_prompts.py" \
    "$PROMPTS_DIR/validation.harmful.jsonl" "$PROMPTS_DIR/validation.harmless.jsonl" \
    "$VALIDATION_HARMFUL" "$VALIDATION_HARMLESS"

if { [ -s "$DIRECTION" ] || [ -s "$DIAGNOSTIC_DIRECTION" ] || [ -s "$VALIDATION_DIRECTION" ]; } \
        && [ "$REUSE_DIRECTION" != 1 ]; then
    die "a direction already exists; inspect all artifacts, then set REUSE_DIRECTION=1 explicitly"
fi

CVECTOR_ARGS=(
    --method mean-last --apply-chat-template --jinja
    --reasoning-format deepseek
    --chat-template-kwargs '{"thinking_effort":"low"}'
    --ctx-size 2048 --batch-size 2048 --ubatch-size 2048
    --threads "$THREADS" --threads-batch "$THREADS" -fa on
)
generate_direction() {
    local model=$1 positive=$2 negative=$3 output=$4 log=$5
    "$CVECTOR" --model "$model" --positive-file "$positive" --negative-file "$negative" \
        "${CVECTOR_ARGS[@]}" --output "$output" 2>&1 | tee "$log"
}

if [ -s "$DIRECTION" ]; then
    [ "$REUSE_DIRECTION" = 1 ] \
        || die "direction already exists; inspect it, then set REUSE_DIRECTION=1 explicitly"
    echo "reusing explicitly approved direction: $DIRECTION"
else
    generate_direction "$SOURCE_MODEL" \
        "$PROMPTS_DIR/train.harmful.txt" "$PROMPTS_DIR/train.harmless.txt" \
        "$DIRECTION" "$ARTIFACT_DIR/direction.log"
fi
if [ -s "$DIAGNOSTIC_DIRECTION" ]; then
    echo "reusing explicitly approved direction: $DIAGNOSTIC_DIRECTION"
else
    generate_direction "$REFERENCE_MODEL" \
        "$PROMPTS_DIR/train.harmful.txt" "$PROMPTS_DIR/train.harmless.txt" \
        "$DIAGNOSTIC_DIRECTION" \
        "$ARTIFACT_DIR/direction-q5-reference.log"
fi
if [ -s "$VALIDATION_DIRECTION" ]; then
    echo "reusing explicitly approved direction: $VALIDATION_DIRECTION"
else
    generate_direction "$SOURCE_MODEL" "$VALIDATION_HARMFUL" "$VALIDATION_HARMLESS" \
        "$VALIDATION_DIRECTION" "$ARTIFACT_DIR/direction-validation.log"
fi
sha256sum "$DIRECTION" "$DIAGNOSTIC_DIRECTION" "$VALIDATION_DIRECTION" \
    > "$ARTIFACT_DIR/directions.sha256"

if [ "$DIRECTION_GEOMETRY" = spectral-basis ]; then
    "$SCRIPT_DIR/compare_subspaces.py" "$DIRECTION" "$DIAGNOSTIC_DIRECTION" \
        --band "$LAYER_START" "$LAYER_END" --rank "$SUBSPACE_RANK" \
        --expected-layers "$DIRECTION_EXPECTED_LAYERS" \
        --min-principal-cosine 0.90 \
        --json "$ARTIFACT_DIR/subspace-q5-crosscheck.json" \
        | tee "$ARTIFACT_DIR/subspace-q5-crosscheck.txt"
    "$SCRIPT_DIR/compare_subspaces.py" "$DIRECTION" "$VALIDATION_DIRECTION" \
        --band "$LAYER_START" "$LAYER_END" --rank "$SUBSPACE_RANK" \
        --expected-layers "$DIRECTION_EXPECTED_LAYERS" \
        --min-principal-cosine 0.80 \
        --json "$ARTIFACT_DIR/subspace-validation-crosscheck.json" \
        | tee "$ARTIFACT_DIR/subspace-validation-crosscheck.txt"
else
    "$SCRIPT_DIR/analyze_direction.py" "$DIRECTION" \
        --band "$LAYER_START" "$LAYER_END" \
        --window "$((LAYER_END - LAYER_START + 1))" \
        --require-positive-band \
        --json "$ARTIFACT_DIR/direction-analysis.json" \
        | tee "$ARTIFACT_DIR/direction-analysis.txt"
    "$SCRIPT_DIR/analyze_direction.py" "$DIAGNOSTIC_DIRECTION" \
        --band "$LAYER_START" "$LAYER_END" \
        --window "$((LAYER_END - LAYER_START + 1))" \
        --require-positive-band \
        --json "$ARTIFACT_DIR/direction-q5-reference-analysis.json" \
        | tee "$ARTIFACT_DIR/direction-q5-reference-analysis.txt"
    "$SCRIPT_DIR/analyze_direction.py" "$VALIDATION_DIRECTION" \
        --band "$LAYER_START" "$LAYER_END" \
        --window "$((LAYER_END - LAYER_START + 1))" \
        --require-positive-band \
        --json "$ARTIFACT_DIR/direction-validation-analysis.json" \
        | tee "$ARTIFACT_DIR/direction-validation-analysis.txt"
    "$SCRIPT_DIR/compare_directions.py" "$DIRECTION" "$DIAGNOSTIC_DIRECTION" \
        --band "$LAYER_START" "$LAYER_END" --min-band-cosine 0.90 \
        --expected-layers "$DIRECTION_EXPECTED_LAYERS" \
        --json "$ARTIFACT_DIR/direction-crosscheck.json" \
        | tee "$ARTIFACT_DIR/direction-crosscheck.txt"
    "$SCRIPT_DIR/compare_directions.py" "$DIRECTION" "$VALIDATION_DIRECTION" \
        --band "$LAYER_START" "$LAYER_END" --min-band-cosine 0.80 \
        --expected-layers "$DIRECTION_EXPECTED_LAYERS" \
        --json "$ARTIFACT_DIR/direction-validation-crosscheck.json" \
        | tee "$ARTIFACT_DIR/direction-validation-crosscheck.txt"
    if [ "$SUBSPACE_RANK" -gt 0 ]; then
        "$SCRIPT_DIR/compare_subspaces.py" "$DIRECTION" "$DIAGNOSTIC_DIRECTION" \
            --band "$LAYER_START" "$LAYER_END" --rank "$SUBSPACE_RANK" \
            --expected-layers "$DIRECTION_EXPECTED_LAYERS" \
            --min-principal-cosine 0.90 \
            --json "$ARTIFACT_DIR/subspace-q5-crosscheck.json" \
            | tee "$ARTIFACT_DIR/subspace-q5-crosscheck.txt"
        "$SCRIPT_DIR/compare_subspaces.py" "$DIRECTION" "$VALIDATION_DIRECTION" \
            --band "$LAYER_START" "$LAYER_END" --rank "$SUBSPACE_RANK" \
            --expected-layers "$DIRECTION_EXPECTED_LAYERS" \
            --min-principal-cosine 0.80 \
            --json "$ARTIFACT_DIR/subspace-validation-crosscheck.json" \
            | tee "$ARTIFACT_DIR/subspace-validation-crosscheck.txt"
    fi
fi

TARGET_PATTERN='^token_embd\.weight$,^blk\.[0-9]+\.attn_output\.weight$,^blk\.0\.ffn_down\.weight$,^blk\.[1-9][0-9]*\.ffn_down_shexp\.weight$,^blk\.[1-9][0-9]*\.ffn_routed_up\.weight$'
COMMON_ARGS=(
    --allow-requantize --keep-f32 --keep-split --keep-pattern '_exps\.'
    --orthogonalize-control-vector "$DIRECTION"
    --orthogonalize-layer-range "$LAYER_START" "$LAYER_END"
    --orthogonalize-pattern "$TARGET_PATTERN"
    --orthogonalize-scale "$ORTHOGONALIZE_SCALE"
    --orthogonalize-expected-count 279
    --orthogonalize-quant-passes "$QUANT_PASSES"
    --orthogonalize-quant-correction "$QUANT_CORRECTION"
    --orthogonalize-max-residual "$MAX_RESIDUAL"
)
if [ "$SUBSPACE_RANK" -gt 0 ]; then
    COMMON_ARGS+=(--orthogonalize-subspace-rank "$SUBSPACE_RANK")
fi

if [ "$PATCH_EXISTING" = 1 ]; then
    # Reflink is mandatory: a silent byte-copy fallback would consume another
    # 788 GiB. Distinct device/inode pairs are checked before the quantizer gets
    # write access, so the immutable reference cannot be patched through a
    # hard link. The quantizer independently validates every tensor name, shape,
    # selected type, and encoded size before writing a selected payload range.
    [ "$(stat -c %d "$REFERENCE_DIR")" = "$(stat -c %d "$OUTPUT_DIR")" ] \
        || die "reference and output must share a filesystem for reflink construction"
    for index in "${!REFERENCE_SHARDS[@]}"; do
        split=$((index + 1))
        destination=$(printf '%s/%s-%05d-of-%05d.gguf' \
            "$OUTPUT_DIR" "$OUTPUT_PREFIX" "$split" "${#REFERENCE_SHARDS[@]}")
        cp --reflink=always --preserve=mode,timestamps -- \
            "${REFERENCE_SHARDS[$index]}" "$destination"
        chmod u+w -- "$destination"
        reference_identity=$(stat -c '%d:%i' "${REFERENCE_SHARDS[$index]}")
        candidate_identity=$(stat -c '%d:%i' "$destination")
        [ "$reference_identity" != "$candidate_identity" ] \
            || die "candidate shard is not a distinct inode: $destination"
    done
    COMMON_ARGS+=(--orthogonalize-patch-existing)
fi

# Count/shape/keep-pattern conflicts fail before an output file is opened.
"$QUANTIZE" --dry-run "${COMMON_ARGS[@]}" \
    "$SOURCE_MODEL" "$OUTPUT_MODEL" Q5_K "$THREADS" \
    > "$ARTIFACT_DIR/quantize-dry-run.log" 2>&1
grep -q "orthogonalization preflight matched 279 tensors; selected-F32=0; basis-rank=$((SUBSPACE_RANK > 0 ? SUBSPACE_RANK : 1));" \
    "$ARTIFACT_DIR/quantize-dry-run.log" \
    || die "dry run did not prove exactly 279 targets with zero selected F32 tensors"
printf -v QUANT_CORRECTION_LOGGED '%.4f' "$QUANT_CORRECTION"
grep -q "quant-passes $QUANT_PASSES; correction $QUANT_CORRECTION_LOGGED;" \
    "$ARTIFACT_DIR/quantize-dry-run.log" \
    || die "dry run did not prove the locked quantization correction schedule"
printf -v ORTHOGONALIZE_SCALE_LOGGED '%.4f' "$ORTHOGONALIZE_SCALE"
grep -q "scale $ORTHOGONALIZE_SCALE_LOGGED;" \
    "$ARTIFACT_DIR/quantize-dry-run.log" \
    || die "dry run did not prove the locked intervention coefficient"
if [ "$PATCH_EXISTING" = 1 ]; then
    grep -q 'patch-existing=yes' "$ARTIFACT_DIR/quantize-dry-run.log" \
        || die "dry run did not validate patch-existing mode"
    grep -q 'patch-existing validated 2573 tensors across 19 existing output shards' \
        "$ARTIFACT_DIR/quantize-dry-run.log" \
        || die "dry run did not validate the complete existing output layout"
fi

"$QUANTIZE" "${COMMON_ARGS[@]}" \
    "$SOURCE_MODEL" "$OUTPUT_MODEL" Q5_K "$THREADS" \
    2>&1 | tee "$QUANT_LOG"

# The full check compares every routed expert byte against the source. It is
# intentionally expensive: a one-bit expert mutation invalidates the candidate.
VERIFY_ARGS=(
    "$SOURCE_DIR" "$OUTPUT_DIR"
    --reference-layout "$REFERENCE_DIR"
    --quant-log "$QUANT_LOG" --max-residual "$MAX_RESIDUAL"
    --expected-scale "$ORTHOGONALIZE_SCALE"
    --expected-basis-rank "$((SUBSPACE_RANK > 0 ? SUBSPACE_RANK : 1))"
    --json "$ARTIFACT_DIR/model-verification.json"
)
if [ "$PATCH_EXISTING" = 1 ]; then
    VERIFY_ARGS+=(--require-patch-existing)
fi
"$SCRIPT_DIR/verify_model.py" "${VERIFY_ARGS[@]}" \
    | tee "$ARTIFACT_DIR/model-verification.txt"

input_guard || die "an input model changed during candidate construction"

total=0
for shard in "$OUTPUT_DIR"/*.gguf; do
    [ -f "$shard" ] || die "candidate has no GGUF shards"
    size=$(stat -c %s "$shard")
    total=$((total + size))
done
trap - EXIT
printf '%s\n' "$total" > "$OUTPUT_DIR/.complete"
echo "candidate structurally complete: $total bytes"
echo "NOT selected live; run the validation and comparison gates next"
