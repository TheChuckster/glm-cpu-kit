#!/bin/bash
# CPU inference server - ik_llama.cpp fused-MoE, NUMA-aware. Serves GLM-5.2,
# Kimi K2.x or DeepSeek-V4-Flash depending on the selected variant.
# See ../GLM-5.2-CPU-inference-runbook.md  §3 (NUMA), §5 (build), §6 (this script).
#
# WHICH model is served comes from GLM_VARIANT, resolved against the registry in
# glm-variants.conf. Set it with `glm-model use <variant>`, which writes
# /etc/default/glm-server for the unit's EnvironmentFile to pick up. Defaults to
# `base`, so an unset or missing state file serves the model the box shipped
# with rather than failing to start.
#
# The registry also supplies per-model serving flags (field 9, `opts`), because
# the right flags are not the same across model families - GLM turns thinking
# off with a chat-template kwarg, Kimi with --reasoning off, DeepSeek-V4 with
# --reasoning-format deepseek. Those are appended LAST, so a variant can
# override any default set below.
#
# It also supplies which ENGINE serves a variant (field 8). Architectures land
# in ik_llama.cpp continuously, so the commit that can serve a new model is
# usually newer than the one already serving the old ones well. Field 8 lets a
# new arch be brought up in its own build tree without repointing the engine
# under a model that currently works.
#
# Env overrides:
#   GLM_VARIANT variant handle from the registry  (default base)
#   VARIANTS    path to the registry              (default /etc/glm-variants.conf)
#   MODEL_DIR   bypass the registry entirely and serve this dir (escape hatch)
#   IK_ROOT     ik_llama.cpp checkout             (default ~/ik_llama.cpp)
#   IK_LLAMA    full path to a llama-server binary; overrides the registry's
#               engine field entirely (escape hatch for a one-off engine)
#   THREADS     = PHYSICAL core count            (auto-detected via lscpu; nproc would
#               report double on an SMT part and 128 threads measures 0.43 tok/s against
#               31.5 at 64. Decode saturates ~32 regardless, so this is a prefill knob)
#   CTX         context ceiling (default 65536=64K). Fits to 1M on RAM, but PP is O(n^2):
#               a 128K-context first-token is ~2-3 HOURS. Do NOT raise blindly — see runbook
#               "Context window: the trap". The harness limit MUST be set below this.
#   NUMA_POLICY auto: "distribute" when numactl reports >1 node, empty on a single
#               NUMA domain (NPS0) where the flag only adds overhead. Set explicitly
#               to override; NUMA_POLICY= (empty) forces it off.
set -e

VARIANTS="${VARIANTS:-/etc/glm-variants.conf}"
GLM_VARIANT="${GLM_VARIANT:-base}"
IK_ROOT="${IK_ROOT:-$HOME/ik_llama.cpp}"
# nproc counts SMT siblings, so on a 64-core part it says 128 - and 128 is not
# merely suboptimal here, it is catastrophic: DeepSeek-V4 measures 0.43 tok/s at
# 128 threads against 31.5 at 64. Decode saturates around 32 threads on every
# model on this box anyway (bandwidth, not cores); prefill wants physical cores
# and no more. Default to those, and let THREADS override.
_physical_cores() {
    local n
    n=$(lscpu -p=Core,Socket 2>/dev/null | grep -v '^#' | sort -u | wc -l)
    [ "${n:-0}" -gt 0 ] 2>/dev/null && echo "$n" || nproc
}
THREADS="${THREADS:-$(_physical_cores)}"
CTX="${CTX:-65536}"
# Same reasoning as THREADS above: the correct value is a property of the machine,
# so read it off the machine. The README calls NUMA "the main thing that differs
# from a single socket", and leaving this empty by default meant the reference
# target - a dual-socket box - silently got the single-socket setting.
_numa_nodes() {
    local n
    n=$(numactl --hardware 2>/dev/null | awk '/^available:/{print $2}')
    [ "${n:-0}" -gt 0 ] 2>/dev/null && echo "$n" || echo 1
}
if [ -z "${NUMA_POLICY+set}" ] || [ -z "$NUMA_POLICY" ]; then
    if [ "$(_numa_nodes)" -gt 1 ]; then NUMA_POLICY=distribute; else NUMA_POLICY=""; fi
fi
ALIAS="glm-5.2"
ENGINE=""

if [ -n "${MODEL_DIR:-}" ]; then
    # Explicit directory wins, for one-off experiments outside the registry.
    MODEL=$(ls "$MODEL_DIR"/*-00001-of-*.gguf 2>/dev/null | head -1)
    [ -n "$MODEL" ] || MODEL=$(ls "$MODEL_DIR"/*.gguf 2>/dev/null | head -1)
    [ -n "$MODEL" ] || { echo "model not found in $MODEL_DIR"; exit 1; }
else
    [ -r "$VARIANTS" ] || { echo "cannot read $VARIANTS (set MODEL_DIR to bypass)"; exit 1; }
    # Re-joined with '|', not a space: fields are legitimately empty (subdir is,
    # for a repo-root quant; engine is, for the default build) and `read`
    # collapses runs of whitespace, which would shift every later field one to
    # the left. See glm-model's variant_row for the same reasoning. Field 9
    # (opts) keeps its inner spaces.
    row=$(awk -F'|' -v want="$GLM_VARIANT" 'BEGIN { OFS = "|" }
        /^[[:space:]]*#/ || /^[[:space:]]*$/ { next }
        { gsub(/^[ \t]+|[ \t]+$/, "", $1)
          if ($1 != want) next
          for (i = 2; i <= 9; i++) gsub(/^[ \t]+|[ \t]+$/, "", $i)
          print $4, $5, $6, $7, $8, $9
          exit }' "$VARIANTS")
    [ -n "$row" ] || { echo "unknown GLM_VARIANT '$GLM_VARIANT' - not in $VARIANTS"; exit 1; }
    # SHARDS is unused (the model file is found by glob below) but must still
    # be named: drop it and PREFIX would absorb the rest of the line.
    # shellcheck disable=SC2034
    IFS='|' read -r PREFIX SHARDS ALIAS DIR ENGINE VARIANT_OPTS <<<"$row"
    # Glob rather than reconstruct the -000NN-of-000MM suffix: the shard count
    # may be `?` (resolved from HuggingFace at download time, runbook), and a
    # publisher re-sharding a repo should not silently break serving.
    MODEL=$(ls "$DIR/$PREFIX"-00001-of-*.gguf 2>/dev/null | head -1)
    # Not every publisher shards. ggml-org and antirez ship DeepSeek-V4-Flash as
    # ONE ~155 GB file with no -000NN-of-000MM suffix at all, so fall back to the
    # bare <prefix>.gguf. Sharded first, because only one of the two can match.
    [ -n "$MODEL" ] || { [ -f "$DIR/$PREFIX.gguf" ] && MODEL="$DIR/$PREFIX.gguf"; }
    [ -n "$MODEL" ] && [ -s "$MODEL" ] || {
        echo "variant '$GLM_VARIANT' selected but its weights are missing in $DIR"
        echo "run: glm-model download $GLM_VARIANT"
        exit 1
    }
fi

# ─── engine ───────────────────────────────────────────────────────────────────
# Field 8 of the registry names the build tree under $IK_ROOT that serves this
# variant (empty = `build`, the default), or an absolute path to a llama-server
# binary for an engine that lives somewhere else entirely. IK_LLAMA overrides
# both.
#
# This exists because bringing up a new architecture means moving to a much
# newer ik commit, and that commit is not automatically the one you want under
# the model that is already serving well. Separate build trees let a new arch be
# proven before anything else is repointed at it.
if [ -n "${IK_LLAMA:-}" ]; then
    IK="$IK_LLAMA"
elif [ -z "$ENGINE" ]; then
    IK="$IK_ROOT/build/bin/llama-server"
elif [ "${ENGINE#/}" != "$ENGINE" ]; then
    IK="$ENGINE"
else
    IK="$IK_ROOT/$ENGINE/bin/llama-server"
fi

# ─── per-variant serving flags ────────────────────────────────────────────────
# Field 9 of the registry. Expanded as shell words so a JSON argument keeps its
# embedded space; appended LAST below so a variant can override any default.
# The registry is root-owned and read by a root-installed unit, so this is the
# same trust boundary as the unit file.
VARIANT_ARGS=()
[ -n "${VARIANT_OPTS:-}" ] && eval "VARIANT_ARGS=($VARIANT_OPTS)"

[ -x "$IK" ]    || { echo "llama-server not found at $IK (engine='${ENGINE:-build}', build it - runbook §5)"; exit 1; }
[ -f "$HOME/.glm-api-key" ] || { echo "no ~/.glm-api-key - run gen-api-key.sh"; exit 1; }
# --mlock pins the whole model in RAM, which is right when it fits and fatal when
# it does not: the unit sets LimitMEMLOCK=infinity, so the kernel will happily try
# and then OOM. Compared against MemTotal rather than MemAvailable because a
# restart races the previous instance's pages being released.
# Two tests, because a percentage alone is not enough. A 1009 GB model on a
# 1133 GB box is 89% - under the ratio test - yet leaves only 124 GB for the OS,
# the KV cache and the compute buffers, where the 861 GB model that works leaves
# 272 GB. So also require an absolute floor of headroom.
#
# MLOCK_MIN_HEADROOM_GB overrides the floor. There is no --no-mlock flag in
# llama-server, so a registry row cannot undo --mlock; this is the only place
# the decision can be made.
MLOCK_ARGS=(--mlock)
MLOCK_MIN_HEADROOM_GB="${MLOCK_MIN_HEADROOM_GB:-160}"
_model_bytes=$(du -scb "$(dirname "$MODEL")"/*.gguf 2>/dev/null | awk '/total$/{print $1}')
_mem_total=$(awk '/^MemTotal:/{print $2*1024}' /proc/meminfo 2>/dev/null)
if [ -n "$_model_bytes" ] && [ -n "$_mem_total" ]; then
    _headroom=$(( (_mem_total - _model_bytes) / 1000000000 ))
    if [ "$_model_bytes" -gt $(( _mem_total * 92 / 100 )) ] || \
       [ "$_headroom" -lt "$MLOCK_MIN_HEADROOM_GB" ]; then
        MLOCK_ARGS=()
        echo "  model is $(( _model_bytes / 1000000000 )) GB against $(( _mem_total / 1000000000 )) GB of RAM"
        echo "  -> ${_headroom} GB headroom, below the ${MLOCK_MIN_HEADROOM_GB} GB floor"
        echo "  -> NOT using --mlock; it would pin more than fits and OOM. Expect slower TG,"
        echo "     since pages will be served from the page cache instead of pinned."
    fi
fi

echo "serving variant '$GLM_VARIANT' as '$ALIAS'  (threads=$THREADS ctx=$CTX engine=${ENGINE:-build})"
echo "model: $MODEL"

# ─── NUMA ──────────────────────────────────────────────────────────────────────
# --numa distribute spreads threads + memory across both sockets' memory controllers,
# which helps on dual-socket and does nothing useful on a single NUMA domain.
# If runbook §7's benchmark shows numactl interleave wins, prefix the exec line with:
#     numactl --interleave=all
# (and for pure-TG single-socket experiments: numactl --cpunodebind=0 --membind=0)
NUMA_ARGS=()
[ -n "$NUMA_POLICY" ] && NUMA_ARGS=(--numa "$NUMA_POLICY")

exec "$IK" \
    --model "$MODEL" \
    --alias "$ALIAS" \
    --host 0.0.0.0 --port 8080 \
    "${NUMA_ARGS[@]}" \
    --ctx-size "$CTX" \
    --defrag-thold 0.1 \
    --parallel 1 \
    --threads "$THREADS" --threads-batch "$THREADS" \
    --batch-size 2048 --ubatch-size 2048 \
    -fa on \
    --cache-type-k q8_0 --cache-type-v q8_0 \
    "${MLOCK_ARGS[@]}" \
    --jinja \
    --repeat-penalty 1.1 --repeat-last-n 256 \
    --metrics \
    --api-key-file "$HOME/.glm-api-key" \
    "${VARIANT_ARGS[@]}"

# NOTE: llama-server does not reject requests whose `model` field names a
# different alias - it serves whatever is loaded. So a client asking for
# glm-5.2-abliterated while `base` is live gets the BASE model with no error.
# `glm-model status` compares /v1/models against the selected variant, which is
# the reliable way to know what you are actually talking to.
