#!/bin/bash
# GLM-5.2 inference server - ik_llama.cpp fused-MoE, NUMA-aware.
# See ../GLM-5.2-CPU-inference-runbook.md  §3 (NUMA), §5 (build), §6 (this script).
#
# WHICH model is served comes from GLM_VARIANT, resolved against the registry in
# glm-variants.conf. Set it with `glm-model use <variant>`, which writes
# /etc/default/glm-server for the unit's EnvironmentFile to pick up. Defaults to
# `base`, so an unset or missing state file serves the model the box shipped
# with rather than failing to start.
#
# Env overrides:
#   GLM_VARIANT variant handle from the registry  (default base)
#   VARIANTS    path to the registry              (default /etc/glm-variants.conf)
#   MODEL_DIR   bypass the registry entirely and serve this dir (escape hatch)
#   IK_LLAMA    path to ik llama-server binary   (default ~/ik_llama.cpp/build/bin/llama-server)
#   THREADS     = PHYSICAL core count            (default nproc; on an SMT part nproc is
#               double the physical count and too high - set it explicitly. Sweep DOWN
#               for TG, see §7)
#   CTX         context ceiling (default 65536=64K). Fits to 1M on RAM, but PP is O(n^2):
#               a 128K-context first-token is ~2-3 HOURS. Do NOT raise blindly — see runbook
#               "Context window: the trap". The harness limit MUST be set below this.
#   NUMA_POLICY "distribute" adds --numa distribute for dual-socket; empty for a
#               single NUMA domain (NPS0), where the flag only adds overhead.
set -e

VARIANTS="${VARIANTS:-/etc/glm-variants.conf}"
GLM_VARIANT="${GLM_VARIANT:-base}"
IK="${IK_LLAMA:-$HOME/ik_llama.cpp/build/bin/llama-server}"
THREADS="${THREADS:-$(nproc)}"
CTX="${CTX:-65536}"
NUMA_POLICY="${NUMA_POLICY:-}"
ALIAS="glm-5.2"

if [ -n "${MODEL_DIR:-}" ]; then
    # Explicit directory wins, for one-off experiments outside the registry.
    MODEL=$(ls "$MODEL_DIR"/*-00001-of-*.gguf 2>/dev/null | head -1)
    [ -n "$MODEL" ] || { echo "model not found in $MODEL_DIR"; exit 1; }
else
    [ -r "$VARIANTS" ] || { echo "cannot read $VARIANTS (set MODEL_DIR to bypass)"; exit 1; }
    row=$(awk -F'|' -v want="$GLM_VARIANT" '
        /^[[:space:]]*#/ || /^[[:space:]]*$/ { next }
        { gsub(/^[ \t]+|[ \t]+$/, "", $1)
          if ($1 != want) next
          for (i = 2; i <= 7; i++) gsub(/^[ \t]+|[ \t]+$/, "", $i)
          print $4, $5, $6, $7
          exit }' "$VARIANTS")
    [ -n "$row" ] || { echo "unknown GLM_VARIANT '$GLM_VARIANT' - not in $VARIANTS"; exit 1; }
    read -r PREFIX SHARDS ALIAS DIR <<<"$row"
    MODEL="$DIR/$(printf '%s-%05d-of-%05d.gguf' "$PREFIX" 1 "$SHARDS")"
    [ -s "$MODEL" ] || {
        echo "variant '$GLM_VARIANT' selected but its first shard is missing: $MODEL"
        echo "run: glm-model download $GLM_VARIANT"
        exit 1
    }
fi

[ -x "$IK" ]    || { echo "ik llama-server not found at $IK (build it, runbook §5)"; exit 1; }
[ -f "$HOME/.glm-api-key" ] || { echo "no ~/.glm-api-key - run gen-api-key.sh"; exit 1; }
echo "serving variant '$GLM_VARIANT' as '$ALIAS'  (threads=$THREADS ctx=$CTX)"
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
    --mlock \
    --jinja \
    --chat-template-kwargs '{"enable_thinking": false}' \
    --repeat-penalty 1.1 --repeat-last-n 256 \
    --metrics \
    --api-key-file "$HOME/.glm-api-key"

# NOTE: llama-server does not reject requests whose `model` field names a
# different alias - it serves whatever is loaded. So a client asking for
# glm-5.2-abliterated while `base` is live gets the BASE model with no error.
# `glm-model status` compares /v1/models against the selected variant, which is
# the reliable way to know what you are actually talking to.
