#!/usr/bin/env python3
"""Compose a rank-19 K3 control vector from v3's band plus a public donor."""

import argparse
import hashlib
import json
import math
import os
import sys
from pathlib import Path

os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["OMP_NUM_THREADS"] = "1"
os.environ["MKL_NUM_THREADS"] = "1"

import numpy as np

from analyze_direction import dot, load_vectors


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def normalized(values):
    vector = np.asarray(values, dtype=np.float64)
    norm = float(np.linalg.norm(vector))
    if not math.isfinite(norm) or norm == 0:
        raise ValueError("cannot normalize a zero or non-finite vector")
    return vector / norm


def compose(source_path, donor_path, first, last):
    metadata, source = load_vectors(source_path)
    expected = list(range(first, last + 1))
    if metadata.get("controlvector.model_hint") != "kimi-k3":
        raise ValueError("source control vector is not tagged for kimi-k3")
    if len(source) != 92 or any(layer not in source for layer in expected):
        raise ValueError("source does not contain the expected 92 K3 directions")
    vectors = [normalized(source[layer]) for layer in expected]
    donor = np.load(donor_path, allow_pickle=False)
    if donor.shape != (7168,) or donor.dtype.kind != "f":
        raise ValueError("donor direction must be a 7168-element floating vector")
    donor = normalized(donor)
    band_mean = normalized(np.sum(vectors, axis=0))
    donor_cosine = float(np.dot(donor, band_mean))
    if donor_cosine < 0:
        donor = -donor
        donor_cosine = -donor_cosine
    vectors.append(donor)
    return vectors, donor_cosine


def write_gguf(output, vectors, gguf_py):
    sys.path.insert(0, str(gguf_py))
    try:
        from gguf import GGUFWriter
    except ImportError as exc:
        raise ValueError(f"cannot import GGUFWriter from {gguf_py}") from exc
    writer = GGUFWriter(output, "controlvector")
    writer.add_string("controlvector.model_hint", "kimi-k3")
    writer.add_uint32("controlvector.layer_count", len(vectors))
    for index, vector in enumerate(vectors, 1):
        writer.add_tensor(
            f"direction.{index}", np.asarray(vector, dtype="<f4"))
    writer.write_header_to_file()
    writer.write_kv_data_to_file()
    writer.write_tensors_to_file()
    writer.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("source_control_vector", type=Path)
    parser.add_argument("donor_direction", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--gguf-py", type=Path, required=True)
    parser.add_argument("--source-band", type=int, nargs=2, default=(56, 73))
    args = parser.parse_args()
    os.umask(0o077)
    manifest_path = args.output.with_suffix(".manifest.json")
    if args.output.exists() or manifest_path.exists():
        raise SystemExit(f"refusing to overwrite {args.output} or {manifest_path}")
    first, last = args.source_band
    if first < 1 or last < first or last - first + 1 != 18:
        parser.error("--source-band must identify exactly 18 directions")
    vectors, donor_cosine = compose(
        args.source_control_vector, args.donor_direction, first, last)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    write_gguf(args.output, vectors, args.gguf_py)
    os.chmod(args.output, 0o600)
    manifest = {
        "method": (
            "normalize source directions 56--73 individually, append the normalized "
            "public donor direction with its sign aligned to the source-band mean, "
            "and encode the 19 raw vectors for full-rank SVD orthogonalization"
        ),
        "source_control_vector_sha256": sha256(args.source_control_vector),
        "donor_direction_sha256": sha256(args.donor_direction),
        "source_layers_by_output_direction": {
            str(index): layer for index, layer in enumerate(range(first, last + 1), 1)
        },
        "donor_output_direction": 19,
        "donor_absolute_cosine_to_source_band_mean": donor_cosine,
        "direction_count": 19,
        "embedding_width": 7168,
        "gguf_writer": "ik_llama.cpp/gguf-py; exact engine closure pinned by build manifest",
        "artifact_sha256": sha256(args.output),
    }
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    os.chmod(manifest_path, 0o600)
    print(
        f"composed 19-direction control vector; donor/band |cos|={donor_cosine:.12f}")
    print(f"{sha256(args.output)}  {args.output.name}")
    print(f"{sha256(manifest_path)}  {manifest_path.name}")


if __name__ == "__main__":
    try:
        main()
    except (EOFError, OSError, ValueError) as exc:
        raise SystemExit(f"FAIL: {exc}") from exc
