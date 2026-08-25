#!/usr/bin/env python3
"""Fail-closed verifier for the pre-registered K3 v2 StrongREJECT sample."""

import argparse
import hashlib
import json
import stat
from collections import Counter
from pathlib import Path


EXPECTED = {
    "manifest.json": "59a499522b15a483c0e52ffdfe3c2014e88e56a4acfe2a01bd86815b6e4dc683",
    "test.strongreject.jsonl": "c6b82afc845cdf60241fac404b3f6ac011994110c3268ce8a26739b47494f50f",
}
SOURCE_COMMIT = "9d852fae1a9121c78b29142de733cb1340770cc3"
EXPECTED_CATEGORIES = {
    "Disinformation and deception": 16,
    "Hate, harassment and discrimination": 16,
    "Illegal goods and services": 16,
    "Non-violent crimes": 19,
    "Sexual content": 16,
    "Violence": 17,
}


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("artifact_dir", type=Path)
    args = parser.parse_args()
    actual_names = {path.name for path in args.artifact_dir.iterdir() if path.is_file()}
    if actual_names != set(EXPECTED):
        raise SystemExit(
            f"FAIL: holdout files differ: actual={sorted(actual_names)}, "
            f"expected={sorted(EXPECTED)}")
    for name, expected in EXPECTED.items():
        path = args.artifact_dir / name
        actual = sha256(path)
        if actual != expected:
            raise SystemExit(f"FAIL: {name} hash {actual} != {expected}")
        if stat.S_IMODE(path.stat().st_mode) & 0o077:
            raise SystemExit(f"FAIL: {path} is not private")

    manifest = json.loads((args.artifact_dir / "manifest.json").read_text())
    if manifest.get("source_commit") != SOURCE_COMMIT:
        raise SystemExit("FAIL: holdout source commit changed")
    if manifest.get("seed") != 20260824 or manifest.get("selected_count") != 100:
        raise SystemExit("FAIL: holdout seed/count changed")
    if manifest.get("artifact_sha256") != {
            "test.strongreject.jsonl": EXPECTED["test.strongreject.jsonl"]}:
        raise SystemExit("FAIL: manifest artifact hash changed")
    if manifest.get("selected_category_counts") != EXPECTED_CATEGORIES:
        raise SystemExit("FAIL: holdout category allocation changed")

    rows = []
    for line_number, line in enumerate(
            (args.artifact_dir / "test.strongreject.jsonl").read_text().splitlines(), 1):
        row = json.loads(line)
        if (not isinstance(row.get("id"), str)
                or row.get("label") != "harmful"
                or row.get("split") != "strongreject-confirmatory-v2"
                or not isinstance(row.get("instruction"), str)
                or not isinstance(row.get("source_index"), int)):
            raise SystemExit(f"FAIL: malformed holdout row {line_number}")
        rows.append(row)
    if len(rows) != 100 or len({row["id"] for row in rows}) != 100:
        raise SystemExit("FAIL: holdout does not contain exactly 100 unique IDs")
    if Counter(row["category"] for row in rows) != Counter(EXPECTED_CATEGORIES):
        raise SystemExit("FAIL: JSONL category allocation differs from the manifest")
    if [row["source_index"] for row in rows] != manifest.get(
            "selected_source_indices_in_evaluation_order"):
        raise SystemExit("FAIL: JSONL evaluation order differs from the manifest")
    print("PASS: locked K3 v2 StrongREJECT holdout: 100 prompts, hashes/order/categories verified")


if __name__ == "__main__":
    main()
