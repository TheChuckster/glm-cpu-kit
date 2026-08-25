#!/usr/bin/env python3
"""Dependency-free structural and expert-byte verifier for a K3 candidate."""

import argparse
import hashlib
import json
import math
import os
import re
import struct
import sys
from dataclasses import dataclass
from pathlib import Path


U8, I8, U16, I16, U32, I32, F32, BOOL, STR, ARR, U64, I64, F64 = range(13)
FORMATS = {U8: "<B", I8: "<b", U16: "<H", I16: "<h", U32: "<I", I32: "<i",
           F32: "<f", BOOL: "<?", U64: "<Q", I64: "<q", F64: "<d"}
SIZES = {kind: struct.calcsize(fmt) for kind, fmt in FORMATS.items()}
WANTED_METADATA = {
    "general.architecture",
    "kimi-k3.block_count",
    "kimi-k3.embedding_length",
    "split.no",
    "split.count",
    "split.tensors.count",
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
        return struct.unpack(FORMATS[kind], self.raw(SIZES[kind]))[0]

    def string(self):
        return self.raw(self.scalar(U64)).decode("utf-8", "strict")

    def value(self, kind):
        if kind == STR:
            return self.string()
        if kind == ARR:
            element_kind = self.scalar(U32)
            return [self.value(element_kind) for _ in range(self.scalar(U64))]
        return self.scalar(kind)

    def skip(self, kind):
        if kind == STR:
            self.source.seek(self.scalar(U64), os.SEEK_CUR)
        elif kind == ARR:
            element_kind = self.scalar(U32)
            count = self.scalar(U64)
            if element_kind in SIZES:
                self.source.seek(SIZES[element_kind] * count, os.SEEK_CUR)
            else:
                for _ in range(count):
                    self.skip(element_kind)
        else:
            self.source.seek(SIZES[kind], os.SEEK_CUR)


@dataclass(frozen=True)
class Tensor:
    name: str
    shape: tuple
    tensor_type: int
    path: Path
    offset: int
    size: int


def parse_shard(path):
    with path.open("rb") as source:
        reader = Reader(source)
        if reader.raw(4) != b"GGUF":
            raise ValueError(f"{path}: not a GGUF file")
        version = reader.scalar(U32)
        tensor_count = reader.scalar(U64)
        kv_count = reader.scalar(U64)
        if version not in (2, 3):
            raise ValueError(f"{path}: unsupported GGUF version {version}")
        metadata = {}
        alignment = 32
        for _ in range(kv_count):
            key = reader.string()
            kind = reader.scalar(U32)
            if key in WANTED_METADATA or key == "general.alignment":
                value = reader.value(kind)
                if key == "general.alignment":
                    alignment = int(value)
                else:
                    metadata[key] = value
            else:
                reader.skip(kind)
        infos = []
        for _ in range(tensor_count):
            name = reader.string()
            shape = tuple(reader.scalar(U64) for _ in range(reader.scalar(U32)))
            tensor_type = reader.scalar(U32)
            offset = reader.scalar(U64)
            infos.append((name, shape, tensor_type, offset))
        data_start = (source.tell() + alignment - 1) // alignment * alignment
        data_bytes = path.stat().st_size - data_start
        source.seek(0)
        metadata["__gguf_header_sha256"] = hashlib.sha256(source.read(data_start)).hexdigest()
        metadata["__gguf_data_start"] = data_start

    by_offset = sorted(infos, key=lambda item: item[3])
    tensors = []
    for index, (name, shape, tensor_type, offset) in enumerate(by_offset):
        end = by_offset[index + 1][3] if index + 1 < len(by_offset) else data_bytes
        if end < offset:
            raise ValueError(f"{path}: invalid offsets around {name}")
        tensors.append(Tensor(name, shape, tensor_type, path, data_start + offset, end - offset))
    return metadata, tensors


def load_model(directory):
    paths = sorted(directory.glob("*.gguf"))
    if len(paths) != 19:
        raise ValueError(f"{directory}: expected 19 GGUF shards, found {len(paths)}")
    metadata = []
    tensors = {}
    for path in paths:
        shard_metadata, shard_tensors = parse_shard(path)
        metadata.append((path, shard_metadata))
        for tensor in shard_tensors:
            if tensor.name in tensors:
                raise ValueError(f"duplicate tensor {tensor.name} in {path} and {tensors[tensor.name].path}")
            tensors[tensor.name] = tensor
    return paths, metadata, tensors


def expected_targets():
    result = {"token_embd.weight", "blk.0.ffn_down.weight"}
    result.update(f"blk.{layer}.attn_output.weight" for layer in range(93))
    result.update(f"blk.{layer}.ffn_down_shexp.weight" for layer in range(1, 93))
    result.update(f"blk.{layer}.ffn_routed_up.weight" for layer in range(1, 93))
    assert len(result) == 279
    return result


def check_metadata(metadata, label):
    populated = [values for _, values in metadata if "general.architecture" in values]
    if not populated:
        raise ValueError(f"{label}: no architecture metadata")
    for values in populated:
        actual = (
            values.get("general.architecture"),
            values.get("kimi-k3.block_count"),
            values.get("kimi-k3.embedding_length"),
            values.get("split.count"),
        )
        expected = ("kimi-k3", 93, 7168, 19)
        if actual != expected:
            raise ValueError(f"{label}: unexpected K3 metadata {actual}, expected {expected}")


def compare_experts(source, candidate, chunk_size=16 * 1024 * 1024):
    names = sorted(name for name in source if "_exps." in name)
    if not names:
        raise ValueError("source contains no routed expert tensors")
    fingerprint = hashlib.sha256()
    compared = 0
    for index, name in enumerate(names, 1):
        left, right = source[name], candidate[name]
        if (left.shape, left.tensor_type, left.size) != (right.shape, right.tensor_type, right.size):
            raise ValueError(f"expert metadata changed: {name}")
        fingerprint.update(name.encode() + b"\0" + str(left.size).encode() + b"\0")
        with left.path.open("rb") as left_file, right.path.open("rb") as right_file:
            left_file.seek(left.offset)
            right_file.seek(right.offset)
            remaining = left.size
            while remaining:
                count = min(remaining, chunk_size)
                left_chunk = left_file.read(count)
                right_chunk = right_file.read(count)
                if len(left_chunk) != count or len(right_chunk) != count:
                    raise EOFError(f"truncated routed expert payload: {name}")
                if left_chunk != right_chunk:
                    raise ValueError(f"expert bytes changed: {name}")
                fingerprint.update(left_chunk)
                compared += count
                remaining -= count
        if index % 25 == 0 or index == len(names):
            print(f"expert bytes: {index}/{len(names)} tensors, {compared / 2**30:.1f} GiB verified", flush=True)
    return names, compared, fingerprint.hexdigest()


def compare_payloads(left, right, names, label, chunk_size=16 * 1024 * 1024):
    """Require exact encoded bytes for a preselected tensor set."""
    ordered = sorted(names, key=lambda name: (str(left[name].path), left[name].offset, name))
    if not ordered:
        raise ValueError(f"{label}: no tensors selected")
    fingerprint = hashlib.sha256()
    compared = 0
    for index, name in enumerate(ordered, 1):
        left_tensor, right_tensor = left[name], right[name]
        if ((left_tensor.shape, left_tensor.tensor_type, left_tensor.size)
                != (right_tensor.shape, right_tensor.tensor_type, right_tensor.size)):
            raise ValueError(f"{label}: tensor metadata changed: {name}")
        fingerprint.update(name.encode() + b"\0" + str(left_tensor.size).encode() + b"\0")
        with left_tensor.path.open("rb") as left_file, right_tensor.path.open("rb") as right_file:
            left_file.seek(left_tensor.offset)
            right_file.seek(right_tensor.offset)
            remaining = left_tensor.size
            while remaining:
                count = min(remaining, chunk_size)
                left_chunk = left_file.read(count)
                right_chunk = right_file.read(count)
                if len(left_chunk) != count or len(right_chunk) != count:
                    raise EOFError(f"{label}: truncated tensor payload: {name}")
                if left_chunk != right_chunk:
                    raise ValueError(f"{label}: tensor bytes changed: {name}")
                fingerprint.update(left_chunk)
                compared += count
                remaining -= count
        if index % 50 == 0 or index == len(ordered):
            print(f"{label}: {index}/{len(ordered)} tensors, {compared / 2**30:.1f} GiB verified", flush=True)
    return compared, fingerprint.hexdigest()


def require_payloads_differ(left, right, names, chunk_size=16 * 1024 * 1024):
    """Prove every intended target differs from the unmodified reference."""
    ordered = sorted(names)
    changed = []
    for name in ordered:
        left_tensor, right_tensor = left[name], right[name]
        if ((left_tensor.shape, left_tensor.tensor_type, left_tensor.size)
                != (right_tensor.shape, right_tensor.tensor_type, right_tensor.size)):
            raise ValueError(f"projection target metadata changed: {name}")
        differs = False
        with left_tensor.path.open("rb") as left_file, right_tensor.path.open("rb") as right_file:
            left_file.seek(left_tensor.offset)
            right_file.seek(right_tensor.offset)
            remaining = left_tensor.size
            while remaining:
                count = min(remaining, chunk_size)
                left_chunk = left_file.read(count)
                right_chunk = right_file.read(count)
                if len(left_chunk) != count or len(right_chunk) != count:
                    raise EOFError(f"truncated projection target payload: {name}")
                if left_chunk != right_chunk:
                    differs = True
                    break
                remaining -= count
        if not differs:
            raise ValueError(f"projection target is byte-identical to the reference: {name}")
        changed.append(name)
    return changed


def check_quant_log(path, targets, max_residual, expected_basis_rank=None,
                    require_patch_existing=False, expected_scale=1.0):
    text = path.read_text(errors="replace")
    if "failed to quantize" in text:
        raise ValueError(f"quantization log reports failure: {path}")
    if "orthogonalization preflight matched 279 tensors" not in text:
        raise ValueError("quantization log lacks the exact 279-tensor preflight")
    if expected_basis_rank is not None:
        marker = f"basis-rank={expected_basis_rank};"
        if marker not in text:
            raise ValueError(f"quantization log lacks expected {marker}")
    scale_matches = re.findall(
        r"orthogonalization preflight matched \d+ tensors;[^\n]*?scale ([0-9.]+);",
        text,
    )
    if len(scale_matches) != 1:
        raise ValueError(
            f"quantization log must contain exactly one intervention scale, "
            f"found {len(scale_matches)}"
        )
    logged_scale = float(scale_matches[0])
    if not math.isclose(logged_scale, expected_scale, rel_tol=0.0, abs_tol=5e-5):
        raise ValueError(
            f"quantization scale mismatch: logged={logged_scale:.4f}, "
            f"expected={expected_scale:.4f}"
        )
    patched = re.findall(
        r"orthogonalize: (\S+) patched-existing shard=\d+ offset=\d+ bytes=\d+", text)
    if require_patch_existing:
        if "patch-existing=yes" not in text:
            raise ValueError("quantization log does not declare patch-existing mode")
        if len(patched) != len(set(patched)):
            raise ValueError("quantization log contains duplicate patch-existing writes")
        if set(patched) != targets:
            missing = sorted(targets - set(patched))[:5]
            extra = sorted(set(patched) - targets)[:5]
            raise ValueError(
                f"patch-existing write set mismatch: missing={missing}, extra={extra}")
    elif patched:
        raise ValueError("unexpected patch-existing writes in a full-output quantization log")
    matches = re.findall(r"orthogonalize: (\S+) post-quant-residual=([0-9.]+)%", text)
    observed = {}
    for name, value in matches:
        if name in observed:
            raise ValueError(f"duplicate post-quant residual for {name}")
        observed[name] = float(value) / 100.0
    if set(observed) != targets:
        missing = sorted(targets - set(observed))[:5]
        extra = sorted(set(observed) - targets)[:5]
        raise ValueError(f"post-quant residual set mismatch: missing={missing}, extra={extra}")
    worst_name = max(observed, key=observed.get)
    worst = observed[worst_name]
    if worst > max_residual:
        raise ValueError(
            f"{worst_name} target-relative subspace error {100 * worst:.6f}% "
            f"exceeds {100 * max_residual:.6f}%"
        )
    actual_matches = re.findall(
        r"orthogonalize: (\S+) post-quant-residual=[0-9.]+% "
        r"actual-source-component=([0-9.]+)%",
        text,
    )
    actual_components = {}
    for name, value in actual_matches:
        if name in actual_components:
            raise ValueError(f"duplicate actual source component for {name}")
        actual_components[name] = float(value) / 100.0
    if math.isclose(expected_scale, 1.0, rel_tol=0.0, abs_tol=1e-12):
        if actual_components:
            raise ValueError("unexpected actual-source-component diagnostics at scale 1")
    else:
        if set(actual_components) != targets:
            missing = sorted(targets - set(actual_components))[:5]
            extra = sorted(set(actual_components) - targets)[:5]
            raise ValueError(
                f"actual source component set mismatch: missing={missing}, extra={extra}"
            )
        expected_magnitude = abs(1.0 - expected_scale)
        tolerance = max_residual + 1e-8
        for name, actual in actual_components.items():
            if abs(actual - expected_magnitude) > tolerance:
                raise ValueError(
                    f"{name} actual source component {100 * actual:.6f}% differs from "
                    f"the expected {100 * expected_magnitude:.6f}% by more than "
                    f"{100 * max_residual:.6f}%"
                )
    return observed, actual_components, worst_name, worst, patched


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path, help="unaltered K3 Q2-source directory")
    parser.add_argument("candidate", type=Path, help="new abliterated Q5-attention directory")
    parser.add_argument(
        "--reference-layout",
        type=Path,
        required=True,
        help="proven Q5-attention model whose tensor types and encoded sizes must match",
    )
    parser.add_argument("--quant-log", type=Path, required=True)
    parser.add_argument("--max-residual", type=float, default=0.02)
    parser.add_argument("--expected-scale", type=float, default=1.0)
    parser.add_argument("--expected-basis-rank", type=int)
    parser.add_argument("--require-patch-existing", action="store_true")
    parser.add_argument("--skip-expert-bytes", action="store_true", help="skip source/candidate routed-expert byte checks during development")
    parser.add_argument("--skip-reference-bytes", action="store_true", help="skip exact A/B payload checks during development")
    parser.add_argument("--json", type=Path)
    args = parser.parse_args()
    if args.source.resolve() == args.candidate.resolve():
        parser.error("source and candidate must be different directories")
    if args.reference_layout.resolve() in (args.source.resolve(), args.candidate.resolve()):
        parser.error("source, candidate, and reference-layout directories must all differ")
    if not (0 <= args.max_residual <= 1):
        parser.error("--max-residual must be in [0, 1]")
    if not (0 < args.expected_scale <= 2):
        parser.error("--expected-scale must be in (0, 2]")
    if args.expected_basis_rank is not None and args.expected_basis_rank < 1:
        parser.error("--expected-basis-rank must be positive")

    source_paths, source_metadata, source = load_model(args.source)
    candidate_paths, candidate_metadata, candidate = load_model(args.candidate)
    reference_paths, reference_metadata, reference = load_model(args.reference_layout)
    check_metadata(source_metadata, "source")
    check_metadata(candidate_metadata, "candidate")
    check_metadata(reference_metadata, "reference layout")
    for index, ((candidate_path, candidate_values), (reference_path, reference_values)) in enumerate(
            zip(candidate_metadata, reference_metadata), 1):
        if candidate_values.get("split.no") != reference_values.get("split.no"):
            raise SystemExit(
                f"shard {index} split number differs: {candidate_path} != {reference_path}"
            )
        if candidate_values["__gguf_header_sha256"] != reference_values["__gguf_header_sha256"]:
            raise SystemExit(
                f"shard {index} GGUF header differs from the reference: "
                f"{candidate_path} != {reference_path}"
            )
    if set(source) != set(candidate):
        raise SystemExit(f"tensor-name set changed: source={len(source)}, candidate={len(candidate)}")
    if set(reference) != set(candidate):
        raise SystemExit(
            f"tensor-name set differs from reference layout: "
            f"reference={len(reference)}, candidate={len(candidate)}"
        )
    for name in source:
        if source[name].shape != candidate[name].shape:
            raise SystemExit(f"tensor shape changed: {name}")
        expected = reference[name]
        actual = candidate[name]
        if actual.shape != expected.shape:
            raise SystemExit(f"tensor shape differs from reference layout: {name}")
        if actual.tensor_type != expected.tensor_type:
            raise SystemExit(
                f"tensor type differs from reference layout: {name}: "
                f"candidate={actual.tensor_type}, reference={expected.tensor_type}"
            )
        if actual.size != expected.size:
            raise SystemExit(
                f"encoded tensor size differs from reference layout: {name}: "
                f"candidate={actual.size}, reference={expected.size}"
            )

    targets = expected_targets()
    missing_targets = targets - set(source)
    if missing_targets:
        raise SystemExit(f"missing expected target tensors: {sorted(missing_targets)[:10]}")
    forbidden = [name for name in targets if "_exps." in name or "ffn_routed_down" in name]
    if forbidden:
        raise AssertionError(f"target recipe includes forbidden expert tensor(s): {forbidden}")

    observed, actual_components, worst_name, worst, patched = check_quant_log(
        args.quant_log, targets, args.max_residual,
        args.expected_basis_rank, args.require_patch_existing, args.expected_scale)
    unchanged_names = set(candidate) - targets
    unchanged_bytes = sum(reference[name].size for name in unchanged_names)
    unchanged_fingerprint = None
    changed_targets = []
    if not args.skip_reference_bytes:
        unchanged_bytes, unchanged_fingerprint = compare_payloads(
            reference, candidate, unchanged_names, "reference unchanged bytes"
        )
        changed_targets = require_payloads_differ(reference, candidate, targets)
    expert_names = [name for name in source if "_exps." in name]
    expert_bytes = sum(source[name].size for name in expert_names)
    expert_fingerprint = None
    if not args.skip_expert_bytes:
        expert_names, expert_bytes, expert_fingerprint = compare_experts(source, candidate)

    result = {
        "source_shards": len(source_paths),
        "candidate_shards": len(candidate_paths),
        "reference_layout_shards": len(reference_paths),
        "source_bytes": sum(path.stat().st_size for path in source_paths),
        "candidate_bytes": sum(path.stat().st_size for path in candidate_paths),
        "reference_layout_bytes": sum(path.stat().st_size for path in reference_paths),
        "reference_layout": str(args.reference_layout),
        "reference_identical_gguf_headers": len(candidate_metadata),
        "reference_gguf_header_fingerprint_sha256": hashlib.sha256(
            "".join(values["__gguf_header_sha256"] for _, values in candidate_metadata).encode()
        ).hexdigest(),
        "tensor_count": len(source),
        "projected_tensor_count": len(targets),
        "basis_rank": args.expected_basis_rank,
        "orthogonalize_scale": args.expected_scale,
        "patch_existing": args.require_patch_existing,
        "patch_existing_payload_writes": len(patched),
        "projected_tensors_differ_from_reference": (
            None if args.skip_reference_bytes else len(changed_targets)
        ),
        "reference_identical_nontarget_tensor_count": len(unchanged_names),
        "reference_identical_nontarget_bytes": unchanged_bytes,
        "reference_nontarget_bytes_compared": not args.skip_reference_bytes,
        "reference_nontarget_fingerprint_sha256": unchanged_fingerprint,
        "routed_expert_tensor_count": len(expert_names),
        "routed_expert_bytes": expert_bytes,
        "routed_expert_bytes_compared": not args.skip_expert_bytes,
        "routed_expert_fingerprint_sha256": expert_fingerprint,
        "post_quant_target_relative_subspace_error": {
            "max": worst,
            "max_tensor": worst_name,
            "median": sorted(observed.values())[len(observed) // 2],
        },
        "post_quant_actual_source_component": None if not actual_components else {
            "expected_magnitude": abs(1.0 - args.expected_scale),
            "max": max(actual_components.values()),
            "min": min(actual_components.values()),
            "median": sorted(actual_components.values())[len(actual_components) // 2],
        },
    }
    if math.isclose(args.expected_scale, 1.0, rel_tol=0.0, abs_tol=1e-12):
        # Preserve the historical key for scale-1 artifacts, where target error
        # and retained source component are algebraically identical.
        result["post_quant_retained_source_component"] = \
            result["post_quant_target_relative_subspace_error"]
    print(f"PASS: {len(source)} tensors and 19 shards structurally identical; "
          f"279 intended projection targets; worst target-relative subspace error "
          f"{100 * worst:.6f}% ({worst_name})")
    if args.skip_reference_bytes:
        print(f"NOTE: skipped byte comparison for {len(unchanged_names)} reference non-target tensors")
    else:
        print(f"PASS: {unchanged_bytes / 2**30:.1f} GiB across {len(unchanged_names)} non-target tensors "
              f"is byte-identical to the live reference; all {len(changed_targets)} targets differ")
    if args.skip_expert_bytes:
        print(f"NOTE: skipped byte comparison for {len(expert_names)} routed expert tensors")
    else:
        print(f"PASS: {expert_bytes / 2**30:.1f} GiB of routed expert data is byte-identical")
    if args.json:
        args.json.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    try:
        main()
    except (EOFError, OSError, ValueError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
