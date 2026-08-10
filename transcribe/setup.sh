#!/usr/bin/env bash
# One-time environment setup for ../transcribe.sh. Idempotent - safe to re-run.
#
# Creates transcribe/.venv with torch (CUDA 12.8 wheels, which is what Blackwell
# sm_120 needs), faster-whisper, and SpeechBrain.
#
# WHY A VENV AND NOT SYSTEM PYTHON: this box's system python is 3.14, which most
# of the ML stack has no wheels for yet. 3.12 is pinned deliberately.
#
# WHY cu128 ON A CUDA 13.3 DRIVER: the driver is backward compatible with older
# CUDA runtimes, and cu128 is the wheel series with sm_120 (Blackwell) kernels.
# Verified working: torch 2.11.0+cu128 reports sm_120 and runs GPU matmuls here.
set -euo pipefail
cd "$(dirname "$0")"

command -v uv >/dev/null || { echo "uv not found - install it first (pacman -S uv)" >&2; exit 1; }

echo "1/3  venv (python 3.12)"
uv venv --python 3.12 .venv

echo "2/3  torch + torchaudio (cu128, for Blackwell sm_120)"
VIRTUAL_ENV=.venv uv pip install --quiet torch torchaudio \
  --index-url https://download.pytorch.org/whl/cu128

echo "3/3  faster-whisper + speechbrain"
VIRTUAL_ENV=.venv uv pip install --quiet numpy faster-whisper speechbrain scikit-learn soundfile

echo
.venv/bin/python - <<'PY'
import torch, faster_whisper, ctranslate2, speechbrain
print(f"torch {torch.__version__}  cuda_available={torch.cuda.is_available()}")
if torch.cuda.is_available():
    print(f"  device: {torch.cuda.get_device_name(0)}  sm_%d%d" % torch.cuda.get_device_capability(0))
print(f"faster-whisper {faster_whisper.__version__}  ctranslate2 {ctranslate2.__version__}")
print(f"speechbrain {speechbrain.__version__}")
print(f"ct2 cuda devices: {ctranslate2.get_cuda_device_count()}")
PY
echo
echo "ready.  usage:  ./transcribe.sh <audio-file>"
