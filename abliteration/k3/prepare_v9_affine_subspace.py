#!/usr/bin/env python3
"""Build the preregistered K3 V9 rank-seven affine-subspace artifacts."""

import argparse
import hashlib
import json
import math
import os
import stat
import struct
import sys
from pathlib import Path

import numpy as np


METHOD_VERSION = "k3-v9-q5-rank7-affine-v1"
SOURCE_METHOD_VERSION = "k3-v5-r2-symmetric-spectral-rank7-v1"
SOURCE_VARIANT = "symmetric-contrast-unit"
WIDTH = 7168
SAMPLES = 359
LAYER = 61
RANK = 7
ALPHAS = (("affine-alpha0.gguf", 0.0),
          ("affine-alpha-m0p5.gguf", -0.5))
EXPECTED_CAPTURE_SHA256 = (
    "bea26596b2f01e4cf964015c4d95c52a1f11f876093b6e5a05bbf4e85fa27051")
EXPECTED_BASIS_SHA256 = (
    "3efcac932b42538b862e1b6b4e454f6ee7930737c7fb6cb794c0ab5d7869c7c9")
EXPECTED_BASIS_MANIFEST_SHA256 = (
    "ce50085977c539c296cb5695df4cc4e6a65f07b8769be69973450d627973dab8")
UNIT_NORM_TOLERANCE = 1e-6
GRAM_TOLERANCE = 2e-6
MIN_MEAN_RETENTION = 0.99
MAX_OFFSET_SPAN_RESIDUAL = 1e-5

U8, I8, U16, I16, U32, I32, F32, BOOL, STR, ARR, U64, I64, F64 = range(13)
GGML_TYPE_F32 = 0
FORMATS = {
    U8: "<B", I8: "<b", U16: "<H", I16: "<h", U32: "<I",
    I32: "<i", F32: "<f", BOOL: "<?", U64: "<Q", I64: "<q", F64: "<d",
}


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


def payload_sha256(values, dtype="<f4"):
    return hashlib.sha256(
        np.asarray(values, dtype=dtype).tobytes(order="C")).hexdigest()


def require_private_file(path, label):
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"missing or symbolic {label}: {path}")
    if stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise ValueError(f"{label} is not private: {path}")


def gguf_index(path):
    with path.open("rb") as source:
        reader = Reader(source)
        if reader.raw(4) != b"GGUF":
            raise ValueError(f"not a GGUF: {path}")
        version = reader.scalar(U32)
        tensor_count = reader.scalar(U64)
        kv_count = reader.scalar(U64)
        if version not in (2, 3):
            raise ValueError(f"unsupported GGUF version {version}")
        metadata = {}
        for _ in range(kv_count):
            key = reader.string()
            if key in metadata:
                raise ValueError(f"duplicate metadata key: {key}")
            metadata[key] = reader.value(reader.scalar(U32))
        tensors = {}
        for _ in range(tensor_count):
            name = reader.string()
            shape = [reader.scalar(U64) for _ in range(reader.scalar(U32))]
            tensor_type = reader.scalar(U32)
            offset = reader.scalar(U64)
            if name in tensors:
                raise ValueError(f"duplicate tensor: {name}")
            tensors[name] = {"shape": shape, "type": tensor_type, "offset": offset}
        alignment = int(metadata.get("general.alignment", 32))
        if alignment <= 0 or alignment & (alignment - 1):
            raise ValueError(f"invalid GGUF alignment {alignment}")
        data_start = (source.tell() + alignment - 1) // alignment * alignment
    return metadata, tensors, data_start


def validate_f32_bounds(path, tensors, data_start, element_counts):
    ranges = []
    for name, count in element_counts.items():
        tensor = tensors[name]
        if tensor["type"] != GGML_TYPE_F32:
            raise ValueError(f"{name} is not F32")
        start = data_start + tensor["offset"]
        end = start + count * 4
        if start < data_start or end > path.stat().st_size:
            raise ValueError(f"tensor exceeds file: {name}")
        ranges.append((start, end, name))
    ranges.sort()
    for left, right in zip(ranges, ranges[1:]):
        if left[1] > right[0]:
            raise ValueError(f"overlapping tensors: {left[2]} and {right[2]}")


def load_capture(path):
    require_private_file(path, "activation capture")
    observed = sha256(path)
    if observed != EXPECTED_CAPTURE_SHA256:
        raise ValueError(
            f"activation hash mismatch: {observed} != {EXPECTED_CAPTURE_SHA256}")
    metadata, tensors, data_start = gguf_index(path)
    expected_names = {f"positive.{LAYER}", f"negative.{LAYER}"}
    if (metadata.get("general.architecture") != "activationcapture"
            or metadata.get("activationcapture.model_hint") != "kimi-k3"
            or metadata.get("activationcapture.method")
            != "final-templated-prompt-position"
            or metadata.get("activationcapture.layer_spec") != str(LAYER)
            or metadata.get("activationcapture.sample_count") != SAMPLES
            or metadata.get("activationcapture.tensor_count") != 2
            or set(tensors) != expected_names):
        raise ValueError("activation metadata/tensor inventory changed")
    for name in expected_names:
        if tensors[name]["shape"] != [WIDTH, SAMPLES]:
            raise ValueError(f"invalid activation tensor {name}: {tensors[name]}")
    validate_f32_bounds(
        path, tensors, data_start, {name: WIDTH * SAMPLES for name in expected_names})
    classes = []
    for label in ("positive", "negative"):
        tensor = tensors[f"{label}.{LAYER}"]
        values = np.memmap(
            path, mode="r", dtype="<f4", offset=data_start + tensor["offset"],
            shape=(SAMPLES, WIDTH))
        if not np.all(np.isfinite(values)):
            raise ValueError(f"non-finite {label} activations")
        classes.append(values)
    return tuple(classes)


def load_basis(path):
    require_private_file(path, "basis")
    observed = sha256(path)
    if observed != EXPECTED_BASIS_SHA256:
        raise ValueError(f"basis hash mismatch: {observed} != {EXPECTED_BASIS_SHA256}")
    metadata, tensors, data_start = gguf_index(path)
    expected_names = {f"direction.{index}" for index in range(1, RANK + 1)}
    if (metadata.get("general.architecture") != "controlvector"
            or metadata.get("controlvector.model_hint") != "kimi-k3"
            or metadata.get("controlvector.layer_count") != RANK
            or set(tensors) != expected_names):
        raise ValueError("basis metadata/tensor inventory changed")
    for name in expected_names:
        if tensors[name]["shape"] != [WIDTH]:
            raise ValueError(f"invalid basis tensor {name}: {tensors[name]}")
    validate_f32_bounds(path, tensors, data_start, {name: WIDTH for name in expected_names})
    rows = []
    for index in range(1, RANK + 1):
        tensor = tensors[f"direction.{index}"]
        row = np.asarray(np.memmap(
            path, mode="r", dtype="<f4", offset=data_start + tensor["offset"],
            shape=(WIDTH,))).copy()
        if not np.all(np.isfinite(row)):
            raise ValueError(f"non-finite basis row {index}")
        rows.append(row)
    return np.asarray(rows, dtype="<f4")


def validate_basis_manifest(path, capture_hash, basis_hash, basis):
    require_private_file(path, "basis manifest")
    observed = sha256(path)
    if observed != EXPECTED_BASIS_MANIFEST_SHA256:
        raise ValueError(
            f"basis manifest hash mismatch: {observed} != "
            f"{EXPECTED_BASIS_MANIFEST_SHA256}")
    manifest = json.loads(path.read_text())
    cosines = manifest.get("principal_cosines_to_source")
    recorded_retention = manifest.get("class_mean_retention")
    if (manifest.get("method_version") != SOURCE_METHOD_VERSION
            or manifest.get("role") != "q5-diagnostic"
            or manifest.get("q5_activation_sha256") != capture_hash
            or manifest.get("selected_layer_locked_from_source") != LAYER
            or manifest.get("selected_variant_locked_from_source") != SOURCE_VARIANT
            or manifest.get("matrix_shape") != [2 * SAMPLES, WIDTH]
            or manifest.get("rank") != RANK
            or manifest.get("basis_payload_sha256") != payload_sha256(basis)
            or manifest.get("artifact_sha256", {}).get("q5.gguf") != basis_hash
            or not isinstance(cosines, list) or len(cosines) != RANK
            or not all(isinstance(value, (int, float)) and math.isfinite(value)
                       for value in cosines)
            or min(cosines) < 0.90
            or not isinstance(recorded_retention, (int, float))
            or not math.isfinite(recorded_retention)
            or recorded_retention < MIN_MEAN_RETENTION):
        raise ValueError("basis manifest binding changed")
    return manifest


def project(basis, vector):
    return basis.T @ (basis @ vector)


def validate_geometry(basis_f32, positive, negative, source_manifest):
    basis = np.asarray(basis_f32, dtype=np.float64)
    norms = np.linalg.norm(basis, axis=1)
    if not np.all(np.isfinite(norms)):
        raise ValueError("non-finite basis norms")
    norm_error = float(np.max(np.abs(norms - 1.0)))
    gram_error = float(np.max(np.abs(basis @ basis.T - np.eye(RANK))))
    if norm_error > UNIT_NORM_TOLERANCE:
        raise ValueError(
            f"basis row norm error {norm_error:.12g} > {UNIT_NORM_TOLERANCE}")
    if gram_error > GRAM_TOLERANCE:
        raise ValueError(f"basis Gram error {gram_error:.12g} > {GRAM_TOLERANCE}")

    positive_mean = np.mean(positive, axis=0, dtype=np.float64)
    negative_mean = np.mean(negative, axis=0, dtype=np.float64)
    difference = positive_mean - negative_mean
    difference_norm = float(np.linalg.norm(difference))
    if (not np.all(np.isfinite(positive_mean))
            or not np.all(np.isfinite(negative_mean))
            or not np.all(np.isfinite(difference))
            or not math.isfinite(difference_norm) or difference_norm <= 0):
        raise ValueError("invalid class means or difference")
    retention = float(np.linalg.norm(basis @ difference) / difference_norm)
    if not math.isfinite(retention) or retention < MIN_MEAN_RETENTION:
        raise ValueError(
            f"Q5 class-mean retention {retention:.12f} < {MIN_MEAN_RETENTION}")
    recorded = float(source_manifest["class_mean_retention"])
    if abs(retention - recorded) > 3e-6:
        raise ValueError(
            f"Q5 class-mean retention {retention:.12f} != manifest {recorded:.12f}")
    return {
        "basis": basis,
        "positive_mean": positive_mean,
        "negative_mean": negative_mean,
        "difference": difference,
        "difference_norm": difference_norm,
        "retention": retention,
        "maximum_row_norm_error": norm_error,
        "maximum_gram_error": gram_error,
    }


def offset_span_residual(basis, offset):
    offset64 = np.asarray(offset, dtype=np.float64)
    norm = float(np.linalg.norm(offset64))
    residual = float(np.linalg.norm(offset64 - project(basis, offset64)))
    if not math.isfinite(norm) or not math.isfinite(residual):
        raise ValueError("non-finite offset geometry")
    return residual / max(norm, np.finfo(np.float64).tiny)


def write_affine_subspace(path, basis, offset, alpha, gguf_py):
    sys.path.insert(0, str(gguf_py))
    try:
        from gguf import GGUFWriter
    except ImportError as exc:
        raise ValueError(f"cannot import GGUFWriter from {gguf_py}") from exc
    writer = GGUFWriter(path, "controlvectorsubspace")
    writer.add_string("controlvectorsubspace.model_hint", "kimi-k3")
    writer.add_string("controlvectorsubspace.method", METHOD_VERSION)
    writer.add_uint32("controlvectorsubspace.layer", LAYER)
    writer.add_uint32("controlvectorsubspace.rank", RANK)
    writer.add_float32("controlvectorsubspace.alpha", alpha)
    writer.add_string(
        "controlvectorsubspace.source_activation_sha256", EXPECTED_CAPTURE_SHA256)
    writer.add_string(
        "controlvectorsubspace.source_basis_sha256", EXPECTED_BASIS_SHA256)
    writer.add_string(
        "controlvectorsubspace.offset_payload_sha256", payload_sha256(offset))
    writer.add_uint32("controlvectorsubspace.tensor_count", 2)
    writer.add_tensor(f"basis.{LAYER}", np.asarray(basis, dtype="<f4"))
    writer.add_tensor(f"offset.{LAYER}", np.asarray(offset, dtype="<f4"))
    writer.write_header_to_file()
    writer.write_kv_data_to_file()
    writer.write_tensors_to_file()
    writer.close()
    os.chmod(path, 0o600)


def validate_affine_subspace(path, expected_basis, expected_offset, alpha):
    require_private_file(path, "affine-subspace artifact")
    metadata, tensors, data_start = gguf_index(path)
    basis_name = f"basis.{LAYER}"
    offset_name = f"offset.{LAYER}"
    expected_metadata = {
        "general.architecture": "controlvectorsubspace",
        "controlvectorsubspace.model_hint": "kimi-k3",
        "controlvectorsubspace.method": METHOD_VERSION,
        "controlvectorsubspace.layer": LAYER,
        "controlvectorsubspace.rank": RANK,
        "controlvectorsubspace.source_activation_sha256": EXPECTED_CAPTURE_SHA256,
        "controlvectorsubspace.source_basis_sha256": EXPECTED_BASIS_SHA256,
        "controlvectorsubspace.offset_payload_sha256": payload_sha256(expected_offset),
        "controlvectorsubspace.tensor_count": 2,
    }
    if any(metadata.get(key) != value for key, value in expected_metadata.items()):
        raise ValueError(f"affine-subspace metadata changed: {path}")
    if not math.isclose(
            float(metadata.get("controlvectorsubspace.alpha", math.nan)), alpha,
            rel_tol=0.0, abs_tol=0.0):
        raise ValueError(f"affine-subspace alpha changed: {path}")
    if set(tensors) != {basis_name, offset_name}:
        raise ValueError(f"affine-subspace tensor inventory changed: {path}")
    if tensors[basis_name]["shape"] != [WIDTH, RANK]:
        raise ValueError(f"affine-subspace basis shape changed: {tensors[basis_name]}")
    if tensors[offset_name]["shape"] != [WIDTH]:
        raise ValueError(f"affine-subspace offset shape changed: {tensors[offset_name]}")
    validate_f32_bounds(path, tensors, data_start, {
        basis_name: WIDTH * RANK, offset_name: WIDTH})
    basis = np.asarray(np.memmap(
        path, mode="r", dtype="<f4", offset=data_start + tensors[basis_name]["offset"],
        shape=(RANK, WIDTH)))
    offset = np.asarray(np.memmap(
        path, mode="r", dtype="<f4", offset=data_start + tensors[offset_name]["offset"],
        shape=(WIDTH,)))
    if not np.array_equal(basis, np.asarray(expected_basis, dtype="<f4")):
        raise ValueError(f"affine-subspace basis payload changed: {path}")
    if not np.array_equal(offset, np.asarray(expected_offset, dtype="<f4")):
        raise ValueError(f"affine-subspace offset payload changed: {path}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("q5_capture", type=Path)
    parser.add_argument("q5_basis", type=Path)
    parser.add_argument("q5_basis_manifest", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--gguf-py", type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)

    if not (args.gguf_py / "gguf").is_dir():
        raise ValueError(f"missing GGUF Python package: {args.gguf_py}")
    if args.output.exists():
        raise ValueError(f"refusing to reuse output path: {args.output}")

    positive, negative = load_capture(args.q5_capture)
    basis_f32 = load_basis(args.q5_basis)
    source_manifest = validate_basis_manifest(
        args.q5_basis_manifest, EXPECTED_CAPTURE_SHA256, EXPECTED_BASIS_SHA256,
        basis_f32)
    geometry = validate_geometry(basis_f32, positive, negative, source_manifest)
    basis = geometry["basis"]

    offsets = {}
    span_residuals = {}
    for filename, alpha in ALPHAS:
        offset64 = (
            project(basis, geometry["negative_mean"])
            + alpha * project(basis, geometry["difference"]))
        offset = np.asarray(offset64, dtype="<f4")
        if offset.shape != (WIDTH,) or not np.all(np.isfinite(offset)):
            raise ValueError(f"invalid offset for alpha {alpha}")
        residual = offset_span_residual(basis, offset)
        if residual > MAX_OFFSET_SPAN_RESIDUAL:
            raise ValueError(
                f"offset span residual {residual:.12g} > {MAX_OFFSET_SPAN_RESIDUAL}")
        offsets[filename] = offset
        span_residuals[filename] = residual

    args.output.mkdir(mode=0o700)
    artifact_paths = {}
    for filename, alpha in ALPHAS:
        path = args.output / filename
        write_affine_subspace(path, basis_f32, offsets[filename], alpha, args.gguf_py)
        validate_affine_subspace(path, basis_f32, offsets[filename], alpha)
        artifact_paths[filename] = path

    manifest = {
        "method_version": METHOD_VERSION,
        "layer": LAYER,
        "width": WIDTH,
        "rank": RANK,
        "samples_per_class": SAMPLES,
        "class_proxy": {
            "positive": "already-consumed harmful prompts",
            "negative": "already-consumed harmless prompts",
            "activation_position": "final templated prompt position",
        },
        "formula": "v' = v - B^T*B*v + B^T*B*n + alpha*B^T*B*(p-n)",
        "alphas_in_locked_order": [alpha for _, alpha in ALPHAS],
        "sources": {
            "q5_capture": {
                "path": str(args.q5_capture),
                "sha256": EXPECTED_CAPTURE_SHA256,
            },
            "q5_basis": {
                "path": str(args.q5_basis),
                "sha256": EXPECTED_BASIS_SHA256,
            },
            "q5_basis_manifest": {
                "path": str(args.q5_basis_manifest),
                "sha256": EXPECTED_BASIS_MANIFEST_SHA256,
            },
        },
        "geometry": {
            "difference_norm": geometry["difference_norm"],
            "class_mean_retention": geometry["retention"],
            "maximum_row_norm_error": geometry["maximum_row_norm_error"],
            "maximum_gram_error": geometry["maximum_gram_error"],
            "offset_relative_span_residual": span_residuals,
        },
        "thresholds": {
            "float32_unit_norm_tolerance": UNIT_NORM_TOLERANCE,
            "maximum_float32_gram_error": GRAM_TOLERANCE,
            "minimum_class_mean_retention": MIN_MEAN_RETENTION,
            "maximum_offset_relative_span_residual": MAX_OFFSET_SPAN_RESIDUAL,
        },
        "source_payload_sha256": {
            "basis_f32": payload_sha256(basis_f32),
            "positive_mean_f64": payload_sha256(geometry["positive_mean"], "<f8"),
            "negative_mean_f64": payload_sha256(geometry["negative_mean"], "<f8"),
            "difference_f64": payload_sha256(geometry["difference"], "<f8"),
        },
        "offset_payload_sha256": {
            filename: payload_sha256(offset) for filename, offset in offsets.items()
        },
        "artifact_sha256": {
            filename: sha256(path) for filename, path in artifact_paths.items()
        },
    }
    manifest_path = args.output / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    os.chmod(manifest_path, 0o600)
    print(json.dumps(manifest, indent=2, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except (EOFError, ImportError, OSError, ValueError, json.JSONDecodeError) as exc:
        raise SystemExit(f"FAIL: {exc}") from exc
