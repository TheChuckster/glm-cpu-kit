#!/usr/bin/env python3
"""Inspect a control-vector GGUF and score stable normalized layer bands.

This is dependency-free so it runs on chuckdancer. Stability is a diagnostic,
not a causal direction selector: the K3 recipe's pre-registered 56--73 band is
kept unless held-out evaluation justifies a different experiment.
"""

import argparse
import array
import json
import math
import statistics
import struct
import sys
from pathlib import Path


U8, I8, U16, I16, U32, I32, F32, BOOL, STR, ARR, U64, I64, F64 = range(13)
FORMATS = {U8: "<B", I8: "<b", U16: "<H", I16: "<h", U32: "<I", I32: "<i",
           F32: "<f", BOOL: "<?", U64: "<Q", I64: "<q", F64: "<d"}


class Reader:
    def __init__(self, source):
        self.source = source

    def raw(self, count):
        data = self.source.read(count)
        if len(data) != count:
            raise EOFError("truncated GGUF")
        return data

    def scalar(self, kind):
        return struct.unpack(FORMATS[kind], self.raw(struct.calcsize(FORMATS[kind])))[0]

    def string(self):
        return self.raw(self.scalar(U64)).decode("utf-8")

    def value(self, kind):
        if kind == STR:
            return self.string()
        if kind == ARR:
            element_kind = self.scalar(U32)
            return [self.value(element_kind) for _ in range(self.scalar(U64))]
        return self.scalar(kind)


def load_vectors(path):
    with path.open("rb") as source:
        reader = Reader(source)
        if reader.raw(4) != b"GGUF":
            raise ValueError("not a GGUF file")
        version = reader.scalar(U32)
        tensor_count = reader.scalar(U64)
        kv_count = reader.scalar(U64)
        if version not in (2, 3):
            raise ValueError(f"unsupported GGUF version {version}")
        metadata = {}
        for _ in range(kv_count):
            key = reader.string()
            metadata[key] = reader.value(reader.scalar(U32))
        tensors = []
        for _ in range(tensor_count):
            name = reader.string()
            shape = [reader.scalar(U64) for _ in range(reader.scalar(U32))]
            tensor_type = reader.scalar(U32)
            offset = reader.scalar(U64)
            tensors.append((name, shape, tensor_type, offset))
        alignment = int(metadata.get("general.alignment", 32))
        data_start = (source.tell() + alignment - 1) // alignment * alignment

        vectors = {}
        for name, shape, tensor_type, offset in tensors:
            if not name.startswith("direction."):
                continue
            if tensor_type != 0 or len(shape) != 1:
                raise ValueError(f"{name} must be a one-dimensional F32 tensor")
            layer = int(name.split(".", 1)[1])
            if layer in vectors:
                raise ValueError(f"duplicate direction tensor for layer {layer}")
            source.seek(data_start + offset)
            values = array.array("f")
            values.frombytes(reader.raw(4 * shape[0]))
            if sys.byteorder != "little":
                values.byteswap()
            vector = [float(value) for value in values]
            norm = math.sqrt(sum(value * value for value in vector))
            if not math.isfinite(norm) or norm == 0:
                raise ValueError(f"{name} has invalid norm {norm}")
            vectors[layer] = [value / norm for value in vector]

    expected = list(range(1, len(vectors) + 1))
    if sorted(vectors) != expected:
        raise ValueError(f"direction layers are not contiguous 1..{len(vectors)}")
    widths = {len(vector) for vector in vectors.values()}
    if len(widths) != 1:
        raise ValueError("direction tensors have inconsistent widths")
    return metadata, vectors


def dot(left, right):
    return sum(a * b for a, b in zip(left, right))


def band_stats(vectors, start, end):
    selected = [vectors[layer] for layer in range(start, end + 1)]
    mean = [sum(values) for values in zip(*selected)]
    norm = math.sqrt(dot(mean, mean))
    if not math.isfinite(norm) or norm == 0:
        raise ValueError(f"layers {start}-{end} have a zero or non-finite band mean")
    mean = [value / norm for value in mean]
    cosines = [dot(vector, mean) for vector in selected]
    adjacent = [dot(selected[i], selected[i + 1]) for i in range(len(selected) - 1)]
    return {
        "start": start,
        "end": end,
        "layers": len(selected),
        "cosine_to_mean": {
            "min": min(cosines),
            "median": statistics.median(cosines),
            "mean": statistics.fmean(cosines),
            "max": max(cosines),
        },
        "adjacent_cosine": {
            "min": min(adjacent) if adjacent else 1.0,
            "median": statistics.median(adjacent) if adjacent else 1.0,
            "mean": statistics.fmean(adjacent) if adjacent else 1.0,
            "max": max(adjacent) if adjacent else 1.0,
        },
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("control_vector", type=Path)
    parser.add_argument("--band", type=int, nargs=2, default=(56, 73))
    parser.add_argument("--window", type=int, default=18)
    parser.add_argument(
        "--require-positive-band",
        action="store_true",
        help="fail unless every selected layer has positive cosine to the band mean",
    )
    parser.add_argument("--json", type=Path)
    args = parser.parse_args()

    metadata, vectors = load_vectors(args.control_vector)
    if args.window < 2 or args.window > len(vectors):
        parser.error("--window must be between 2 and the number of direction layers")
    start, end = args.band
    if start < 1 or end < start or end > len(vectors):
        parser.error("--band is outside the available direction layers")

    windows = [band_stats(vectors, first, first + args.window - 1)
               for first in range(1, len(vectors) - args.window + 2)]
    # Maximise the worst-aligned layer first; median breaks ties. This reports
    # geometric stability only and intentionally does not tune on test output.
    best = max(windows, key=lambda item: (
        item["cosine_to_mean"]["min"], item["cosine_to_mean"]["median"]))
    prescribed = band_stats(vectors, start, end)
    result = {
        "file": str(args.control_vector),
        "model_hint": metadata.get("controlvector.model_hint"),
        "embedding_length": len(next(iter(vectors.values()))),
        "direction_layers": len(vectors),
        "prescribed_band": prescribed,
        "most_stable_window": best,
        "positive_prescribed_band": prescribed["cosine_to_mean"]["min"] > 0,
        "warning": "stability is not a causal efficacy score; do not select a band on final test prompts",
    }

    print(f"{len(vectors)} direction layers, width {result['embedding_length']}, "
          f"model hint {result['model_hint']!r}")
    for label, stats in (("pre-registered", prescribed), ("most stable", best)):
        cosine = stats["cosine_to_mean"]
        adjacent = stats["adjacent_cosine"]
        print(f"{label:14s} {stats['start']:02d}-{stats['end']:02d}: "
              f"to-mean min/median/max {cosine['min']:.6f}/{cosine['median']:.6f}/{cosine['max']:.6f}; "
              f"adjacent min/median {adjacent['min']:.6f}/{adjacent['median']:.6f}")
    if args.json:
        args.json.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    if args.require_positive_band and not result["positive_prescribed_band"]:
        raise SystemExit("FAIL: a pre-registered band layer opposes the normalized band mean")


if __name__ == "__main__":
    main()
