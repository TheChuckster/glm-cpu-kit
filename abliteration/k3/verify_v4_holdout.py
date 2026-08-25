#!/usr/bin/env python3
"""Fail closed unless K3 v4's final untouched StrongREJECT remainder is exact."""

import argparse
import hashlib
import json
import stat
from collections import Counter
from pathlib import Path


EXPECTED = {
    "manifest.json": "32bfac6df6a8297e025f0edb1fe6876090a47601ca1a019b5e89beddeb2350ff",
    "test.strongreject.jsonl": "5a986a9b5997b9a19a98411a1f6295e9a5cd34f8feb5d032efe7f002734d861c",
}
PRIOR_HASHES = {
    "v2": {
        "manifest.json": "59a499522b15a483c0e52ffdfe3c2014e88e56a4acfe2a01bd86815b6e4dc683",
        "test.strongreject.jsonl": "c6b82afc845cdf60241fac404b3f6ac011994110c3268ce8a26739b47494f50f",
    },
    "v3": {
        "manifest.json": "6c2be9666d787ba7476c96420716eda7f078a14c54e3b4619017377853771245",
        "test.strongreject.jsonl": "71dfeb597c2eb74809c6ec479ae66b62917484224f1a7cf5947109bbcfca4b49",
    },
}
SOURCE_COMMIT = "9d852fae1a9121c78b29142de733cb1340770cc3"
EXPECTED_CATEGORIES = {
    "Disinformation and deception": 18,
    "Hate, harassment and discrimination": 18,
    "Illegal goods and services": 17,
    "Non-violent crimes": 21,
    "Sexual content": 17,
    "Violence": 19,
}


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def normalized(text):
    return " ".join(text.casefold().split())


def load_rows(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("artifact_dir", type=Path)
    parser.add_argument(
        "--v2-holdout", type=Path,
        default=Path("/models/.abliteration/k3/v2-holdout"))
    parser.add_argument(
        "--v3-holdout", type=Path,
        default=Path("/models/.abliteration/k3/v3-holdout"))
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
    if (manifest.get("seed") != 20260826
            or manifest.get("source_count") != 313
            or manifest.get("selected_count") != 110):
        raise SystemExit("FAIL: holdout seed/source/selection count changed")
    if (manifest.get("canonical_overlap_excluded_count") != 3
            or manifest.get("prior_v2_excluded_count") != 100
            or manifest.get("prior_v3_excluded_count") != 100
            or manifest.get("eligible_count") != 110):
        raise SystemExit("FAIL: holdout exclusion/eligibility counts changed")
    if manifest.get("prior_holdout_sha256") != PRIOR_HASHES:
        raise SystemExit("FAIL: prior holdout binding changed")
    if manifest.get("artifact_sha256") != {
            "test.strongreject.jsonl": EXPECTED["test.strongreject.jsonl"]}:
        raise SystemExit("FAIL: manifest artifact binding changed")
    if manifest.get("selected_category_counts") != EXPECTED_CATEGORIES:
        raise SystemExit("FAIL: holdout category allocation changed")

    rows = load_rows(args.artifact_dir / "test.strongreject.jsonl")
    for line_number, row in enumerate(rows, 1):
        if (not isinstance(row.get("id"), str)
                or row.get("label") != "harmful"
                or row.get("split") != "strongreject-confirmatory-v4"
                or not isinstance(row.get("instruction"), str)
                or not isinstance(row.get("source_index"), int)):
            raise SystemExit(f"FAIL: malformed holdout row {line_number}")
    if (len(rows) != 110 or len({row["id"] for row in rows}) != 110
            or len({normalized(row["instruction"]) for row in rows}) != 110):
        raise SystemExit("FAIL: holdout does not contain exactly 110 unique rows")
    if Counter(row["category"] for row in rows) != Counter(EXPECTED_CATEGORIES):
        raise SystemExit("FAIL: JSONL category allocation differs from manifest")
    if [row["source_index"] for row in rows] != manifest.get(
            "selected_source_indices_in_evaluation_order"):
        raise SystemExit("FAIL: JSONL evaluation order differs from manifest")

    current = {normalized(row["instruction"]) for row in rows}
    priors = set()
    for version, directory in (("v2", args.v2_holdout), ("v3", args.v3_holdout)):
        expected = PRIOR_HASHES[version]
        actual_names = {path.name for path in directory.iterdir() if path.is_file()}
        if actual_names != set(expected):
            raise SystemExit(f"FAIL: prior {version} holdout files differ")
        for name, expected_hash in expected.items():
            path = directory / name
            if (sha256(path) != expected_hash
                    or stat.S_IMODE(path.stat().st_mode) & 0o077):
                raise SystemExit(f"FAIL: prior {version} binding differs: {path}")
        prior_rows = load_rows(directory / "test.strongreject.jsonl")
        instructions = {normalized(row.get("instruction", "")) for row in prior_rows}
        if len(instructions) != 100 or priors & instructions or current & instructions:
            raise SystemExit(f"FAIL: {version} overlap or duplication detected")
        priors |= instructions
    print(
        "PASS: locked K3 v4 StrongREJECT remainder: 110 prompts, "
        "hashes/order/categories/prior disjointness verified")


if __name__ == "__main__":
    main()
