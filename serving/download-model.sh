#!/bin/bash
# Download GLM-5.2 Q4_K_XL (unsloth dynamic quant) from HuggingFace.
# IPv4-forced (cloud VMs often have flaky IPv6 that stalls HF) + 4-way parallel + resumable.
# ~440 GB on disk. Shard 00001 is ~9 MB (split metadata) - that is NOT truncation.
#   ./download-model.sh [dest_dir]
set -e
DIR="${1:-/models/GLM-5.2-Q4_K_XL/UD-Q4_K_XL}"
mkdir -p "$DIR"; cd "$DIR"
BASE="https://huggingface.co/unsloth/GLM-5.2-GGUF/resolve/main/UD-Q4_K_XL"

seq -w 1 11 | xargs -P4 -I{} curl -4 -sfL -C - --retry 1000 --retry-delay 10 --retry-all-errors \
  -o "GLM-5.2-UD-Q4_K_XL-000{}-of-00011.gguf" \
  "$BASE/GLM-5.2-UD-Q4_K_XL-000{}-of-00011.gguf"

echo "== on-disk shards =="; ls -la *.gguf
echo "== verify against HuggingFace sizes =="
curl -4 -s "https://huggingface.co/api/models/unsloth/GLM-5.2-GGUF/tree/main/UD-Q4_K_XL" \
  | python3 -c 'import sys,json;[print(f"{ (f.get("lfs") or {}).get("size",0)/1e9:7.2f}G  {f[\"path\"].split(\"/\")[-1]}") for f in json.load(sys.stdin) if f.get("path","").endswith(".gguf")]' 2>/dev/null || true
