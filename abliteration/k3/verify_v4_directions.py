#!/usr/bin/env python3
"""Fail-closed geometry and provenance checks for K3 v4 fused directions."""

import argparse
import array
import ast
import hashlib
import json
import math
import struct
import sys
from pathlib import Path

from analyze_direction import dot, load_vectors
from compare_subspaces import principal_cosines, principal_subspace


SOURCE_HASHES = {
    "train": "7ce9aee3339ee267fa3de8017bba933168467a3d5a59f6d4c7da080b0b0588ad",
    "q5": "57c79d69b275dab8f801d6e08694c5a0471d99f3de52fa18c9f06ed5e9fffdce",
    "validation": "7e94d9256a9f55779252e9c2fd8cab67cafeeee78be1623f8a5c8849e6592246",
}
MIN_FULL_RANK_SINGULAR_VALUE = 0.20
MAX_DONOR_PROJECTION_INTO_TRAIN_SPAN = 0.85
MIN_Q5_PRINCIPAL_COSINE = 0.90
MIN_VALIDATION_PRINCIPAL_COSINE = 0.80
DONOR_BINDING = {
    "base_repo": "moonshotai/Kimi-K3",
    "base_revision": "a590ce090cb049c93a33dfe8c208ec652aa20503",
    "donor_repo": "Resggg/Kimi-K3-Abliterated-modal",
    "donor_revision": "b3a52d265b56551c0011b24d299ba3f8f1393e42",
    "identical_tensor_index_sha256": (
        "a1c5210650ce71d2d3ae9ec5a101ac4afd3cf4b10091be589853437eb967febd"),
}
DONOR_ARTIFACT_HASHES = {
    "layer56-direction.npy": "44ad63ccc1f5fc73cb92841eb277b3cd849644aa1918872d53ec29ee1fe6cdf0",
    "layer70-direction.npy": "97258060dfe950d6f7085919ee8a5a4fd01b9359766945e54ee3ff2a2b7c76ee",
    "donor-direction.npy": "84d7fd6ac161bb1654e926b9352de0375df62ccbec73f3db27cbec2d1e82a8d9",
}


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def normalized(values):
    vector = [float(value) for value in values]
    norm = math.sqrt(dot(vector, vector))
    if not math.isfinite(norm) or norm == 0:
        raise ValueError("zero or non-finite vector")
    return [value / norm for value in vector]


def maximum_error(left, right):
    return max(abs(left_value - right_value)
               for left_value, right_value in zip(left, right))


def load_npy_f32(path):
    """Read the single 1-D little-endian F32 form emitted by recover_v4_donor."""
    with path.open("rb") as source:
        if source.read(6) != b"\x93NUMPY":
            raise ValueError(f"{path} is not a NumPy file")
        major, minor = struct.unpack("BB", source.read(2))
        if major == 1:
            header_length = struct.unpack("<H", source.read(2))[0]
        elif major in (2, 3):
            header_length = struct.unpack("<I", source.read(4))[0]
        else:
            raise ValueError(f"unsupported NumPy format {major}.{minor}")
        header = ast.literal_eval(source.read(header_length).decode("latin1").strip())
        if (header.get("descr") not in ("<f4", "=f4")
                or header.get("fortran_order") is not False
                or header.get("shape") != (7168,)):
            raise ValueError(f"unsupported donor array metadata: {header!r}")
        values = array.array("f")
        values.frombytes(source.read())
        if len(values) != 7168:
            raise ValueError("donor array payload length changed")
        if sys.byteorder != "little":
            values.byteswap()
        return [float(value) for value in values]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("donor_manifest", type=Path)
    parser.add_argument("donor_direction", type=Path)
    parser.add_argument("train_source", type=Path)
    parser.add_argument("q5_source", type=Path)
    parser.add_argument("validation_source", type=Path)
    parser.add_argument("train_fused", type=Path)
    parser.add_argument("q5_fused", type=Path)
    parser.add_argument("validation_fused", type=Path)
    parser.add_argument("--json", type=Path)
    args = parser.parse_args()

    source_paths = {
        "train": args.train_source,
        "q5": args.q5_source,
        "validation": args.validation_source,
    }
    fused_paths = {
        "train": args.train_fused,
        "q5": args.q5_fused,
        "validation": args.validation_fused,
    }
    for label, expected in SOURCE_HASHES.items():
        actual = sha256(source_paths[label])
        if actual != expected:
            raise ValueError(f"{label} source hash {actual} != {expected}")

    donor_manifest = json.loads(args.donor_manifest.read_text())
    donor_hash = sha256(args.donor_direction)
    for key, expected in DONOR_BINDING.items():
        if donor_manifest.get(key) != expected:
            raise ValueError(f"donor manifest binding changed: {key}")
    if donor_manifest.get("artifact_sha256") != DONOR_ARTIFACT_HASHES:
        raise ValueError("donor manifest artifact bindings changed")
    if donor_hash != DONOR_ARTIFACT_HASHES["donor-direction.npy"]:
        raise ValueError("donor direction is not bound by its recovery manifest")
    if donor_manifest.get("cross_layer_absolute_cosine", 0) < 0.9999:
        raise ValueError("donor manifest does not pass the cross-layer gate")
    layer_metrics = donor_manifest.get("layer_metrics", {})
    if set(layer_metrics) != {"56", "70"}:
        raise ValueError("donor manifest layer metrics changed")
    for metrics in layer_metrics.values():
        if metrics.get("rank1_energy_fraction", 0) < 0.98:
            raise ValueError("donor manifest does not pass the rank-one energy gate")
    donor = normalized(load_npy_f32(args.donor_direction))

    source_vectors = {}
    fused_vectors = {}
    geometry = {}
    for label in source_paths:
        source_metadata, source = load_vectors(source_paths[label])
        fused_metadata, fused = load_vectors(fused_paths[label])
        if (source_metadata.get("controlvector.model_hint") != "kimi-k3"
                or len(source) != 92):
            raise ValueError(f"{label} source control vector metadata changed")
        if (fused_metadata.get("controlvector.model_hint") != "kimi-k3"
                or fused_metadata.get("controlvector.layer_count") != 19
                or len(fused) != 19):
            raise ValueError(f"{label} fused control vector metadata changed")
        errors = [maximum_error(fused[index], source[layer])
                  for index, layer in enumerate(range(56, 74), 1)]
        donor_error = min(
            maximum_error(fused[19], donor),
            maximum_error(fused[19], [-value for value in donor]))
        if max(errors) > 2e-7 or donor_error > 2e-7:
            raise ValueError(
                f"{label} fused vector content changed: "
                f"source_error={max(errors):.3g} donor_error={donor_error:.3g}")
        subspace = principal_subspace(fused, 1, 19, 19)
        singular_values = [math.sqrt(max(0.0, value))
                           for value in subspace["eigenvalues"]]
        if min(singular_values) < MIN_FULL_RANK_SINGULAR_VALUE:
            raise ValueError(
                f"{label} fused directions lost numerical rank: "
                f"min singular={min(singular_values):.12f}")
        mean = normalized([sum(values) for values in zip(
            *(fused[index] for index in range(1, 20)))])
        cosines_to_mean = [dot(fused[index], mean)
                           for index in range(1, 20)]
        if min(cosines_to_mean) <= 0:
            raise ValueError(f"{label} contains a direction opposed to its fused mean")
        source_vectors[label] = source
        fused_vectors[label] = fused
        geometry[label] = {
            "minimum_full_rank_singular_value": min(singular_values),
            "maximum_full_rank_singular_value": max(singular_values),
            "minimum_cosine_to_fused_mean": min(cosines_to_mean),
            "maximum_source_roundtrip_error": max(errors),
            "donor_roundtrip_error_up_to_sign": donor_error,
        }

    train_basis = principal_subspace(source_vectors["train"], 56, 73, 18)["basis"]
    donor_projection = math.sqrt(sum(
        dot(donor, basis) ** 2 for basis in train_basis))
    if donor_projection > MAX_DONOR_PROJECTION_INTO_TRAIN_SPAN:
        raise ValueError(
            f"donor is not sufficiently independent: projection={donor_projection:.12f}")

    train_fused_basis = principal_subspace(fused_vectors["train"], 1, 19, 19)["basis"]
    cross = {}
    for label, threshold in (
            ("q5", MIN_Q5_PRINCIPAL_COSINE),
            ("validation", MIN_VALIDATION_PRINCIPAL_COSINE)):
        other = principal_subspace(fused_vectors[label], 1, 19, 19)["basis"]
        cosines = principal_cosines(train_fused_basis, other)
        if min(cosines) < threshold:
            raise ValueError(
                f"{label} fused subspace minimum cosine {min(cosines):.12f} "
                f"is below {threshold:.12f}")
        cross[label] = {
            "principal_cosines": cosines,
            "minimum": min(cosines),
            "threshold": threshold,
        }

    result = {
        "source_sha256": SOURCE_HASHES,
        "donor_sha256": donor_hash,
        "donor_projection_norm_in_train_rank18_span": donor_projection,
        "maximum_allowed_donor_projection": MAX_DONOR_PROJECTION_INTO_TRAIN_SPAN,
        "geometry": geometry,
        "cross_subspaces": cross,
        "fused_sha256": {label: sha256(path) for label, path in fused_paths.items()},
        "pass": True,
    }
    print(
        "PASS: fused rank-19 directions; "
        f"donor/train-span={donor_projection:.9f}; "
        f"principal minima q5={cross['q5']['minimum']:.9f} "
        f"validation={cross['validation']['minimum']:.9f}")
    if args.json:
        args.json.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    try:
        main()
    except (EOFError, OSError, ValueError) as exc:
        raise SystemExit(f"FAIL: {exc}") from exc
