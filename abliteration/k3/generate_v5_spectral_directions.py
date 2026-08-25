#!/usr/bin/env python3
"""Generate the preregistered K3 v5-r2 spectral source/Q5 directions."""

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

from analyze_direction import load_vectors
from diagnose_v5_spectral import (
    BOOTSTRAP_SEEDS,
    EXPECTED_CAPTURE_SHA256,
    METHOD_VERSION as DIAGNOSTIC_METHOD_VERSION,
    MIN_BOOTSTRAP_PRINCIPAL_COSINE,
    MIN_MEAN_RETENTION,
    RANK,
    VARIANTS,
    contrast_matrix,
    mean_retention,
    principal_cosines,
    sha256,
    spectral_subspace,
)
from generate_v5_directions import (
    EXPECTED_MINISOM,
    EXPECTED_NUMPY,
    LAYERS,
    SAMPLES,
    WIDTH,
    load_layer,
    require_dependencies,
    validate_capture,
    write_gguf,
)


METHOD_VERSION = "k3-v5-r2-symmetric-spectral-rank7-v1"
EXPECTED_DIAGNOSTIC_SHA256 = "267d841e23036a5db48293d73e2627d444342d14cbc5fef36be489e6937545e2"
SELECTED_LAYER = 61
SELECTED_VARIANT = "symmetric-contrast-unit"
MIN_Q5_PRINCIPAL_COSINE = 0.90


def locked_diagnostic(path):
    if stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise ValueError("spectral diagnostic is not private")
    if sha256(path) != EXPECTED_DIAGNOSTIC_SHA256:
        raise ValueError("spectral diagnostic hash changed")
    report = json.loads(path.read_text())
    selected = report.get("variants", {}).get(SELECTED_VARIANT, {})
    if (report.get("method_version") != DIAGNOSTIC_METHOD_VERSION
            or report.get("source_activation_sha256") != EXPECTED_CAPTURE_SHA256
            or report.get("rank") != RANK
            or report.get("selected_layer_locked_by_original_fisher_rule") != SELECTED_LAYER
            or report.get("selected_variant") != SELECTED_VARIANT
            or report.get("variants_in_locked_tie_order") != list(VARIANTS)
            or report.get("bootstrap", {}).get("seeds") != list(BOOTSTRAP_SEEDS)
            or report.get("thresholds") != {
                "minimum_bootstrap_principal_cosine": MIN_BOOTSTRAP_PRINCIPAL_COSINE,
                "minimum_class_mean_retention": MIN_MEAN_RETENTION,
            }
            or not report.get("pass") or not selected.get("pass")
            or selected.get("minimum_bootstrap_principal_cosine", 0)
            < MIN_BOOTSTRAP_PRINCIPAL_COSINE
            or selected.get("mean_retention", 0) < MIN_MEAN_RETENTION):
        raise ValueError("spectral diagnostic binding changed")
    return report


def orient_basis(basis):
    basis = np.asarray(basis, dtype=np.float64).copy()
    for row in basis:
        pivot = int(np.argmax(np.abs(row)))
        if row[pivot] < 0:
            row *= -1
        if row[pivot] <= 0:
            raise ValueError("could not orient spectral basis")
    return basis


def basis_payload_sha256(basis):
    return hashlib.sha256(
        np.asarray(basis, dtype="<f4").tobytes(order="C")).hexdigest()


def compute_basis(path, required_layers):
    _, tensors, data_start = validate_capture(path, required_layers)
    harmful, harmless = load_layer(path, tensors, data_start, SELECTED_LAYER)
    basis, geometry = spectral_subspace(
        contrast_matrix(harmful, harmless, SELECTED_VARIANT), RANK)
    basis = orient_basis(basis)
    retention = mean_retention(basis, harmful, harmless)
    if retention < MIN_MEAN_RETENTION:
        raise ValueError(
            f"class-mean retention {retention:.12f} < {MIN_MEAN_RETENTION}")
    return basis, geometry, retention


def load_direction_basis(path):
    metadata, vectors = load_vectors(path)
    if (metadata.get("controlvector.model_hint") != "kimi-k3"
            or metadata.get("controlvector.layer_count") != RANK
            or sorted(vectors) != list(range(1, RANK + 1))
            or {len(vector) for vector in vectors.values()} != {WIDTH}):
        raise ValueError("spectral direction metadata changed")
    return np.asarray([vectors[index] for index in range(1, RANK + 1)])


def source_role(args, versions, diagnostic):
    if sha256(args.activations) != EXPECTED_CAPTURE_SHA256:
        raise ValueError("source activation capture hash changed")
    if args.output.exists() and any(args.output.iterdir()):
        raise ValueError(f"refusing to overwrite non-empty {args.output}")
    basis, geometry, retention = compute_basis(args.activations, LAYERS)
    args.output.mkdir(parents=True, exist_ok=True)
    direction_path = args.output / "source.gguf"
    write_gguf(direction_path, basis, args.gguf_py)
    manifest = {
        "method_version": METHOD_VERSION,
        "role": "source",
        "dependencies": versions,
        "diagnostic_sha256": EXPECTED_DIAGNOSTIC_SHA256,
        "diagnostic_method_version": DIAGNOSTIC_METHOD_VERSION,
        "source_activation_sha256": EXPECTED_CAPTURE_SHA256,
        "selected_layer": SELECTED_LAYER,
        "selected_variant": SELECTED_VARIANT,
        "matrix_shape": [2 * SAMPLES, WIDTH],
        "rank": RANK,
        "sign_orientation": "largest-absolute coordinate positive; lower coordinate wins numpy argmax ties",
        "basis_payload_sha256": basis_payload_sha256(basis),
        "geometry": geometry,
        "class_mean_retention": retention,
        "source_bootstrap": diagnostic["variants"][SELECTED_VARIANT]["bootstraps"],
        "minimum_source_bootstrap_principal_cosine": diagnostic[
            "variants"][SELECTED_VARIANT]["minimum_bootstrap_principal_cosine"],
        "thresholds": {
            "minimum_source_bootstrap_principal_cosine": MIN_BOOTSTRAP_PRINCIPAL_COSINE,
            "minimum_class_mean_retention": MIN_MEAN_RETENTION,
            "minimum_q5_principal_cosine": MIN_Q5_PRINCIPAL_COSINE,
        },
        "artifact_sha256": {"source.gguf": sha256(direction_path)},
    }
    manifest_path = args.output / "source.manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    os.chmod(manifest_path, 0o600)
    print(
        f"source spectral direction: layer={SELECTED_LAYER}; rank={RANK}; "
        f"bootstrap_min={manifest['minimum_source_bootstrap_principal_cosine']:.9f}; "
        f"mean_retention={retention:.9f}")


def q5_role(args, versions, _diagnostic):
    source_manifest_path = args.output / "source.manifest.json"
    source_path = args.output / "source.gguf"
    q5_path = args.output / "q5.gguf"
    q5_manifest_path = args.output / "q5.manifest.json"
    if not source_manifest_path.is_file() or not source_path.is_file():
        raise ValueError("source spectral directions must be generated first")
    if q5_path.exists() or q5_manifest_path.exists():
        raise ValueError("refusing to overwrite Q5 spectral direction artifacts")
    source_manifest = json.loads(source_manifest_path.read_text())
    if (source_manifest.get("method_version") != METHOD_VERSION
            or source_manifest.get("role") != "source"
            or source_manifest.get("dependencies") != versions
            or source_manifest.get("diagnostic_sha256") != EXPECTED_DIAGNOSTIC_SHA256
            or source_manifest.get("artifact_sha256", {}).get("source.gguf")
            != sha256(source_path)):
        raise ValueError("source spectral manifest binding changed")

    q5_basis, geometry, retention = compute_basis(
        args.activations, (SELECTED_LAYER,))
    source_basis = load_direction_basis(source_path)
    cosines = principal_cosines(source_basis, q5_basis)
    if min(cosines) < MIN_Q5_PRINCIPAL_COSINE:
        raise ValueError(
            f"Q5 minimum principal cosine {min(cosines):.12f} "
            f"< {MIN_Q5_PRINCIPAL_COSINE}")
    write_gguf(q5_path, q5_basis, args.gguf_py)
    manifest = {
        "method_version": METHOD_VERSION,
        "role": "q5-diagnostic",
        "dependencies": versions,
        "diagnostic_sha256": EXPECTED_DIAGNOSTIC_SHA256,
        "source_manifest_sha256": sha256(source_manifest_path),
        "source_direction_sha256": sha256(source_path),
        "q5_activation_sha256": sha256(args.activations),
        "selected_layer_locked_from_source": SELECTED_LAYER,
        "selected_variant_locked_from_source": SELECTED_VARIANT,
        "matrix_shape": [2 * SAMPLES, WIDTH],
        "rank": RANK,
        "basis_payload_sha256": basis_payload_sha256(q5_basis),
        "geometry": geometry,
        "class_mean_retention": retention,
        "principal_cosines_to_source": cosines,
        "minimum_required_principal_cosine": MIN_Q5_PRINCIPAL_COSINE,
        "artifact_sha256": {"q5.gguf": sha256(q5_path)},
    }
    q5_manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    os.chmod(q5_manifest_path, 0o600)
    print(
        f"Q5 spectral direction: layer={SELECTED_LAYER}; rank={RANK}; "
        f"principal_min={min(cosines):.9f}; mean_retention={retention:.9f}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("role", choices=("source", "q5"))
    parser.add_argument("activations", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--diagnostic", type=Path, required=True)
    parser.add_argument("--gguf-py", type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    versions = require_dependencies()
    diagnostic = locked_diagnostic(args.diagnostic)
    if args.role == "source":
        source_role(args, versions, diagnostic)
    else:
        q5_role(args, versions, diagnostic)


if __name__ == "__main__":
    try:
        main()
    except (EOFError, ImportError, OSError, ValueError, json.JSONDecodeError) as exc:
        raise SystemExit(f"FAIL: {exc}") from exc
