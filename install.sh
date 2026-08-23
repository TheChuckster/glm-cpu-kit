#!/bin/bash
# =============================================================================
# One-shot software bring-up for frontier MoE CPU inference (ik_llama.cpp).
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

say "1/7  dependencies"
sudo apt-get update -qq
sudo apt-get -y install build-essential cmake git python3 python3-pip numactl unzip curl jq >/dev/null
echo "  ok"

say "2/7  build ik_llama.cpp (native AVX-512/VNNI)"
# Clones the FORK, not ikawrakow/ik_llama.cpp. Branch `kimi-k3` is upstream main
# plus additive commits for the kimi-k3 architecture, which upstream does not
# have and declined to add (ik #2203). That used to matter only for the K3 rows,
# which pinned their own build tree; since every registry row now serves off this
# one `build`, cloning upstream here would produce an engine that cannot load K3
# at all while the registry cheerfully points K3 at it.
#
# GLM and DeepSeek-V4 are unaffected by the K3 commits and both pass the full
# validation gate on this branch - that is what made converging the trees safe.
# Validated 2026-08-23 at fork tip 4d1f09d3 on upstream base 8337e4cd; the
# branch remains the source of truth, so fresh installs follow later fixes too.
IK_REPO="${IK_REPO:-https://github.com/TheChuckster/ik_llama.cpp}"
IK_BRANCH="${IK_BRANCH:-kimi-k3}"
if [ ! -x "$HOME/ik_llama.cpp/build/bin/llama-server" ]; then
  [ -d "$HOME/ik_llama.cpp" ] || git clone --depth 1 -b "$IK_BRANCH" "$IK_REPO" "$HOME/ik_llama.cpp"
  cd "$HOME/ik_llama.cpp"
  cmake -B build -DGGML_NATIVE=ON -DGGML_CUDA=OFF -DLLAMA_CURL=OFF >/dev/null
  cmake --build build --config Release -j "$(nproc)"
else
  echo "  already built"
fi
# ggml lives at ggml/src/libggml.so in an ik build tree - NOT bin/libggml-cpu.so,
# which is mainline llama.cpp's layout. Checking the mainline path here silently
# reported 0 and made a correctly-built engine look like it had no VNNI at all.
VNNI=$(objdump -d "$HOME/ik_llama.cpp/build/ggml/src/libggml.so" 2>/dev/null | grep -c vpdpbusd || true)
echo "  VNNI (vpdpbusd) instructions in ggml: ${VNNI:-0}  (must be > 0)"

say "3/7  api key"
bash "$KITDIR/serving/gen-api-key.sh"

say "4/7  install model registry + glm-model CLI"
# serve-glm.sh resolves the active variant against /etc/glm-variants.conf, so
# without this step a fresh box starts the unit and immediately dies on
# "cannot read /etc/glm-variants.conf".
CONF=/etc/glm-variants.conf
if [ -e "$CONF" ] && ! sudo cmp -s "$KITDIR/serving/glm-variants.conf" "$CONF"; then
  # Never clobber a registry the operator has edited - correcting a pre-staged
  # variant's subdir/prefix once its publisher ships is expected, and a silent
  # overwrite would throw that away along with any local variants.
  sudo cp "$KITDIR/serving/glm-variants.conf" "$CONF.new"
  echo "  $CONF exists and differs - wrote $CONF.new instead (diff and merge)"
else
  sudo cp "$KITDIR/serving/glm-variants.conf" "$CONF"
  echo "  installed $CONF"
fi
sudo install -m 0755 "$KITDIR/serving/glm-model" /usr/local/bin/glm-model
echo "  installed /usr/local/bin/glm-model   (try: glm-model list)"

# The registry ships rows whose `engine` field names a build tree this script
# does NOT create. Say so here rather than let `glm-model use` fail with a
# missing-binary error on a box that was just told installation succeeded.
MISSING=""
for eng in $(awk -F'|' '/^[a-z0-9-]+ *\|/ {gsub(/ /,"",$8); if ($8 != "") print $8}' \
             "$KITDIR/serving/glm-variants.conf" | sort -u); do
    [ -x "$HOME/ik_llama.cpp/$eng/bin/llama-server" ] || MISSING="$MISSING $eng"
done
if [ -n "$MISSING" ]; then
    echo "  NOTE: these registry rows need engine trees that do not exist yet:$MISSING"
    echo "        Rows with an empty engine field use ~/ik_llama.cpp/build, which this"
    echo "        script built. A pinned tree is deliberate - a new architecture gets"
    echo "        its own so bringing it up cannot disturb a working model. Build with:"
    echo "          cmake -B <tree> -DGGML_NATIVE=ON && cmake --build <tree> -j"
    echo "        As shipped NO row pins an engine, so seeing this means you added one."
    echo "        Converge it back onto build once it is proven - a pinned tree does not"
    echo "        move when you rebase, and that is how one silently went three weeks"
    echo "        stale here, missing the fix for its own model's tool calls."
fi

say "5/7  install systemd service"
SVC=/etc/systemd/system/glm-server.service
sed -e "s|REPLACE_WITH_YOUR_USER|$USER_NAME|g" "$KITDIR/serving/glm-server.service" \
  | sed -e "s|ExecStart=.*serve-glm.sh|ExecStart=$KITDIR/serving/serve-glm.sh|" \
  | sudo tee "$SVC" >/dev/null
sudo systemctl daemon-reload
echo "  installed $SVC  (User=$USER_NAME, ExecStart=$KITDIR/serving/serve-glm.sh)"

say "6/7  model"
if [ "${1:-}" = "--download" ]; then
  bash "$KITDIR/serving/download-model.sh" "$MODEL_DIR"
fi
if ls "$MODEL_DIR"/GLM-5.2-UD-Q4_K_XL-00001-of-*.gguf >/dev/null 2>&1; then
  echo "  model present in $MODEL_DIR"; MODEL_READY=1
else
  echo "  NOT PRESENT. Run (in tmux, ~440 GB):  $KITDIR/serving/download-model.sh $MODEL_DIR"; MODEL_READY=0
fi

say "7/7  start"
if [ "${MODEL_READY:-0}" = "1" ]; then
  sudo systemctl enable --now glm-server
  echo "  starting (loads ~440 GB + mlock - a few minutes). Watch health:"
  echo "    watch -n5 'curl -s -o /dev/null -w \"%{http_code}\\n\" localhost:8080/health -H \"Authorization: Bearer \$(cat ~/.glm-api-key)\"'"
else
  echo "  skipped (no model). After download:  sudo systemctl enable --now glm-server"
fi

echo ""
say "DONE. Next (manual): benchmark NUMA + threads (runbook §7); harness (§9); monitoring (§11)."
