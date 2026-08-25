#!/usr/bin/env python3
"""Build the preregistered K3 V8 ACE projection and offset vectors."""

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


METHOD_VERSION = "k3-v8-ace-layer61-v1"
WIDTH = 7168
SAMPLES = 359
LAYER = 61
BOOTSTRAP_SEEDS = tuple(range(20260827, 20260832))
BOOTSTRAP_SAMPLES = 287
MIN_BOOTSTRAP_COSINE = 0.95
MIN_Q5_COSINE = 0.90
MIN_NORM_RATIO = 0.5
MAX_NORM_RATIO = 2.0
UNIT_NORM_TOLERANCE = 1e-6
EXPECTED_Q5_SHA256 = "bea26596b2f01e4cf964015c4d95c52a1f11f876093b6e5a05bbf4e85fa27051"

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


def payload_sha256(values):
    return hashlib.sha256(
        np.asarray(values, dtype="<f4").tobytes(order="C")).hexdigest()


def payload_f64_sha256(values):
    return hashlib.sha256(
        np.asarray(values, dtype="<f8").tobytes(order="C")).hexdigest()


def require_private(path):
    if stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise ValueError(f"artifact is not private: {path}")


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
        data_start = (source.tell() + alignment - 1) // alignment * alignment
    return metadata, tensors, data_start


def validate_capture(path, expected_sha256):
    if not path.is_file():
        raise ValueError(f"missing activation capture: {path}")
    require_private(path)
    observed = sha256(path)
    if observed != expected_sha256:
        raise ValueError(
            f"activation hash mismatch for {path}: {observed} != {expected_sha256}")
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
        raise ValueError(f"activation metadata/tensor inventory changed: {path}")
    for name, tensor in tensors.items():
        if tensor["type"] != GGML_TYPE_F32 or tensor["shape"] != [WIDTH, SAMPLES]:
            raise ValueError(f"invalid activation tensor {name}: {tensor}")
        end = data_start + tensor["offset"] + WIDTH * SAMPLES * 4
        if end > path.stat().st_size:
            raise ValueError(f"activation tensor exceeds file: {name}")
    return tensors, data_start


def load_classes(path, expected_sha256):
    tensors, data_start = validate_capture(path, expected_sha256)
    result = []
    for label in ("positive", "negative"):
        tensor = tensors[f"{label}.{LAYER}"]
        values = np.memmap(
            path, mode="r", dtype="<f4",
            offset=data_start + tensor["offset"], shape=(SAMPLES, WIDTH))
        if not np.all(np.isfinite(values)):
            raise ValueError(f"non-finite {label} activations in {path}")
        result.append(values)
    return tuple(result)


def class_geometry(positive, negative):
    positive_mean = np.mean(positive, axis=0, dtype=np.float64)
    negative_mean = np.mean(negative, axis=0, dtype=np.float64)
    raw = positive_mean - negative_mean
    raw_norm = float(np.linalg.norm(raw))
    if (not np.all(np.isfinite(positive_mean))
            or not np.all(np.isfinite(negative_mean))
            or not np.all(np.isfinite(raw))
            or not math.isfinite(raw_norm) or raw_norm <= 0):
        raise ValueError("invalid class means or difference norm")
    unit = raw / raw_norm
    return positive_mean, negative_mean, raw, raw_norm, unit


def cosine(left, right):
    denominator = float(np.linalg.norm(left) * np.linalg.norm(right))
    value = float(np.dot(left, right) / denominator) if denominator else math.nan
    if not math.isfinite(value):
        raise ValueError("non-finite cosine")
    return value


def write_control_vector(path, values, gguf_py):
    sys.path.insert(0, str(gguf_py))
    try:
        from gguf import GGUFWriter
    except ImportError as exc:
        raise ValueError(f"cannot import GGUFWriter from {gguf_py}") from exc
    writer = GGUFWriter(path, "controlvector")
    writer.add_string("controlvector.model_hint", "kimi-k3")
    writer.add_uint32("controlvector.layer_count", 1)
    writer.add_tensor(f"direction.{LAYER}", np.asarray(values, dtype="<f4"))
    writer.write_header_to_file()
    writer.write_kv_data_to_file()
    writer.write_tensors_to_file()
    writer.close()
    os.chmod(path, 0o600)


def validate_control_vector(path, expected_payload):
    require_private(path)
    metadata, tensors, data_start = gguf_index(path)
    expected_name = f"direction.{LAYER}"
    if (metadata.get("general.architecture") != "controlvector"
            or metadata.get("controlvector.model_hint") != "kimi-k3"
            or metadata.get("controlvector.layer_count") != 1
            or set(tensors) != {expected_name}):
        raise ValueError(f"control-vector metadata changed: {path}")
    tensor = tensors[expected_name]
    if tensor["type"] != GGML_TYPE_F32 or tensor["shape"] != [WIDTH]:
        raise ValueError(f"control-vector tensor changed: {tensor}")
    offset = data_start + tensor["offset"]
    values = np.memmap(path, mode="r", dtype="<f4", offset=offset, shape=(WIDTH,))
    if not np.array_equal(values, np.asarray(expected_payload, dtype="<f4")):
        raise ValueError(f"control-vector payload mismatch: {path}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("v2_capture", type=Path)
    parser.add_argument("q5_capture", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--expected-v2-sha256", required=True)
    parser.add_argument("--gguf-py", type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)

    if (len(args.expected_v2_sha256) != 64
            or any(char not in "0123456789abcdef" for char in args.expected_v2_sha256)):
        raise ValueError("expected V2 SHA-256 must be 64 lowercase hexadecimal digits")
    if not (args.gguf_py / "gguf").is_dir():
        raise ValueError(f"missing GGUF Python package: {args.gguf_py}")
    if args.output.exists():
        raise ValueError(f"refusing to reuse output path: {args.output}")

    v2_positive, v2_negative = load_classes(
        args.v2_capture, args.expected_v2_sha256)
    q5_positive, q5_negative = load_classes(
        args.q5_capture, EXPECTED_Q5_SHA256)
    v2_p, v2_n, v2_raw, v2_norm, v2_unit = class_geometry(
        v2_positive, v2_negative)
    q5_p, q5_n, q5_raw, q5_norm, q5_unit = class_geometry(
        q5_positive, q5_negative)

    q5_cosine = cosine(v2_unit, q5_unit)
    norm_ratio = v2_norm / q5_norm
    negative_projection = float(np.dot(v2_n, v2_unit))
    if q5_cosine < MIN_Q5_COSINE:
        raise ValueError(f"V2/Q5 direction cosine {q5_cosine:.12f} < {MIN_Q5_COSINE}")
    if not MIN_NORM_RATIO <= norm_ratio <= MAX_NORM_RATIO:
        raise ValueError(
            f"V2/Q5 norm ratio {norm_ratio:.12f} outside "
            f"[{MIN_NORM_RATIO}, {MAX_NORM_RATIO}]")
    if not math.isfinite(negative_projection) or abs(negative_projection) > v2_norm:
        raise ValueError("V2 negative-mean projection exceeds the class gap")

    bootstrap = []
    for seed in BOOTSTRAP_SEEDS:
        indices = np.sort(np.random.default_rng(seed).choice(
            SAMPLES, size=BOOTSTRAP_SAMPLES, replace=False))
        _, _, _, boot_norm, boot_unit = class_geometry(
            v2_positive[indices], v2_negative[indices])
        boot_cosine = cosine(v2_unit, boot_unit)
        if boot_cosine < MIN_BOOTSTRAP_COSINE:
            raise ValueError(
                f"bootstrap {seed} cosine {boot_cosine:.12f} "
                f"< {MIN_BOOTSTRAP_COSINE}")
        bootstrap.append({
            "seed": seed,
            "sample_count": BOOTSTRAP_SAMPLES,
            "indices_sha256": hashlib.sha256(
                np.asarray(indices, dtype="<u4").tobytes()).hexdigest(),
            "difference_norm": boot_norm,
            "cosine_to_full": boot_cosine,
        })

    projection = np.asarray(v2_unit, dtype="<f4")
    runtime_unit = np.asarray(projection, dtype=np.float64)
    projection_norm = float(np.linalg.norm(runtime_unit))
    if abs(projection_norm - 1.0) > UNIT_NORM_TOLERANCE:
        raise ValueError(f"float32 projection norm {projection_norm:.12f} is not unit")
    runtime_negative_projection = float(np.dot(v2_n, runtime_unit))
    offset_alpha0 = np.asarray(
        runtime_negative_projection * runtime_unit, dtype="<f4")
    offset_alpha_m0p5 = np.asarray(
        runtime_negative_projection * runtime_unit - 0.5 * v2_raw, dtype="<f4")
    for name, values in (
            ("projection", projection),
            ("offset-alpha0", offset_alpha0),
            ("offset-alpha-m0p5", offset_alpha_m0p5)):
        if values.shape != (WIDTH,) or not np.all(np.isfinite(values)):
            raise ValueError(f"invalid {name} output")

    args.output.mkdir(mode=0o700)
    paths = {
        "projection.gguf": args.output / "projection.gguf",
        "offset-alpha0.gguf": args.output / "offset-alpha0.gguf",
        "offset-alpha-m0p5.gguf": args.output / "offset-alpha-m0p5.gguf",
    }
    payloads = {
        "projection.gguf": projection,
        "offset-alpha0.gguf": offset_alpha0,
        "offset-alpha-m0p5.gguf": offset_alpha_m0p5,
    }
    for name, path in paths.items():
        write_control_vector(path, payloads[name], args.gguf_py)
        validate_control_vector(path, payloads[name])

    manifest = {
        "method_version": METHOD_VERSION,
        "layer": LAYER,
        "width": WIDTH,
        "samples_per_class": SAMPLES,
        "class_proxy": {
            "positive": "already-consumed harmful prompts",
            "negative": "already-consumed harmless prompts",
            "activation_position": "final templated prompt position",
        },
        "formula": "v' = v - proj_u(v) + proj_u(n) + alpha*(p-n)",
        "alphas": [0.0, -0.5],
        "sources": {
            "v2_capture": {
                "path": str(args.v2_capture),
                "sha256": args.expected_v2_sha256,
            },
            "q5_capture": {
                "path": str(args.q5_capture),
                "sha256": EXPECTED_Q5_SHA256,
            },
        },
        "geometry": {
            "v2_difference_norm": v2_norm,
            "q5_difference_norm": q5_norm,
            "v2_q5_norm_ratio": norm_ratio,
            "v2_q5_direction_cosine": q5_cosine,
            "v2_positive_mean_projection": float(np.dot(v2_p, runtime_unit)),
            "v2_negative_mean_projection": runtime_negative_projection,
            "float32_projection_norm": projection_norm,
            "bootstrap": bootstrap,
        },
        "thresholds": {
            "minimum_v2_q5_direction_cosine": MIN_Q5_COSINE,
            "v2_q5_norm_ratio": [MIN_NORM_RATIO, MAX_NORM_RATIO],
            "maximum_negative_projection_to_gap_ratio": 1.0,
            "minimum_bootstrap_cosine": MIN_BOOTSTRAP_COSINE,
            "float32_unit_norm_tolerance": UNIT_NORM_TOLERANCE,
        },
        "payload_sha256": {
            name: payload_sha256(values) for name, values in payloads.items()
        },
        "source_mean_f64_payload_sha256": {
            "v2_positive_mean": payload_f64_sha256(v2_p),
            "v2_negative_mean": payload_f64_sha256(v2_n),
            "v2_raw_difference": payload_f64_sha256(v2_raw),
            "q5_positive_mean": payload_f64_sha256(q5_p),
            "q5_negative_mean": payload_f64_sha256(q5_n),
            "q5_raw_difference": payload_f64_sha256(q5_raw),
        },
        "artifact_sha256": {
            name: sha256(path) for name, path in paths.items()
        },
    }
    manifest_path = args.output / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    os.chmod(manifest_path, 0o600)
    print(json.dumps(manifest, indent=2, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except (EOFError, ImportError, OSError, ValueError) as exc:
        raise SystemExit(f"FAIL: {exc}") from exc
