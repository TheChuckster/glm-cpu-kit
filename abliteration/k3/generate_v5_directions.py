#!/usr/bin/env python3
"""Generate K3 v5 multi-direction refusal manifolds from raw GGUF activations.

This is a CPU adaptation of Piras et al.'s official SOM method. It keeps their
4x4 map and training hyperparameters, chooses one K3 layer by a pre-behavior
Fisher separation score, and replaces their costly behavioral Bayesian search
with deterministic support-weighted pivoted QR selecting the paper's maximum
seven directions.
"""

import argparse
import hashlib
import importlib.metadata
import json
import math
import os
import stat
import struct
import sys
from pathlib import Path

os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["OMP_NUM_THREADS"] = "1"
os.environ["MKL_NUM_THREADS"] = "1"

import numpy as np
from minisom import MiniSom


METHOD_VERSION = "k3-v5-som-4x4-pivot7-v1"
EXPECTED_NUMPY = "2.2.4"
EXPECTED_MINISOM = "2.3.5"
WIDTH = 7168
SAMPLES = 359
LAYERS = tuple(range(56, 74))
SOM_X = 4
SOM_Y = 4
SOM_SIGMA = 0.33
SOM_LEARNING_RATE = 0.01
SOM_ITERATIONS = 10000
SOM_SEED = 0
DIRECTION_COUNT = 7
BOOTSTRAP_SEED = 20260827
BOOTSTRAP_FRACTION = 0.80
MIN_SINGULAR_VALUE = 0.05
MIN_BOOTSTRAP_PRINCIPAL_COSINE = 0.80
MIN_Q5_PRINCIPAL_COSINE = 0.90

U8, I8, U16, I16, U32, I32, F32, BOOL, STR, ARR, U64, I64, F64 = range(13)
FORMATS = {U8: "<B", I8: "<b", U16: "<H", I16: "<h", U32: "<I",
           I32: "<i", F32: "<f", BOOL: "<?", U64: "<Q", I64: "<q", F64: "<d"}


class Reader:
    def __init__(self, source):
        self.source = source

    def raw(self, count):
        value = self.source.read(count)
        if len(value) != count:
            raise EOFError("truncated GGUF")
        return value

    def scalar(self, kind):
        fmt = FORMATS.get(kind)
        if fmt is None:
            raise ValueError(f"unsupported GGUF scalar type {kind}")
        return struct.unpack(fmt, self.raw(struct.calcsize(fmt)))[0]

    def string(self):
        return self.raw(self.scalar(U64)).decode("utf-8")

    def value(self, kind):
        if kind == STR:
            return self.string()
        if kind == ARR:
            item_kind = self.scalar(U32)
            return [self.value(item_kind) for _ in range(self.scalar(U64))]
        return self.scalar(kind)


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def dependency_versions():
    return {
        "numpy": np.__version__,
        "minisom": importlib.metadata.version("MiniSom"),
    }


def require_dependencies():
    versions = dependency_versions()
    expected = {"numpy": EXPECTED_NUMPY, "minisom": EXPECTED_MINISOM}
    if versions != expected:
        raise ValueError(f"dependency versions {versions} != locked {expected}")
    return versions


def activation_index(path):
    with path.open("rb") as source:
        reader = Reader(source)
        if reader.raw(4) != b"GGUF":
            raise ValueError(f"{path} is not GGUF")
        version = reader.scalar(U32)
        tensor_count = reader.scalar(U64)
        kv_count = reader.scalar(U64)
        if version not in (2, 3):
            raise ValueError(f"unsupported GGUF version {version}")
        metadata = {}
        for _ in range(kv_count):
            key = reader.string()
            metadata[key] = reader.value(reader.scalar(U32))
        tensors = {}
        for _ in range(tensor_count):
            name = reader.string()
            shape = [reader.scalar(U64) for _ in range(reader.scalar(U32))]
            tensor_type = reader.scalar(U32)
            offset = reader.scalar(U64)
            if name in tensors:
                raise ValueError(f"duplicate tensor {name}")
            tensors[name] = {"shape": shape, "type": tensor_type, "offset": offset}
        alignment = int(metadata.get("general.alignment", 32))
        data_start = (source.tell() + alignment - 1) // alignment * alignment
    return metadata, tensors, data_start


def validate_capture(path, required_layers):
    if stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise ValueError(f"activation capture is not private: {path}")
    metadata, tensors, data_start = activation_index(path)
    expected_names = {f"{label}.{layer}" for layer in required_layers
                      for label in ("positive", "negative")}
    expected_spec = (f"{required_layers[0]}-{required_layers[-1]}"
                     if len(required_layers) > 1
                     and tuple(required_layers) == tuple(range(
                         required_layers[0], required_layers[-1] + 1))
                     else ",".join(str(layer) for layer in required_layers))
    if (metadata.get("general.architecture") != "activationcapture"
            or metadata.get("activationcapture.model_hint") != "kimi-k3"
            or metadata.get("activationcapture.method") != "final-templated-prompt-position"
            or metadata.get("activationcapture.layer_spec") != expected_spec
            or metadata.get("activationcapture.sample_count") != SAMPLES
            or metadata.get("activationcapture.tensor_count") != len(expected_names)):
        raise ValueError("activation metadata changed")
    if set(tensors) != expected_names:
        raise ValueError(f"activation tensors changed: {sorted(tensors)}")
    for name, tensor in tensors.items():
        if tensor["type"] != 0 or tensor["shape"] != [WIDTH, SAMPLES]:
            raise ValueError(f"invalid activation tensor {name}: {tensor}")
        end = data_start + tensor["offset"] + WIDTH * SAMPLES * 4
        if end > path.stat().st_size:
            raise ValueError(f"activation tensor exceeds file: {name}")
    return metadata, tensors, data_start


def load_layer(path, tensors, data_start, layer):
    arrays = []
    for label in ("positive", "negative"):
        tensor = tensors[f"{label}.{layer}"]
        arrays.append(np.memmap(
            path, mode="r", dtype="<f4",
            offset=data_start + tensor["offset"], shape=(SAMPLES, WIDTH)))
    return tuple(arrays)


def fisher_score(harmful, harmless):
    harmful_mean = np.mean(harmful, axis=0, dtype=np.float64)
    harmless_mean = np.mean(harmless, axis=0, dtype=np.float64)
    separation = float(np.linalg.norm(harmful_mean - harmless_mean))
    harmful_centered = np.asarray(harmful, dtype=np.float64) - harmful_mean
    harmless_centered = np.asarray(harmless, dtype=np.float64) - harmless_mean
    within_squared = 0.5 * (
        float(np.mean(np.einsum("ij,ij->i", harmful_centered, harmful_centered)))
        + float(np.mean(np.einsum("ij,ij->i", harmless_centered, harmless_centered))))
    score = separation / math.sqrt(within_squared)
    if not math.isfinite(score) or score <= 0:
        raise ValueError("invalid Fisher separation score")
    return {
        "centroid_distance": separation,
        "pooled_within_rms": math.sqrt(within_squared),
        "fisher_score": score,
    }


def normalized_rows(values):
    values = np.asarray(values, dtype=np.float64)
    norms = np.linalg.norm(values, axis=1)
    if np.any(~np.isfinite(norms)) or np.any(norms == 0):
        raise ValueError("zero or non-finite direction")
    return values / norms[:, None]


def train_som_directions(harmful, harmless):
    harmful = np.asarray(harmful, dtype=np.float32)
    harmless = np.asarray(harmless, dtype=np.float32)
    som = MiniSom(
        SOM_X, SOM_Y, WIDTH, sigma=SOM_SIGMA,
        learning_rate=SOM_LEARNING_RATE, random_seed=SOM_SEED,
        activation_distance="euclidean", topology="hexagonal")
    som.random_weights_init(harmful)
    som.train_random(harmful, SOM_ITERATIONS)
    weights = som.get_weights().reshape(SOM_X * SOM_Y, WIDTH)
    harmless_centroid = np.mean(harmless, axis=0, dtype=np.float64)
    directions = normalized_rows(weights - harmless_centroid)
    counts = np.zeros(SOM_X * SOM_Y, dtype=np.int64)
    for row in harmful:
        x, y = som.winner(row)
        counts[x * SOM_Y + y] += 1
    return directions, counts


def select_supported_pivots(directions, counts, count=DIRECTION_COUNT):
    directions = normalized_rows(directions)
    counts = np.asarray(counts, dtype=np.int64)
    if len(directions) != SOM_X * SOM_Y or counts.shape != (SOM_X * SOM_Y,):
        raise ValueError("SOM direction/count shape changed")
    if np.count_nonzero(counts) < count:
        raise ValueError(f"only {np.count_nonzero(counts)} occupied SOM neurons")
    max_count = int(np.max(counts))
    selected = []
    basis = []
    for _ in range(count):
        best = None
        for index, direction in enumerate(directions):
            if index in selected or counts[index] == 0:
                continue
            residual = direction.copy()
            for vector in basis:
                residual -= np.dot(residual, vector) * vector
            novelty = float(np.linalg.norm(residual))
            score = novelty * math.sqrt(int(counts[index]) / max_count)
            candidate = (score, int(counts[index]), -index)
            if best is None or candidate > best[0]:
                best = (candidate, index, residual)
        if best is None or best[0][0] <= 1e-10:
            raise ValueError("SOM directions lost numerical rank during pivot selection")
        _, index, residual = best
        for vector in basis:  # second pass for deterministic reorthogonalization
            residual -= np.dot(residual, vector) * vector
        residual_norm = float(np.linalg.norm(residual))
        if residual_norm <= 1e-10:
            raise ValueError("selected SOM pivot is numerically dependent")
        basis.append(residual / residual_norm)
        selected.append(index)
    return selected


def direction_geometry(directions):
    directions = normalized_rows(directions)
    singular = np.linalg.svd(directions, compute_uv=False)
    mean = np.sum(directions, axis=0)
    mean /= np.linalg.norm(mean)
    cosines = directions @ mean
    result = {
        "singular_values": [float(value) for value in singular],
        "minimum_singular_value": float(np.min(singular)),
        "cosine_to_selected_mean": {
            "minimum": float(np.min(cosines)),
            "median": float(np.median(cosines)),
            "maximum": float(np.max(cosines)),
        },
    }
    if result["minimum_singular_value"] < MIN_SINGULAR_VALUE:
        raise ValueError(
            f"selected direction minimum singular value "
            f"{result['minimum_singular_value']:.12f} < {MIN_SINGULAR_VALUE}")
    if result["cosine_to_selected_mean"]["minimum"] <= 0:
        raise ValueError("selected direction opposes the selected mean")
    return result


def principal_cosines(left, right):
    left_basis, _ = np.linalg.qr(np.asarray(left, dtype=np.float64).T, mode="reduced")
    right_basis, _ = np.linalg.qr(np.asarray(right, dtype=np.float64).T, mode="reduced")
    return [float(value) for value in
            np.linalg.svd(left_basis.T @ right_basis, compute_uv=False)]


def write_gguf(path, directions, gguf_py):
    sys.path.insert(0, str(gguf_py))
    try:
        from gguf import GGUFWriter
    except ImportError as exc:
        raise ValueError(f"cannot import GGUFWriter from {gguf_py}") from exc
    writer = GGUFWriter(path, "controlvector")
    writer.add_string("controlvector.model_hint", "kimi-k3")
    writer.add_uint32("controlvector.layer_count", len(directions))
    for index, direction in enumerate(directions, 1):
        writer.add_tensor(f"direction.{index}", np.asarray(direction, dtype="<f4"))
    writer.write_header_to_file()
    writer.write_kv_data_to_file()
    writer.write_tensors_to_file()
    writer.close()
    os.chmod(path, 0o600)


def selected_metadata(selected, counts):
    return [{
        "flat_index": int(index),
        "coordinate": [int(index // SOM_Y), int(index % SOM_Y)],
        "harmful_wins": int(counts[index]),
    } for index in selected]


def source_role(args, versions):
    if args.output.exists() and any(args.output.iterdir()):
        raise ValueError(f"refusing to overwrite non-empty {args.output}")
    _, tensors, data_start = validate_capture(args.activations, LAYERS)
    layer_scores = {}
    for layer in LAYERS:
        harmful, harmless = load_layer(args.activations, tensors, data_start, layer)
        layer_scores[str(layer)] = fisher_score(harmful, harmless)
    selected_layer = max(LAYERS, key=lambda layer: (
        layer_scores[str(layer)]["fisher_score"], -layer))

    harmful, harmless = load_layer(
        args.activations, tensors, data_start, selected_layer)
    all_directions, counts = train_som_directions(harmful, harmless)
    selected = select_supported_pivots(all_directions, counts)
    train_directions = all_directions[selected]
    train_geometry = direction_geometry(train_directions)

    bootstrap_count = int(round(SAMPLES * BOOTSTRAP_FRACTION))
    rng = np.random.default_rng(BOOTSTRAP_SEED)
    bootstrap_indices = np.sort(rng.choice(
        SAMPLES, size=bootstrap_count, replace=False))
    boot_all, boot_counts = train_som_directions(
        harmful[bootstrap_indices], harmless[bootstrap_indices])
    boot_selected = select_supported_pivots(boot_all, boot_counts)
    boot_directions = boot_all[boot_selected]
    boot_geometry = direction_geometry(boot_directions)
    boot_cosines = principal_cosines(train_directions, boot_directions)
    if min(boot_cosines) < MIN_BOOTSTRAP_PRINCIPAL_COSINE:
        raise ValueError(
            f"bootstrap minimum principal cosine {min(boot_cosines):.12f} "
            f"< {MIN_BOOTSTRAP_PRINCIPAL_COSINE}")

    args.output.mkdir(parents=True, exist_ok=True)
    train_path = args.output / "train.gguf"
    boot_path = args.output / "bootstrap.gguf"
    write_gguf(train_path, train_directions, args.gguf_py)
    write_gguf(boot_path, boot_directions, args.gguf_py)
    manifest = {
        "method_version": METHOD_VERSION,
        "role": "source",
        "dependencies": versions,
        "official_method": {
            "repository": "https://github.com/pralab/som-refusal-directions",
            "commit": "d244c7d282ac65a1520bef0d418615ef148108af",
            "license": "MIT",
            "som": {
                "grid": [SOM_X, SOM_Y], "topology": "hexagonal",
                "activation_distance": "euclidean", "sigma": SOM_SIGMA,
                "learning_rate": SOM_LEARNING_RATE,
                "iterations": SOM_ITERATIONS, "random_seed": SOM_SEED,
            },
        },
        "k3_adaptation": {
            "candidate_layers": list(LAYERS),
            "layer_selection": "maximum pre-behavior centroid Fisher score; lower layer breaks an exact tie",
            "selected_layer": selected_layer,
            "direction_selection": "support-weighted pivoted QR, seven occupied 4x4 SOM neurons",
            "direction_count": DIRECTION_COUNT,
        },
        "activation_capture_sha256": sha256(args.activations),
        "activation_shape": [SAMPLES, WIDTH],
        "layer_scores": layer_scores,
        "cluster_counts": [int(value) for value in counts],
        "selected_neurons": selected_metadata(selected, counts),
        "train_geometry": train_geometry,
        "bootstrap": {
            "seed": BOOTSTRAP_SEED,
            "fraction": BOOTSTRAP_FRACTION,
            "sample_count": bootstrap_count,
            "sample_indices_sha256": hashlib.sha256(
                np.asarray(bootstrap_indices, dtype="<u4").tobytes()).hexdigest(),
            "cluster_counts": [int(value) for value in boot_counts],
            "selected_neurons": selected_metadata(boot_selected, boot_counts),
            "geometry": boot_geometry,
            "principal_cosines_to_full": boot_cosines,
            "minimum_required_principal_cosine": MIN_BOOTSTRAP_PRINCIPAL_COSINE,
        },
        "artifact_sha256": {
            "train.gguf": sha256(train_path),
            "bootstrap.gguf": sha256(boot_path),
        },
    }
    manifest_path = args.output / "train.manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    os.chmod(manifest_path, 0o600)
    print(
        f"selected layer {selected_layer}; train min singular "
        f"{train_geometry['minimum_singular_value']:.9f}; bootstrap principal min "
        f"{min(boot_cosines):.9f}")


def q5_role(args, versions):
    train_manifest_path = args.output / "train.manifest.json"
    train_path = args.output / "train.gguf"
    q5_path = args.output / "q5.gguf"
    q5_manifest_path = args.output / "q5.manifest.json"
    if not train_manifest_path.is_file() or not train_path.is_file():
        raise ValueError("source directions must be generated first")
    if q5_path.exists() or q5_manifest_path.exists():
        raise ValueError("refusing to overwrite q5 direction artifacts")
    train_manifest = json.loads(train_manifest_path.read_text())
    if (train_manifest.get("method_version") != METHOD_VERSION
            or train_manifest.get("role") != "source"
            or train_manifest.get("dependencies") != versions
            or train_manifest.get("artifact_sha256", {}).get("train.gguf") != sha256(train_path)):
        raise ValueError("source direction manifest binding changed")
    selected_layer = train_manifest["k3_adaptation"]["selected_layer"]
    _, tensors, data_start = validate_capture(args.activations, (selected_layer,))
    harmful, harmless = load_layer(
        args.activations, tensors, data_start, selected_layer)
    all_directions, counts = train_som_directions(harmful, harmless)
    selected = select_supported_pivots(all_directions, counts)
    q5_directions = all_directions[selected]
    geometry = direction_geometry(q5_directions)

    from analyze_direction import load_vectors
    _, train_vectors = load_vectors(train_path)
    train_directions = np.asarray(
        [train_vectors[index] for index in range(1, DIRECTION_COUNT + 1)],
        dtype=np.float64)
    q5_cosines = principal_cosines(train_directions, q5_directions)
    if min(q5_cosines) < MIN_Q5_PRINCIPAL_COSINE:
        raise ValueError(
            f"Q5 minimum principal cosine {min(q5_cosines):.12f} "
            f"< {MIN_Q5_PRINCIPAL_COSINE}")
    write_gguf(q5_path, q5_directions, args.gguf_py)
    manifest = {
        "method_version": METHOD_VERSION,
        "role": "q5-diagnostic",
        "dependencies": versions,
        "train_manifest_sha256": sha256(train_manifest_path),
        "train_direction_sha256": sha256(train_path),
        "activation_capture_sha256": sha256(args.activations),
        "selected_layer_locked_from_source": selected_layer,
        "cluster_counts": [int(value) for value in counts],
        "selected_neurons": selected_metadata(selected, counts),
        "geometry": geometry,
        "principal_cosines_to_source": q5_cosines,
        "minimum_required_principal_cosine": MIN_Q5_PRINCIPAL_COSINE,
        "artifact_sha256": {"q5.gguf": sha256(q5_path)},
    }
    q5_manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    os.chmod(q5_manifest_path, 0o600)
    print(
        f"Q5 selected layer {selected_layer}; min singular "
        f"{geometry['minimum_singular_value']:.9f}; principal min {min(q5_cosines):.9f}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("role", choices=("source", "q5"))
    parser.add_argument("activations", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--gguf-py", type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    versions = require_dependencies()
    if args.role == "source":
        source_role(args, versions)
    else:
        q5_role(args, versions)


if __name__ == "__main__":
    try:
        main()
    except (EOFError, ImportError, OSError, ValueError, json.JSONDecodeError) as exc:
        raise SystemExit(f"FAIL: {exc}") from exc
