#!/usr/bin/env python3
"""Fail-closed verifier for K3 v5-r2 source and Q5 spectral directions."""

import argparse
import json
import os
import stat
from pathlib import Path

os.environ["OPENBLAS_NUM_THREADS"] = "8"
os.environ["OMP_NUM_THREADS"] = "8"
os.environ["MKL_NUM_THREADS"] = "8"

import numpy as np

from diagnose_v5_spectral import (
    MIN_BOOTSTRAP_PRINCIPAL_COSINE,
    MIN_MEAN_RETENTION,
    principal_cosines,
    sha256,
)
from generate_v5_directions import LAYERS, require_dependencies
from generate_v5_spectral_directions import (
    EXPECTED_CAPTURE_SHA256,
    EXPECTED_DIAGNOSTIC_SHA256,
    METHOD_VERSION,
    MIN_Q5_PRINCIPAL_COSINE,
    RANK,
    SELECTED_LAYER,
    SELECTED_VARIANT,
    basis_payload_sha256,
    compute_basis,
    load_direction_basis,
    locked_diagnostic,
    require_spectral_dependencies,
)


def close_list(left, right, tolerance=3e-6):
    return (len(left) == len(right)
            and max(abs(float(a) - float(b)) for a, b in zip(left, right)) <= tolerance)


def check_basis(path, recomputed):
    if stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise ValueError(f"direction is not private: {path}")
    loaded = load_direction_basis(path)
    normalized = np.asarray(recomputed, dtype=np.float32).astype(np.float64)
    normalized /= np.linalg.norm(normalized, axis=1)[:, None]
    maximum_error = float(np.max(np.abs(loaded - normalized)))
    orthogonality_error = float(np.max(
        np.abs(loaded @ loaded.T - np.eye(RANK))))
    if maximum_error > 3e-6 or orthogonality_error > 3e-6:
        raise ValueError(
            f"direction basis changed: max={maximum_error}, orth={orthogonality_error}")
    return loaded, maximum_error, orthogonality_error


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("direction_dir", type=Path)
    parser.add_argument("source_activations", type=Path)
    parser.add_argument("q5_activations", type=Path)
    parser.add_argument("diagnostic", type=Path)
    parser.add_argument("--json", type=Path)
    args = parser.parse_args()
    os.umask(0o077)
    versions = require_spectral_dependencies()
    diagnostic = locked_diagnostic(args.diagnostic)
    core_names = {
        "source.gguf", "q5.gguf", "source.manifest.json", "q5.manifest.json"}
    names = {path.name for path in args.direction_dir.iterdir() if path.is_file()}
    if names not in (core_names, core_names | {"verification.json"}):
        raise ValueError(f"direction artifact names changed: {sorted(names)}")
    for name in names:
        if stat.S_IMODE((args.direction_dir / name).stat().st_mode) & 0o077:
            raise ValueError(f"direction artifact is not private: {name}")

    source_manifest_path = args.direction_dir / "source.manifest.json"
    q5_manifest_path = args.direction_dir / "q5.manifest.json"
    source_path = args.direction_dir / "source.gguf"
    q5_path = args.direction_dir / "q5.gguf"
    source_manifest = json.loads(source_manifest_path.read_text())
    q5_manifest = json.loads(q5_manifest_path.read_text())
    expected_dependencies = {
        "numpy": "2.2.4", "minisom": "2.3.5",
        "pyyaml": "6.0.2", "tqdm": "4.67.1"}
    if versions != expected_dependencies:
        raise ValueError("installed dependency versions changed")
    if (source_manifest.get("method_version") != METHOD_VERSION
            or source_manifest.get("role") != "source"
            or source_manifest.get("dependencies") != expected_dependencies
            or source_manifest.get("diagnostic_sha256") != EXPECTED_DIAGNOSTIC_SHA256
            or source_manifest.get("source_activation_sha256") != EXPECTED_CAPTURE_SHA256
            or source_manifest.get("selected_layer") != SELECTED_LAYER
            or source_manifest.get("selected_variant") != SELECTED_VARIANT
            or source_manifest.get("rank") != RANK
            or source_manifest.get("artifact_sha256", {}).get("source.gguf")
            != sha256(source_path)):
        raise ValueError("source spectral manifest changed")
    if (q5_manifest.get("method_version") != METHOD_VERSION
            or q5_manifest.get("role") != "q5-diagnostic"
            or q5_manifest.get("dependencies") != expected_dependencies
            or q5_manifest.get("diagnostic_sha256") != EXPECTED_DIAGNOSTIC_SHA256
            or q5_manifest.get("source_manifest_sha256") != sha256(source_manifest_path)
            or q5_manifest.get("source_direction_sha256") != sha256(source_path)
            or q5_manifest.get("q5_activation_sha256") != sha256(args.q5_activations)
            or q5_manifest.get("selected_layer_locked_from_source") != SELECTED_LAYER
            or q5_manifest.get("selected_variant_locked_from_source") != SELECTED_VARIANT
            or q5_manifest.get("rank") != RANK
            or q5_manifest.get("artifact_sha256", {}).get("q5.gguf")
            != sha256(q5_path)):
        raise ValueError("Q5 spectral manifest changed")
    if sha256(args.source_activations) != EXPECTED_CAPTURE_SHA256:
        raise ValueError("source activation capture changed")

    source_recomputed, _, source_retention = compute_basis(
        args.source_activations, LAYERS)
    q5_recomputed, _, q5_retention = compute_basis(
        args.q5_activations, (SELECTED_LAYER,))
    source_basis, source_error, source_orthogonality = check_basis(
        source_path, source_recomputed)
    q5_basis, q5_error, q5_orthogonality = check_basis(q5_path, q5_recomputed)
    cosines = principal_cosines(source_basis, q5_basis)
    recorded_cosines = q5_manifest.get("principal_cosines_to_source", [])
    source_bootstraps = diagnostic["variants"][SELECTED_VARIANT]["bootstraps"]
    source_minimum = min(
        row["minimum_principal_cosine"] for row in source_bootstraps)
    if (not close_list(cosines, recorded_cosines)
            or min(cosines) < MIN_Q5_PRINCIPAL_COSINE
            or source_minimum < MIN_BOOTSTRAP_PRINCIPAL_COSINE
            or abs(source_retention - source_manifest.get("class_mean_retention", -1)) > 3e-6
            or abs(q5_retention - q5_manifest.get("class_mean_retention", -1)) > 3e-6
            or source_retention < MIN_MEAN_RETENTION
            or q5_retention < MIN_MEAN_RETENTION
            or source_manifest.get("basis_payload_sha256")
            != basis_payload_sha256(source_recomputed)
            or q5_manifest.get("basis_payload_sha256")
            != basis_payload_sha256(q5_recomputed)):
        raise ValueError("spectral geometry gate failed")

    result = {
        "method_version": METHOD_VERSION,
        "diagnostic_sha256": EXPECTED_DIAGNOSTIC_SHA256,
        "source_activation_sha256": EXPECTED_CAPTURE_SHA256,
        "q5_activation_sha256": sha256(args.q5_activations),
        "direction_sha256": {
            "source": sha256(source_path), "q5": sha256(q5_path)},
        "selected_layer": SELECTED_LAYER,
        "selected_variant": SELECTED_VARIANT,
        "rank": RANK,
        "source_bootstrap_minimum_principal_cosine": source_minimum,
        "q5_principal_cosines": cosines,
        "source_class_mean_retention": source_retention,
        "q5_class_mean_retention": q5_retention,
        "maximum_basis_error": {"source": source_error, "q5": q5_error},
        "orthogonality_max_error": {
            "source": source_orthogonality, "q5": q5_orthogonality},
        "pass": True,
    }
    print(
        f"PASS: v5-r2 spectral rank-{RANK}; layer={SELECTED_LAYER}; "
        f"bootstrap_min={source_minimum:.9f}; Q5_min={min(cosines):.9f}; "
        f"mean_retention={min(source_retention, q5_retention):.9f}")
    if args.json:
        args.json.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
        os.chmod(args.json, 0o600)


if __name__ == "__main__":
    try:
        main()
    except (EOFError, ImportError, OSError, ValueError, json.JSONDecodeError) as exc:
        raise SystemExit(f"FAIL: {exc}") from exc
