#!/usr/bin/env python3
"""Fail-closed verifier for K3 v5 source/bootstrap/Q5 SOM directions."""

import argparse
import hashlib
import json
import math
import stat
from pathlib import Path

import numpy as np

from analyze_direction import load_vectors
from generate_v5_directions import (
    BOOTSTRAP_FRACTION,
    BOOTSTRAP_SEED,
    DIRECTION_COUNT,
    EXPECTED_MINISOM,
    EXPECTED_NUMPY,
    LAYERS,
    METHOD_VERSION,
    MIN_BOOTSTRAP_PRINCIPAL_COSINE,
    MIN_Q5_PRINCIPAL_COSINE,
    MIN_SINGULAR_VALUE,
    SAMPLES,
    WIDTH,
    direction_geometry,
    principal_cosines,
)


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_direction(path):
    if stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise ValueError(f"direction is not private: {path}")
    metadata, vectors = load_vectors(path)
    if (metadata.get("controlvector.model_hint") != "kimi-k3"
            or metadata.get("controlvector.layer_count") != DIRECTION_COUNT
            or sorted(vectors) != list(range(1, DIRECTION_COUNT + 1))
            or {len(vector) for vector in vectors.values()} != {WIDTH}):
        raise ValueError(f"direction metadata changed: {path}")
    return np.asarray([vectors[index] for index in range(1, DIRECTION_COUNT + 1)])


def close_list(left, right, tolerance=2e-6):
    return (len(left) == len(right)
            and max(abs(float(a) - float(b)) for a, b in zip(left, right)) <= tolerance)


def check_selection(counts, selected, expected_total, label):
    if (not isinstance(counts, list) or len(counts) != 16
            or sum(counts) != expected_total
            or not isinstance(selected, list) or len(selected) != DIRECTION_COUNT
            or len({row.get("flat_index") for row in selected}) != DIRECTION_COUNT):
        raise ValueError(f"{label} cluster selection changed")
    for row in selected:
        index = row.get("flat_index")
        if (not isinstance(index, int) or not 0 <= index < 16
                or row.get("coordinate") != [index // 4, index % 4]
                or row.get("harmful_wins") != counts[index]
                or counts[index] <= 0):
            raise ValueError(f"{label} selected-neuron binding changed")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("direction_dir", type=Path)
    parser.add_argument("source_activations", type=Path)
    parser.add_argument("q5_activations", type=Path)
    parser.add_argument("--json", type=Path)
    args = parser.parse_args()

    core_names = {
        "train.gguf", "bootstrap.gguf", "q5.gguf",
        "train.manifest.json", "q5.manifest.json",
    }
    names = {path.name for path in args.direction_dir.iterdir() if path.is_file()}
    if names not in (core_names, core_names | {"verification.json"}):
        raise ValueError(f"direction artifact names changed: {sorted(names)}")
    for name in names:
        if stat.S_IMODE((args.direction_dir / name).stat().st_mode) & 0o077:
            raise ValueError(f"direction artifact is not private: {name}")

    train_manifest_path = args.direction_dir / "train.manifest.json"
    q5_manifest_path = args.direction_dir / "q5.manifest.json"
    train_manifest = json.loads(train_manifest_path.read_text())
    q5_manifest = json.loads(q5_manifest_path.read_text())
    versions = {"numpy": EXPECTED_NUMPY, "minisom": EXPECTED_MINISOM}
    if (train_manifest.get("method_version") != METHOD_VERSION
            or train_manifest.get("role") != "source"
            or train_manifest.get("dependencies") != versions):
        raise ValueError("train method/dependency binding changed")
    if (q5_manifest.get("method_version") != METHOD_VERSION
            or q5_manifest.get("role") != "q5-diagnostic"
            or q5_manifest.get("dependencies") != versions):
        raise ValueError("Q5 method/dependency binding changed")
    official = train_manifest.get("official_method", {})
    if (official.get("repository") != "https://github.com/pralab/som-refusal-directions"
            or official.get("commit") != "d244c7d282ac65a1520bef0d418615ef148108af"
            or official.get("license") != "MIT"):
        raise ValueError("official SOM source binding changed")
    selected_layer = train_manifest.get("k3_adaptation", {}).get("selected_layer")
    if selected_layer not in LAYERS:
        raise ValueError("selected layer is outside the locked band")
    if q5_manifest.get("selected_layer_locked_from_source") != selected_layer:
        raise ValueError("Q5 did not reuse the source-selected layer")
    if (train_manifest.get("activation_shape") != [SAMPLES, WIDTH]
            or train_manifest.get("k3_adaptation", {}).get("direction_count") != DIRECTION_COUNT):
        raise ValueError("train shape/direction count changed")
    if set(train_manifest.get("layer_scores", {})) != {
            str(layer) for layer in LAYERS}:
        raise ValueError("layer-score set changed")
    recomputed_layer = max(LAYERS, key=lambda layer: (
        train_manifest["layer_scores"][str(layer)]["fisher_score"], -layer))
    if recomputed_layer != selected_layer:
        raise ValueError("selected layer does not maximize the locked Fisher score")

    source_hash = sha256(args.source_activations)
    q5_hash = sha256(args.q5_activations)
    if train_manifest.get("activation_capture_sha256") != source_hash:
        raise ValueError("source activation capture binding changed")
    if q5_manifest.get("activation_capture_sha256") != q5_hash:
        raise ValueError("Q5 activation capture binding changed")
    if (q5_manifest.get("train_manifest_sha256") != sha256(train_manifest_path)
            or q5_manifest.get("train_direction_sha256")
            != train_manifest.get("artifact_sha256", {}).get("train.gguf")):
        raise ValueError("Q5-to-train binding changed")

    paths = {name: args.direction_dir / f"{name}.gguf"
             for name in ("train", "bootstrap", "q5")}
    for name, path in paths.items():
        expected = (q5_manifest if name == "q5" else train_manifest).get(
            "artifact_sha256", {}).get(f"{name}.gguf")
        if sha256(path) != expected:
            raise ValueError(f"{name} direction artifact hash changed")
    vectors = {name: load_direction(path) for name, path in paths.items()}
    geometry = {name: direction_geometry(value) for name, value in vectors.items()}
    for name in geometry:
        recorded = (q5_manifest.get("geometry") if name == "q5"
                    else train_manifest.get("train_geometry") if name == "train"
                    else train_manifest.get("bootstrap", {}).get("geometry"))
        if (not recorded
                or abs(geometry[name]["minimum_singular_value"]
                       - recorded["minimum_singular_value"]) > 2e-6):
            raise ValueError(f"{name} recorded geometry changed")

    bootstrap = principal_cosines(vectors["train"], vectors["bootstrap"])
    q5 = principal_cosines(vectors["train"], vectors["q5"])
    recorded_bootstrap = train_manifest.get("bootstrap", {})
    if (recorded_bootstrap.get("seed") != BOOTSTRAP_SEED
            or recorded_bootstrap.get("fraction") != BOOTSTRAP_FRACTION
            or recorded_bootstrap.get("sample_count") != round(SAMPLES * BOOTSTRAP_FRACTION)
            or not close_list(bootstrap, recorded_bootstrap.get("principal_cosines_to_full", []))
            or min(bootstrap) < MIN_BOOTSTRAP_PRINCIPAL_COSINE):
        raise ValueError("bootstrap stability gate failed")
    if (not close_list(q5, q5_manifest.get("principal_cosines_to_source", []))
            or min(q5) < MIN_Q5_PRINCIPAL_COSINE):
        raise ValueError("Q5 stability gate failed")
    check_selection(
        train_manifest.get("cluster_counts"),
        train_manifest.get("selected_neurons"), SAMPLES, "train")
    check_selection(
        q5_manifest.get("cluster_counts"),
        q5_manifest.get("selected_neurons"), SAMPLES, "q5")
    check_selection(
        recorded_bootstrap.get("cluster_counts"),
        recorded_bootstrap.get("selected_neurons"),
        recorded_bootstrap["sample_count"], "bootstrap")

    result = {
        "method_version": METHOD_VERSION,
        "selected_layer": selected_layer,
        "source_activation_sha256": source_hash,
        "q5_activation_sha256": q5_hash,
        "direction_sha256": {name: sha256(path) for name, path in paths.items()},
        "minimum_singular_value": {
            name: geometry[name]["minimum_singular_value"] for name in geometry},
        "bootstrap_principal_cosines": bootstrap,
        "q5_principal_cosines": q5,
        "thresholds": {
            "minimum_singular_value": MIN_SINGULAR_VALUE,
            "bootstrap_principal_cosine": MIN_BOOTSTRAP_PRINCIPAL_COSINE,
            "q5_principal_cosine": MIN_Q5_PRINCIPAL_COSINE,
        },
        "pass": True,
    }
    print(
        f"PASS: v5 SOM rank-7 directions; layer={selected_layer}; "
        f"singular min={min(value['minimum_singular_value'] for value in geometry.values()):.9f}; "
        f"bootstrap principal min={min(bootstrap):.9f}; Q5 principal min={min(q5):.9f}")
    if args.json:
        args.json.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    try:
        main()
    except (EOFError, OSError, ValueError, json.JSONDecodeError) as exc:
        raise SystemExit(f"FAIL: {exc}") from exc
