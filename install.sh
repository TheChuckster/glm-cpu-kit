#!/bin/bash
# =============================================================================
# One-shot software bring-up for GLM-5.2 CPU inference (ik_llama.cpp).
# Run on the TARGET machine as your normal (sudo-capable) user:
#
#   ./install.sh              # deps + build ik + api key + install service
#   ./install.sh --download   # also download the ~440 GB model (long - use tmux)
#
# DO THIS FIRST (runbook §1): fast storage mounted at /models (RAID0 NVMe), Ubuntu 24.04.
# NOT automated on purpose: NUMA/thread tuning - benchmark it (runbook §7).
# =============================================================================
set -euo pipefail
KITDIR="$(cd "$(dirname "$0")" && pwd)"
USER_NAME="$(id -un)"
MODEL_DIR="${MODEL_DIR:-/models/GLM-5.2-Q4_K_XL/UD-Q4_K_XL}"
say(){ printf '\n\033[1;36m== %s\033[0m\n' "$*"; }

say "1/6  dependencies"
sudo apt-get update -qq
sudo apt-get -y install build-essential cmake git python3 python3-pip numactl unzip curl jq >/dev/null
echo "  ok"

say "2/6  build ik_llama.cpp (native AVX-512/VNNI)"
if [ ! -x "$HOME/ik_llama.cpp/build/bin/llama-server" ]; then
  [ -d "$HOME/ik_llama.cpp" ] || git clone --depth 1 https://github.com/ikawrakow/ik_llama.cpp "$HOME/ik_llama.cpp"
  cd "$HOME/ik_llama.cpp"
  cmake -B build -DGGML_NATIVE=ON -DGGML_CUDA=OFF -DLLAMA_CURL=OFF >/dev/null
  cmake --build build --config Release -j "$(nproc)"
else
  echo "  already built"
fi
VNNI=$(objdump -d "$HOME/ik_llama.cpp/build/bin/libggml-cpu.so" 2>/dev/null | grep -c vpdpbusd || true)
echo "  VNNI (vpdpbusd) instructions in binary: ${VNNI:-0}  (must be > 0)"

say "3/6  api key"
bash "$KITDIR/serving/gen-api-key.sh"

say "4/6  install systemd service"
SVC=/etc/systemd/system/glm-server.service
sed -e "s|REPLACE_WITH_YOUR_USER|$USER_NAME|g" "$KITDIR/serving/glm-server.service" \
  | sed -e "s|ExecStart=.*serve-glm.sh|ExecStart=$KITDIR/serving/serve-glm.sh|" \
  | sudo tee "$SVC" >/dev/null
sudo systemctl daemon-reload
echo "  installed $SVC  (User=$USER_NAME, ExecStart=$KITDIR/serving/serve-glm.sh)"

say "5/6  model"
if [ "${1:-}" = "--download" ]; then
  bash "$KITDIR/serving/download-model.sh" "$MODEL_DIR"
fi
if ls "$MODEL_DIR"/GLM-5.2-UD-Q4_K_XL-00001-of-*.gguf >/dev/null 2>&1; then
  echo "  model present in $MODEL_DIR"; MODEL_READY=1
else
  echo "  NOT PRESENT. Run (in tmux, ~440 GB):  $KITDIR/serving/download-model.sh $MODEL_DIR"; MODEL_READY=0
fi

say "6/6  start"
if [ "${MODEL_READY:-0}" = "1" ]; then
  sudo systemctl enable --now glm-server
  echo "  starting (loads ~440 GB + mlock - a few minutes). Watch health:"
  echo "    watch -n5 'curl -s -o /dev/null -w \"%{http_code}\\n\" localhost:8080/health -H \"Authorization: Bearer \$(cat ~/.glm-api-key)\"'"
else
  echo "  skipped (no model). After download:  sudo systemctl enable --now glm-server"
fi

echo ""
say "DONE. Next (manual): benchmark NUMA + threads (runbook §7); harness (§9); monitoring (§11)."
