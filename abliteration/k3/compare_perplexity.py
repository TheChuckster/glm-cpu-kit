#!/usr/bin/env python3
"""Compare paired llama-perplexity logs and enforce the K3 quality gate."""

import argparse
import json
import math
import re
from pathlib import Path


FINAL = re.compile(
    r"Final estimate:\s*PPL(?:\s+over\s+[^\n]*)?\s*=\s*"
    r"([0-9.eE+-]+)\s*\+/-\s*([0-9.eE+-]+)"
)
PAIRED_META_KEYS = (
    "binary_sha256",
    "runtime_libraries_sha256",
    "runner_sha256",
    "corpus_sha256",
    "threads",
    "arguments",
)


def load(path):
    matches = FINAL.findall(path.read_text(errors="replace"))
    if len(matches) != 1:
        raise ValueError(f"{path}: expected exactly one final estimate, found {len(matches)}")
    mean, error = map(float, matches[0])
    if not (math.isfinite(mean) and mean > 0 and math.isfinite(error) and error > 0):
        raise ValueError(f"{path}: invalid PPL estimate {mean} +/- {error}")
    return mean, error


def load_meta(log_path):
    path = log_path.with_suffix(".meta")
    result = {}
    for line_number, line in enumerate(path.read_text().splitlines(), 1):
        if "=" not in line:
            raise ValueError(f"{path}:{line_number}: malformed metadata line")
        key, value = line.split("=", 1)
        if key in result:
            raise ValueError(f"{path}:{line_number}: duplicate metadata key {key!r}")
        result[key] = value
    required = set(PAIRED_META_KEYS)
    missing = required - set(result)
    if missing:
        raise ValueError(f"{path}: missing metadata keys {sorted(missing)}")
    return path, result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("baseline", type=Path)
    parser.add_argument("candidate", type=Path)
    parser.add_argument(
        "--max-increase",
        type=float,
        help="maximum absolute PPL increase; defaults to one baseline error bar",
    )
    parser.add_argument("--max-error-ratio", type=float, default=1.25)
    parser.add_argument("--json", type=Path)
    args = parser.parse_args()
    if args.max_increase is not None and args.max_increase < 0:
        parser.error("--max-increase must be non-negative")
    if args.max_error_ratio < 1:
        parser.error("--max-error-ratio must be at least 1")

    baseline_mean, baseline_error = load(args.baseline)
    candidate_mean, candidate_error = load(args.candidate)
    baseline_meta_path, baseline_meta = load_meta(args.baseline)
    candidate_meta_path, candidate_meta = load_meta(args.candidate)
    for key in PAIRED_META_KEYS:
        if baseline_meta[key] != candidate_meta[key]:
            raise ValueError(
                f"paired perplexity metadata differs for {key}: "
                f"{baseline_meta_path} != {candidate_meta_path}"
            )
    max_increase = baseline_error if args.max_increase is None else args.max_increase
    increase = candidate_mean - baseline_mean
    failures = []
    if increase > max_increase + 1e-12:
        failures.append(
            f"PPL increase {increase:.6f} exceeds allowed {max_increase:.6f}"
        )
    if candidate_error > baseline_error * args.max_error_ratio + 1e-12:
        failures.append(
            f"candidate error {candidate_error:.6f} exceeds "
            f"{args.max_error_ratio:.2f}x baseline error"
        )

    result = {
        "baseline": {"mean": baseline_mean, "error": baseline_error},
        "candidate": {"mean": candidate_mean, "error": candidate_error},
        "increase": increase,
        "maximum_allowed_increase": max_increase,
        "maximum_error_ratio": args.max_error_ratio,
        "paired_provenance": {
            key: baseline_meta[key]
            for key in PAIRED_META_KEYS
        },
        "failures": failures,
        "pass": not failures,
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    if args.json:
        args.json.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    raise SystemExit(1 if failures else 0)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError) as exc:
        raise SystemExit(f"FAIL: {exc}") from exc
