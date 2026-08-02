#!/bin/bash
# Run Kimi K3 UD-Q2_K_XL on unsloth's llama.cpp fork, for the viability test.
# This is NOT the production server - it is the throwaway measurement described
# in porting/k3/README.md ("the cheap experiment"). It deliberately uses a
# DIFFERENT port and binary from the ik_llama.cpp GLM server.
#
# PREREQUISITE: stop the GLM server first. K3 needs ~861 GB mlocked and the GLM
# server holds ~441 GB; 1302 > 1133 GB RAM, so they cannot coexist.
#     sudo systemctl stop glm-server.service
# and restart it when you are done measuring:
#     sudo systemctl start glm-server.service
#
# Env overrides: PORT (8081), CTX (16384), THREADS (64 = physical cores).
set -e

BIN="${K3_BIN:-$HOME/llama.cpp-k3/build/bin/llama-server}"
MODEL="${K3_MODEL:-/models/Kimi-K3-UD-Q2_K_XL/Kimi-K3-UD-Q2_K_XL-00001-of-00019.gguf}"
PORT="${PORT:-8081}"
CTX="${CTX:-16384}"
THREADS="${THREADS:-64}"

[ -x "$BIN" ]  || { echo "no k3 llama-server at $BIN (build it, README)"; exit 1; }
[ -s "$MODEL" ] || { echo "model shard 1 missing: $MODEL"; exit 1; }

# Refuse to fight the GLM server for RAM. If it is up, K3's mlock will thrash or
# OOM - stop it first. Checked by port, since this box's GLM server is on 8080.
if curl -sf --max-time 3 "http://127.0.0.1:8080/health" >/dev/null 2>&1; then
    echo "GLM server is still up on :8080 - stop it first:"
    echo "    sudo systemctl stop glm-server.service"
    echo "(K3 needs ~861 GB mlocked; the two do not fit together)"
    exit 1
fi

echo "K3 viability run: $MODEL"
echo "  port=$PORT ctx=$CTX threads=$THREADS  (mainline fork, no fused-MoE - PP is slower than ik)"
echo

# --reasoning-format deepseek: K3 ALWAYS thinks and has no enable_thinking; this
#   routes the thoughts into message.reasoning_content rather than content.
# --jinja: use K3's embedded chat template.
# No --temp/--top-p: Moonshot's generation_config sets none; keep defaults.
# --mlock: pin the 861 GB so the measurement is not distorted by page faults.
exec "$BIN" \
    --model "$MODEL" \
    --alias kimi-k3 \
    --host 0.0.0.0 --port "$PORT" \
    --ctx-size "$CTX" \
    --parallel 1 \
    --threads "$THREADS" --threads-batch "$THREADS" \
    --batch-size 2048 --ubatch-size 2048 \
    -fa on \
    --mlock \
    --jinja \
    --reasoning-format deepseek \
    --repeat-penalty 1.1 --repeat-last-n 256 \
    --metrics
