#!/usr/bin/env python3
"""Recover a public K3 ablation direction from two sparse BF16 weight deltas.

Only the exact payload ranges named below are accepted.  This avoids treating a
third-party model card or a multi-terabyte checkpoint as the direction source:
the rank-one edit is measured directly in two distant writer matrices.
"""

import argparse
import hashlib
import json
import math
import os
from pathlib import Path

# Reproducible artifact bytes matter more than parallelism for two sparse
# 51-MiB payloads.  These must be set before NumPy initializes a BLAS runtime.
os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["OMP_NUM_THREADS"] = "1"
os.environ["MKL_NUM_THREADS"] = "1"

import numpy as np


ROWS = 7168
COLS = 3584
TENSOR_TEMPLATE = (
    "language_model.model.layers.{layer}.block_sparse_moe."
    "routed_expert_up_proj.weight"
)
BASE_REPO = "moonshotai/Kimi-K3"
BASE_REVISION = "a590ce090cb049c93a33dfe8c208ec652aa20503"
DONOR_REPO = "Resggg/Kimi-K3-Abliterated-modal"
DONOR_REVISION = "b3a52d265b56551c0011b24d299ba3f8f1393e42"
INDEX_SHA256 = "a1c5210650ce71d2d3ae9ec5a101ac4afd3cf4b10091be589853437eb967febd"
PAYLOAD_RANGE = "65700144-117080367"
INPUTS = {
    "base_layer56": {
        "layer": 56,
        "shard": "model-00057-of-000096.safetensors",
        "sha256": "7d129deaa31934d1d9a5a0a1a6f39d2eb5f40c6fd8cf6cfa193b18363077959b",
    },
    "donor_layer56": {
        "layer": 56,
        "shard": "model-00057-of-000096.safetensors",
        "sha256": "1d9f3eddbecddf898e3d159af768f660f5a20e7aa5da03f8122dd03a72ce388d",
    },
    "base_layer70": {
        "layer": 70,
        "shard": "model-00071-of-000096.safetensors",
        "sha256": "ec608a82d6059f7536c10d1a65158a62320d5c6339d17450532278236d395e8d",
    },
    "donor_layer70": {
        "layer": 70,
        "shard": "model-00071-of-000096.safetensors",
        "sha256": "300b50d0325b45ad643ca808fb713d8288560f285ef07bc4aa88bcc5991e9c4d",
    },
}


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonicalize(vector):
    vector = np.asarray(vector, dtype=np.float64)
    norm = float(np.linalg.norm(vector))
    if not math.isfinite(norm) or norm == 0:
        raise ValueError("cannot normalize a zero or non-finite vector")
    vector = vector / norm
    pivot = int(np.argmax(np.abs(vector)))
    if vector[pivot] < 0:
        vector = -vector
    return vector


def decode_bf16(path, rows=ROWS, cols=COLS):
    expected_size = rows * cols * 2
    if path.stat().st_size != expected_size:
        raise ValueError(
            f"{path} is {path.stat().st_size} bytes; expected {expected_size}")
    words = np.fromfile(path, dtype="<u2")
    bits = words.astype("<u4") << np.uint32(16)
    return bits.view("<f4").reshape(rows, cols)


def top_left_direction(delta, iterations=40):
    """Return a deterministic power-iteration estimate and rank-one energy."""
    cols = delta.shape[1]
    # A fixed, non-symmetric start avoids randomness and is extremely unlikely
    # to be orthogonal to the dominant right singular vector.
    indices = np.arange(1, cols + 1, dtype=np.float64)
    right = canonicalize(np.sin(indices) + 0.5 * np.cos(indices * math.sqrt(2.0)))
    right = right.astype(np.float32)
    previous = None
    for _ in range(iterations):
        left = delta @ right
        left_norm = float(np.linalg.norm(left))
        if not math.isfinite(left_norm) or left_norm == 0:
            raise ValueError("power iteration reached a degenerate left vector")
        left = left / left_norm
        right = delta.T @ left
        right_norm = float(np.linalg.norm(right))
        if not math.isfinite(right_norm) or right_norm == 0:
            raise ValueError("power iteration reached a degenerate right vector")
        right = right / right_norm
        if previous is not None and abs(float(np.dot(right, previous))) > 1 - 1e-7:
            break
        previous = right.copy()

    projected = delta @ right
    sigma = float(np.linalg.norm(projected))
    direction = canonicalize(projected)
    energy = 0.0
    for first in range(0, delta.shape[0], 128):
        block = delta[first:first + 128]
        energy += float(np.einsum("ij,ij->", block, block, dtype=np.float64))
    fraction = sigma * sigma / energy
    return direction, sigma, fraction, math.sqrt(max(0.0, 1.0 - fraction))


def recover_pair(base_path, donor_path, rows=ROWS, cols=COLS):
    base = decode_bf16(base_path, rows, cols)
    donor = decode_bf16(donor_path, rows, cols)
    delta = donor - base
    return top_left_direction(delta)


def save_npy(path, vector):
    if path.exists():
        raise ValueError(f"refusing to overwrite {path}")
    np.save(path, np.asarray(vector, dtype="<f4"), allow_pickle=False)
    os.chmod(path, 0o600)


def main():
    parser = argparse.ArgumentParser()
    for name in INPUTS:
        parser.add_argument(name, type=Path)
    parser.add_argument("output", type=Path, help="new or empty artifact directory")
    args = parser.parse_args()
    os.umask(0o077)
    if args.output.exists() and any(args.output.iterdir()):
        raise SystemExit(f"refusing to overwrite non-empty output: {args.output}")

    paths = {name: getattr(args, name) for name in INPUTS}
    for name, details in INPUTS.items():
        path = paths[name]
        actual = sha256(path)
        if actual != details["sha256"]:
            raise SystemExit(f"hash mismatch for {path}: {actual}")

    recovered = {}
    for layer in (56, 70):
        direction, sigma, fraction, residual = recover_pair(
            paths[f"base_layer{layer}"], paths[f"donor_layer{layer}"])
        if fraction < 0.98:
            raise SystemExit(
                f"layer {layer} edit is not rank-one enough: {fraction:.12f}")
        recovered[layer] = {
            "direction": direction,
            "sigma": sigma,
            "rank1_energy_fraction": fraction,
            "relative_rank1_residual": residual,
        }

    cross = float(np.dot(
        recovered[56]["direction"], recovered[70]["direction"]))
    if cross < 0:
        recovered[70]["direction"] *= -1
        cross = -cross
    if cross < 0.9999:
        raise SystemExit(
            f"recovered directions are not globally consistent: cosine={cross:.12f}")
    donor = canonicalize(
        recovered[56]["direction"] + recovered[70]["direction"])

    args.output.mkdir(parents=True, exist_ok=True)
    outputs = {
        "layer56-direction.npy": recovered[56]["direction"],
        "layer70-direction.npy": recovered[70]["direction"],
        "donor-direction.npy": donor,
    }
    for name, vector in outputs.items():
        save_npy(args.output / name, vector)

    source_entries = {}
    for name, details in INPUTS.items():
        repo = BASE_REPO if name.startswith("base_") else DONOR_REPO
        revision = BASE_REVISION if name.startswith("base_") else DONOR_REVISION
        source_entries[name] = {
            **details,
            "tensor": TENSOR_TEMPLATE.format(layer=details["layer"]),
            "byte_range_inclusive": PAYLOAD_RANGE,
            "url": (
                f"https://huggingface.co/{repo}/resolve/{revision}/"
                f"{details['shard']}")
        }
    manifest = {
        "method": (
            "decode exact BF16 payloads; subtract pristine base from public donor; "
            "recover each delta's dominant left singular direction with deterministic "
            "power iteration; sign-align layers 56 and 70; normalize their mean"
        ),
        "shape": [ROWS, COLS],
        "base_repo": BASE_REPO,
        "base_revision": BASE_REVISION,
        "donor_repo": DONOR_REPO,
        "donor_revision": DONOR_REVISION,
        "identical_tensor_index_sha256": INDEX_SHA256,
        "sources": source_entries,
        "layer_metrics": {
            str(layer): {
                "top_singular_value": recovered[layer]["sigma"],
                "rank1_energy_fraction": recovered[layer]["rank1_energy_fraction"],
                "relative_rank1_residual": recovered[layer]["relative_rank1_residual"],
            } for layer in (56, 70)
        },
        "cross_layer_absolute_cosine": cross,
        "artifact_sha256": {
            name: sha256(args.output / name) for name in outputs
        },
    }
    manifest_path = args.output / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    os.chmod(manifest_path, 0o600)
    print(
        "recovered public donor direction: "
        f"energy56={recovered[56]['rank1_energy_fraction']:.12f} "
        f"energy70={recovered[70]['rank1_energy_fraction']:.12f} "
        f"cross={cross:.12f}")
    for name in (*outputs, "manifest.json"):
        print(f"{sha256(args.output / name)}  {name}")


if __name__ == "__main__":
    main()
