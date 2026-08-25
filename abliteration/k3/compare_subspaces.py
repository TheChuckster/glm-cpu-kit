#!/usr/bin/env python3
"""Dependency-free comparison of principal refusal-direction subspaces."""

import argparse
import hashlib
import json
import math
from pathlib import Path

from analyze_direction import dot, load_vectors


def jacobi_eigen_symmetric(matrix, max_sweeps=100, tolerance=1e-13):
    """Return descending eigenvalues and column eigenvectors of a small matrix."""
    size = len(matrix)
    work = [list(row) for row in matrix]
    vectors = [[float(row == col) for col in range(size)] for row in range(size)]
    for _ in range(max_sweeps):
        max_diagonal = max(abs(work[index][index]) for index in range(size))
        max_off_diagonal = max(
            (abs(work[row][col]) for row in range(size)
             for col in range(row + 1, size)),
            default=0.0,
        )
        if max_off_diagonal <= tolerance * max(1.0, max_diagonal):
            order = sorted(range(size), key=lambda index: work[index][index], reverse=True)
            return (
                [work[index][index] for index in order],
                [[vectors[row][index] for index in order] for row in range(size)],
            )

        for left in range(size):
            for right in range(left + 1, size):
                off = work[left][right]
                local_scale = math.sqrt(max(
                    0.0, abs(work[left][left] * work[right][right])))
                if abs(off) <= tolerance * max(1.0, local_scale):
                    continue
                left_value = work[left][left]
                right_value = work[right][right]
                angle = 0.5 * math.atan2(2.0 * off, right_value - left_value)
                cosine, sine = math.cos(angle), math.sin(angle)
                for index in range(size):
                    if index in (left, right):
                        continue
                    old_left = work[index][left]
                    old_right = work[index][right]
                    work[index][left] = work[left][index] = (
                        cosine * old_left - sine * old_right)
                    work[index][right] = work[right][index] = (
                        sine * old_left + cosine * old_right)
                work[left][left] = (
                    cosine * cosine * left_value
                    - 2.0 * sine * cosine * off
                    + sine * sine * right_value)
                work[right][right] = (
                    sine * sine * left_value
                    + 2.0 * sine * cosine * off
                    + cosine * cosine * right_value)
                work[left][right] = work[right][left] = 0.0
                for index in range(size):
                    old_left = vectors[index][left]
                    old_right = vectors[index][right]
                    vectors[index][left] = cosine * old_left - sine * old_right
                    vectors[index][right] = sine * old_left + cosine * old_right
    raise ValueError("Jacobi eigensolver did not converge")


def principal_subspace(vectors, start, end, rank):
    rows = [vectors[layer] for layer in range(start, end + 1)]
    if not 1 <= rank <= len(rows):
        raise ValueError("rank is outside the selected layer band")
    gram = [[dot(left, right) for right in rows] for left in rows]
    eigenvalues, sample_vectors = jacobi_eigen_symmetric(gram)
    total_energy = sum(max(0.0, value) for value in eigenvalues)
    maximum = max(0.0, eigenvalues[0])
    basis = []
    for component in range(rank):
        eigenvalue = max(0.0, eigenvalues[component])
        if eigenvalue <= maximum * 1e-10:
            raise ValueError("requested rank exceeds numerical rank")
        principal = [0.0] * len(rows[0])
        for row_index, row in enumerate(rows):
            coefficient = sample_vectors[row_index][component] / math.sqrt(eigenvalue)
            for column, value in enumerate(row):
                principal[column] += coefficient * value
        for _ in range(2):
            for previous in basis:
                coefficient = dot(principal, previous)
                principal = [value - coefficient * old
                             for value, old in zip(principal, previous)]
        norm = math.sqrt(dot(principal, principal))
        if not math.isfinite(norm) or norm == 0:
            raise ValueError("principal subspace produced a degenerate vector")
        basis.append([value / norm for value in principal])
    return {
        "basis": basis,
        "eigenvalues": eigenvalues[:rank],
        "captured_energy_fraction": sum(eigenvalues[:rank]) / total_energy,
    }


def principal_cosines(left, right):
    cross = [[dot(left_row, right_row) for right_row in right] for left_row in left]
    normal = [[sum(cross[row][left_col] * cross[row][right_col]
                   for row in range(len(cross)))
               for right_col in range(len(right))]
              for left_col in range(len(right))]
    eigenvalues, _ = jacobi_eigen_symmetric(normal)
    return [math.sqrt(max(0.0, min(1.0, value))) for value in eigenvalues]


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("left", type=Path)
    parser.add_argument("right", type=Path)
    parser.add_argument("--band", type=int, nargs=2, default=(56, 73))
    parser.add_argument("--rank", type=int, default=10)
    parser.add_argument("--min-principal-cosine", type=float, default=0.80)
    parser.add_argument("--expected-layers", type=int, default=92)
    parser.add_argument("--expected-width", type=int, default=7168)
    parser.add_argument("--expected-model-hint", default="kimi-k3")
    parser.add_argument("--json", type=Path)
    args = parser.parse_args()
    if not 0 <= args.min_principal_cosine <= 1:
        parser.error("--min-principal-cosine must be in [0, 1]")

    left_metadata, left_vectors = load_vectors(args.left)
    right_metadata, right_vectors = load_vectors(args.right)
    if set(left_vectors) != set(right_vectors):
        raise ValueError("direction layer sets differ")
    widths = ({len(vector) for vector in left_vectors.values()}
              | {len(vector) for vector in right_vectors.values()})
    if len(widths) != 1:
        raise ValueError("direction widths differ")
    width = next(iter(widths))
    if args.expected_layers and len(left_vectors) != args.expected_layers:
        raise ValueError(
            f"direction has {len(left_vectors)} layers, expected {args.expected_layers}")
    if args.expected_width and width != args.expected_width:
        raise ValueError(f"direction width is {width}, expected {args.expected_width}")
    hints = {
        left_metadata.get("controlvector.model_hint"),
        right_metadata.get("controlvector.model_hint"),
    }
    if args.expected_model_hint and hints != {args.expected_model_hint}:
        raise ValueError(
            f"direction model hints are {sorted(repr(value) for value in hints)}, "
            f"expected {args.expected_model_hint!r}")
    start, end = args.band
    if start < 1 or end < start or end > len(left_vectors):
        parser.error("--band is outside the available direction layers")

    left = principal_subspace(left_vectors, start, end, args.rank)
    right = principal_subspace(right_vectors, start, end, args.rank)
    cosines = principal_cosines(left["basis"], right["basis"])
    minimum = min(cosines)
    result = {
        "left": str(args.left),
        "right": str(args.right),
        "left_sha256": sha256(args.left),
        "right_sha256": sha256(args.right),
        "layers": len(left_vectors),
        "width": width,
        "band": [start, end],
        "rank": args.rank,
        "left_eigenvalues": left["eigenvalues"],
        "right_eigenvalues": right["eigenvalues"],
        "left_captured_energy_fraction": left["captured_energy_fraction"],
        "right_captured_energy_fraction": right["captured_energy_fraction"],
        "principal_cosines": cosines,
        "minimum_principal_cosine": minimum,
        "minimum_required_principal_cosine": args.min_principal_cosine,
        "pass": minimum >= args.min_principal_cosine,
    }
    print(
        f"layers={len(left_vectors)} width={width} band={start}-{end} rank={args.rank}; "
        f"energy={left['captured_energy_fraction']:.6f}/{right['captured_energy_fraction']:.6f}; "
        f"principal-cosine min/mean/max={minimum:.6f}/"
        f"{sum(cosines) / len(cosines):.6f}/{max(cosines):.6f}")
    print("principal cosines: " + ",".join(f"{value:.9f}" for value in cosines))
    if args.json:
        args.json.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    if not result["pass"]:
        raise SystemExit(
            f"FAIL: minimum principal cosine {minimum:.6f} is below "
            f"{args.min_principal_cosine:.6f}")


if __name__ == "__main__":
    try:
        main()
    except (EOFError, OSError, ValueError) as exc:
        raise SystemExit(f"FAIL: {exc}") from exc
