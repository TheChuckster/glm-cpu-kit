#!/usr/bin/env python3
"""Compare refusal directions extracted from two quantizations of one model."""

import argparse
import json
import math
import statistics
from pathlib import Path

from analyze_direction import dot, load_vectors


def normalized_band(vectors, start, end):
    mean = [sum(values) for values in zip(*(vectors[layer] for layer in range(start, end + 1)))]
    norm = math.sqrt(dot(mean, mean))
    if not math.isfinite(norm) or norm == 0:
        raise ValueError("direction band has a zero or non-finite mean")
    return [value / norm for value in mean]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("left", type=Path)
    parser.add_argument("right", type=Path)
    parser.add_argument("--band", type=int, nargs=2, default=(56, 73))
    parser.add_argument("--min-band-cosine", type=float, default=0.90)
    parser.add_argument("--expected-layers", type=int, default=92)
    parser.add_argument("--expected-width", type=int, default=7168)
    parser.add_argument("--expected-model-hint", default="kimi-k3")
    parser.add_argument("--json", type=Path)
    args = parser.parse_args()
    if not -1 <= args.min_band_cosine <= 1:
        parser.error("--min-band-cosine must be in [-1, 1]")

    left_metadata, left = load_vectors(args.left)
    right_metadata, right = load_vectors(args.right)
    if set(left) != set(right):
        raise ValueError("direction layer sets differ")
    widths = {len(vector) for vector in left.values()} | {len(vector) for vector in right.values()}
    if len(widths) != 1:
        raise ValueError("direction widths differ")
    width = next(iter(widths))
    if args.expected_layers and len(left) != args.expected_layers:
        raise ValueError(
            f"direction has {len(left)} layers, expected {args.expected_layers}"
        )
    if args.expected_width and width != args.expected_width:
        raise ValueError(f"direction width is {width}, expected {args.expected_width}")
    if args.expected_model_hint:
        hints = {
            left_metadata.get("controlvector.model_hint"),
            right_metadata.get("controlvector.model_hint"),
        }
        if hints != {args.expected_model_hint}:
            raise ValueError(
                f"direction model hints are {sorted(repr(value) for value in hints)}, "
                f"expected {args.expected_model_hint!r}"
            )
    start, end = args.band
    if start < 1 or end < start or end > len(left):
        parser.error("--band is outside the available direction layers")

    per_layer = {layer: dot(left[layer], right[layer]) for layer in sorted(left)}
    band_layer_cosines = [per_layer[layer] for layer in range(start, end + 1)]
    band_cosine = dot(normalized_band(left, start, end), normalized_band(right, start, end))
    result = {
        "left": str(args.left),
        "right": str(args.right),
        "left_model_hint": left_metadata.get("controlvector.model_hint"),
        "right_model_hint": right_metadata.get("controlvector.model_hint"),
        "layers": len(left),
        "width": width,
        "band": [start, end],
        "band_mean_cosine": band_cosine,
        "band_layer_cosine": {
            "min": min(band_layer_cosines),
            "median": statistics.median(band_layer_cosines),
            "max": max(band_layer_cosines),
        },
        "minimum_required_band_mean_cosine": args.min_band_cosine,
        "pass": band_cosine >= args.min_band_cosine,
    }
    print(
        f"layers={len(left)} width={result['width']} band={start}-{end}; "
        f"band-mean cosine={band_cosine:.6f}; per-layer min/median/max "
        f"{min(band_layer_cosines):.6f}/{statistics.median(band_layer_cosines):.6f}/"
        f"{max(band_layer_cosines):.6f}"
    )
    if args.json:
        args.json.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    if not result["pass"]:
        raise SystemExit(
            f"FAIL: band-mean cosine {band_cosine:.6f} is below {args.min_band_cosine:.6f}"
        )


if __name__ == "__main__":
    try:
        main()
    except (EOFError, OSError, ValueError) as exc:
        raise SystemExit(f"FAIL: {exc}") from exc
