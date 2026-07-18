#!/bin/bash
# GLM-5.2 (Q4_K_XL) inference server - ik_llama.cpp fused-MoE, dual-socket NUMA-aware.
# See ../GLM-5.2-CPU-inference-runbook.md  §3 (NUMA), §5 (build), §6 (this script).
#
# Env overrides:
#   MODEL_DIR   dir with the .gguf shards        (default /models/GLM-5.2-Q4_K_XL/UD-Q4_K_XL)
#   IK_LLAMA    path to ik llama-server binary   (default ~/ik_llama.cpp/build/bin/llama-server)
#   THREADS     = physical core count            (default nproc; sweep DOWN for TG, see §7)
#   CTX         context size                     (default 1048576 = model max; DSA keeps KV tiny)
set -e

MODEL_DIR="${MODEL_DIR:-/models/GLM-5.2-Q4_K_XL/UD-Q4_K_XL}"
IK="${IK_LLAMA:-$HOME/ik_llama.cpp/build/bin/llama-server}"
THREADS="${THREADS:-$(nproc)}"
CTX="${CTX:-1048576}"

MODEL=$(ls "$MODEL_DIR"/GLM-5.2-UD-Q4_K_XL-00001-of-*.gguf 2>/dev/null | head -1)
[ -n "$MODEL" ] || { echo "model not found in $MODEL_DIR"; exit 1; }
[ -x "$IK" ]    || { echo "ik llama-server not found at $IK (build it, runbook §5)"; exit 1; }
[ -f "$HOME/.glm-api-key" ] || { echo "no ~/.glm-api-key - run gen-api-key.sh"; exit 1; }
echo "serving: $MODEL  (threads=$THREADS ctx=$CTX)"

# ─── DUAL-SOCKET NUMA ──────────────────────────────────────────────────────────
# --numa distribute spreads threads + memory across both sockets' memory controllers.
# If runbook §7's benchmark shows numactl interleave wins, prefix the exec line with:
#     numactl --interleave=all
# (and for pure-TG single-socket experiments: numactl --cpunodebind=0 --membind=0)
exec "$IK" \
    --model "$MODEL" \
    --alias glm-5.2 \
    --host 0.0.0.0 --port 8080 \
    --numa distribute \
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
