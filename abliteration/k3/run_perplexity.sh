#!/usr/bin/env bash
# Run the fixed K3 Wikitext control and record enough provenance for a paired gate.
set -euo pipefail
umask 077

LABEL=${1:?usage: run_perplexity.sh <label> <first-model-shard.gguf>}
MODEL=${2:?usage: run_perplexity.sh <label> <first-model-shard.gguf>}
if [ -d "$HOME/ik_llama.cpp-abliteration" ]; then
    DEFAULT_IK_DIR="$HOME/ik_llama.cpp-abliteration"
else
    DEFAULT_IK_DIR="$HOME/ik_llama.cpp"
fi
PPL_BIN=${PPL_BIN:-$DEFAULT_IK_DIR/build-abliteration/bin/llama-perplexity}
RUNNER=$(readlink -f -- "${BASH_SOURCE[0]}")
CORPUS=${PPL_CORPUS:-/models/wiki.test.raw}
OUT_DIR=${PPL_OUT:-/models/.abliteration/k3/perplexity}
THREADS=${THREADS:-64}
LOG=$OUT_DIR/$LABEL.log
META=$OUT_DIR/$LABEL.meta
INPUT_BEFORE=$OUT_DIR/$LABEL.input.before.stat
INPUT_AFTER=$OUT_DIR/$LABEL.input.after.stat
RUNTIME_LIBS=$OUT_DIR/$LABEL.runtime-libraries.sha256

die() { echo "run_perplexity: $*" >&2; exit 1; }
[ -x "$PPL_BIN" ] || die "missing perplexity binary: $PPL_BIN"
[ -r "$MODEL" ] || die "missing model: $MODEL"
[ -r "$CORPUS" ] || die "missing corpus: $CORPUS"
[[ "$LABEL" =~ ^[A-Za-z0-9._-]+$ ]] || die "label contains unsafe characters"
[[ "$THREADS" =~ ^[1-9][0-9]*$ ]] || die "THREADS must be a positive integer"
if pgrep -f '/llama-(server|perplexity|cvector-generator|quantize)([[:space:]]|$)' \
        >/dev/null 2>&1; then
    die "another llama model workload is running; do not compete for RAM or bandwidth"
fi
mkdir -p "$OUT_DIR"
[ ! -e "$LOG" ] && [ ! -e "$META" ] && [ ! -e "$INPUT_BEFORE" ] && [ ! -e "$INPUT_AFTER" ] \
    && [ ! -e "$RUNTIME_LIBS" ] \
    || die "refusing to overwrite $LABEL results"

if [[ "$MODEL" =~ ^(.+)-00001-of-00019\.gguf$ ]]; then
    prefix=${BASH_REMATCH[1]}
    mapfile -t INPUT_SHARDS < <(find "$(dirname "$MODEL")" -maxdepth 1 -type f \
        -name "$(basename "$prefix")-?????-of-00019.gguf" -print | sort)
    [ "${#INPUT_SHARDS[@]}" -eq 19 ] || die "expected 19 model shards, found ${#INPUT_SHARDS[@]}"
else
    INPUT_SHARDS=("$MODEL")
fi
stat -c $'%n\t%s\t%Y\t%Z' "${INPUT_SHARDS[@]}" > "$INPUT_BEFORE"
input_guard() {
    stat -c $'%n\t%s\t%Y\t%Z' "${INPUT_SHARDS[@]}" > "$INPUT_AFTER" || return 1
    cmp -s "$INPUT_BEFORE" "$INPUT_AFTER" || {
        echo "run_perplexity: model size/mtime/ctime changed during the run" >&2
        diff -u "$INPUT_BEFORE" "$INPUT_AFTER" >&2 || true
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

# The executable dynamically links the locally rebuilt inference libraries.
# Hash the complete resolved runtime-library closure: hashing only the small
# front-end binary would fail to identify the actual engine code being tested.
mapfile -t RUNTIME_LIBRARY_PATHS < <(
    ldd "$PPL_BIN" | awk '{ for (i = 1; i <= NF; ++i) if ($i ~ /^\//) { print $i; break } }' | sort -u
)
[ "${#RUNTIME_LIBRARY_PATHS[@]}" -gt 0 ] || die "could not resolve runtime libraries for $PPL_BIN"
sha256sum "${RUNTIME_LIBRARY_PATHS[@]}" > "$RUNTIME_LIBS"

{
    printf 'label=%s\nmodel=%s\ncorpus=%s\nthreads=%s\n' "$LABEL" "$MODEL" "$CORPUS" "$THREADS"
    printf 'binary=%s\n' "$PPL_BIN"
    printf 'binary_sha256=%s\n' "$(sha256sum "$PPL_BIN" | awk '{print $1}')"
    printf 'runtime_libraries_sha256=%s\n' "$(sha256sum "$RUNTIME_LIBS" | awk '{print $1}')"
    printf 'runner_sha256=%s\n' "$(sha256sum "$RUNNER" | awk '{print $1}')"
    printf 'corpus_sha256=%s\n' "$(sha256sum "$CORPUS" | awk '{print $1}')"
    printf 'arguments=-c 512 -b 512 -ub 512 -t %s -fa 1 -mla 3 --chunks 60\n' "$THREADS"
} > "$META"

"$PPL_BIN" --model "$MODEL" --file "$CORPUS" \
    --ctx-size 512 --batch-size 512 --ubatch-size 512 \
    --threads "$THREADS" -fa 1 -mla 3 --chunks 60 2>&1 | tee "$LOG"

grep -Eq 'Final estimate: PPL( over .*)? = [0-9.]+ \+/- [0-9.]+' "$LOG" \
    || die "perplexity run did not produce a final estimate"
input_guard || die "model changed during perplexity run"
