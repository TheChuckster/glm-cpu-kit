#!/usr/bin/env python3
"""Geometry-only diagnosis after the rejected K3 v5 SOM bootstrap gate.

This script never loads a model, reads responses, opens holdouts, emits a
control vector, or writes weights.  It compares four preregistered rank-seven
spectral representations of the already captured source activations.
"""

import argparse
import hashlib
import json
import os
import stat
from pathlib import Path

os.environ["OPENBLAS_NUM_THREADS"] = "8"
os.environ["OMP_NUM_THREADS"] = "8"
os.environ["MKL_NUM_THREADS"] = "8"

import numpy as np

from generate_v5_directions import (
    BOOTSTRAP_FRACTION,
    BOOTSTRAP_SEED,
    LAYERS,
    SAMPLES,
    WIDTH,
    fisher_score,
    load_layer,
    require_dependencies,
    validate_capture,
)


METHOD_VERSION = "k3-v5-source-spectral-diagnostic-v1"
EXPECTED_CAPTURE_SHA256 = "9a47478af8370ffe539c14de61f442451cd3240579c902d1e227df0eabd0559f"
RANK = 7
BOOTSTRAP_SEEDS = tuple(range(BOOTSTRAP_SEED, BOOTSTRAP_SEED + 5))
MIN_BOOTSTRAP_PRINCIPAL_COSINE = 0.80
MIN_MEAN_RETENTION = 0.95
VARIANTS = (
    "harmful-residual-raw",
    "harmful-residual-unit",
    "symmetric-contrast-raw",
    "symmetric-contrast-unit",
)


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def unit_rows(values):
    values = np.asarray(values, dtype=np.float32)
    norms = np.linalg.norm(values, axis=1)
    if np.any(~np.isfinite(norms)) or np.any(norms <= 0):
        raise ValueError("zero or non-finite contrast row")
    return values / norms[:, None]


def contrast_matrix(harmful, harmless, variant):
    harmful = np.asarray(harmful, dtype=np.float32)
    harmless = np.asarray(harmless, dtype=np.float32)
    harmful_mean = np.mean(harmful, axis=0, dtype=np.float64).astype(np.float32)
    harmless_mean = np.mean(harmless, axis=0, dtype=np.float64).astype(np.float32)
    harmful_residual = harmful - harmless_mean
    if variant.startswith("harmful-residual-"):
        result = harmful_residual
    elif variant.startswith("symmetric-contrast-"):
        result = np.concatenate(
            (harmful_residual, harmful_mean - harmless), axis=0)
    else:
        raise ValueError(f"unknown spectral variant {variant}")
    if variant.endswith("-unit"):
        result = unit_rows(result)
    return np.ascontiguousarray(result, dtype=np.float32)


def spectral_subspace(matrix, rank=RANK):
    if matrix.ndim != 2 or rank <= 0 or rank > min(matrix.shape):
        raise ValueError("invalid spectral matrix/rank")
    _, singular, right = np.linalg.svd(matrix, full_matrices=False)
    basis = np.asarray(right[:rank], dtype=np.float64)
    orthogonality_error = float(np.max(np.abs(basis @ basis.T - np.eye(rank))))
    if orthogonality_error > 2e-5:
        raise ValueError(f"spectral basis lost orthogonality: {orthogonality_error}")
    energy = np.square(np.asarray(singular, dtype=np.float64))
    return basis, {
        "singular_values": [float(value) for value in singular[:rank]],
        "rank7_energy_fraction": float(np.sum(energy[:rank]) / np.sum(energy)),
        "orthogonality_max_error": orthogonality_error,
    }


def principal_cosines(left, right):
    values = np.linalg.svd(left @ right.T, compute_uv=False)
    return [float(value) for value in values]


def mean_retention(basis, harmful, harmless):
    difference = (
        np.mean(harmful, axis=0, dtype=np.float64)
        - np.mean(harmless, axis=0, dtype=np.float64))
    norm = float(np.linalg.norm(difference))
    if not np.isfinite(norm) or norm <= 0:
        raise ValueError("invalid class-mean contrast")
    direction = difference / norm
    return float(np.linalg.norm(basis @ direction))


def diagnose(harmful, harmless):
    full = {}
    for variant in VARIANTS:
        basis, geometry = spectral_subspace(
            contrast_matrix(harmful, harmless, variant))
        full[variant] = {
            "basis": basis,
            "geometry": geometry,
            "mean_retention": mean_retention(basis, harmful, harmless),
            "bootstraps": [],
        }

    bootstrap_count = int(round(SAMPLES * BOOTSTRAP_FRACTION))
    for seed in BOOTSTRAP_SEEDS:
        rng = np.random.default_rng(seed)
        harmful_indices = np.sort(rng.choice(
            SAMPLES, size=bootstrap_count, replace=False))
        harmless_indices = np.sort(rng.choice(
            SAMPLES, size=bootstrap_count, replace=False))
        for variant in VARIANTS:
            boot_basis, _ = spectral_subspace(contrast_matrix(
                harmful[harmful_indices], harmless[harmless_indices], variant))
            cosines = principal_cosines(full[variant]["basis"], boot_basis)
            full[variant]["bootstraps"].append({
                "seed": seed,
                "principal_cosines": cosines,
                "minimum_principal_cosine": min(cosines),
            })

    results = {}
    for variant in VARIANTS:
        row = full[variant]
        minima = [entry["minimum_principal_cosine"]
                  for entry in row["bootstraps"]]
        results[variant] = {
            "geometry": row["geometry"],
            "mean_retention": row["mean_retention"],
            "bootstraps": row["bootstraps"],
            "minimum_bootstrap_principal_cosine": min(minima),
            "mean_bootstrap_minimum_principal_cosine": float(np.mean(minima)),
            "pass": (min(minima) >= MIN_BOOTSTRAP_PRINCIPAL_COSINE
                     and row["mean_retention"] >= MIN_MEAN_RETENTION),
        }
    selected = max(range(len(VARIANTS)), key=lambda index: (
        results[VARIANTS[index]]["minimum_bootstrap_principal_cosine"],
        results[VARIANTS[index]]["mean_bootstrap_minimum_principal_cosine"],
        results[VARIANTS[index]]["mean_retention"],
        -index,
    ))
    return VARIANTS[selected], results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("activations", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    os.umask(0o077)
    require_dependencies()
    if sha256(args.activations) != EXPECTED_CAPTURE_SHA256:
        raise ValueError("source activation capture hash changed")
    if args.output.exists():
        raise ValueError(f"refusing to overwrite {args.output}")
    if stat.S_IMODE(args.activations.stat().st_mode) & 0o077:
        raise ValueError("source activation capture is not private")

    _, tensors, data_start = validate_capture(args.activations, LAYERS)
    layer_scores = {}
    for layer in LAYERS:
        harmful, harmless = load_layer(args.activations, tensors, data_start, layer)
        layer_scores[str(layer)] = fisher_score(harmful, harmless)
    selected_layer = max(LAYERS, key=lambda layer: (
        layer_scores[str(layer)]["fisher_score"], -layer))
    harmful, harmless = load_layer(
        args.activations, tensors, data_start, selected_layer)
    selected_variant, results = diagnose(harmful, harmless)

    result = {
        "method_version": METHOD_VERSION,
        "source_activation_sha256": EXPECTED_CAPTURE_SHA256,
        "rank": RANK,
        "selected_layer_locked_by_original_fisher_rule": selected_layer,
        "layer_scores": layer_scores,
        "variants_in_locked_tie_order": list(VARIANTS),
        "bootstrap": {
            "fraction": BOOTSTRAP_FRACTION,
            "sample_count_per_class": int(round(SAMPLES * BOOTSTRAP_FRACTION)),
            "seeds": list(BOOTSTRAP_SEEDS),
            "harmful_and_harmless_sampled_independently": True,
        },
        "thresholds": {
            "minimum_bootstrap_principal_cosine": MIN_BOOTSTRAP_PRINCIPAL_COSINE,
            "minimum_class_mean_retention": MIN_MEAN_RETENTION,
        },
        "selection_rule": (
            "maximize worst bootstrap principal cosine, then mean bootstrap "
            "minimum, then class-mean retention, then locked variant order"),
        "selected_variant": selected_variant,
        "variants": results,
        "pass": results[selected_variant]["pass"],
    }
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    os.chmod(args.output, 0o600)
    best = results[selected_variant]
    print(
        f"spectral diagnostic: layer={selected_layer}; variant={selected_variant}; "
        f"bootstrap_min={best['minimum_bootstrap_principal_cosine']:.9f}; "
        f"mean_retention={best['mean_retention']:.9f}; pass={best['pass']}")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError) as exc:
        raise SystemExit(f"FAIL: {exc}") from exc
