#!/usr/bin/env python3
"""Fail closed unless the exact private K3 v5 manifold prompts are present."""

import argparse
import hashlib
import json
import stat
from pathlib import Path


EXPECTED = {
    "manifest.json": "f4ea340c455c103d8cfef990e552ddae5474ef3f9e8eca62c3ab09d213b93af0",
    "train.harmful.jsonl": "99d680ee8887bf8f912b09dde3a7b99a6be7a9dc11a15abef267bfcbaf6efa31",
    "train.harmful.txt": "98c044a2dd14047259d7c7e36d4464d125efabf54b3638904a41a73263b01cf8",
    "train.harmless.jsonl": "227ae09df31d16674bc73a23860c122e52cbc5becb54d9504ef75f7189f7041d",
    "train.harmless.txt": "6583c21a1668832301ab62c33e5dffa32d49511a049a2b30acd0f59ca20f3dfc",
}
SOURCE_COMMIT = "9d852fae1a9121c78b29142de733cb1340770cc3"
SEED = 20260827


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_jsonl(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("artifact_dir", type=Path)
    args = parser.parse_args()

    names = {path.name for path in args.artifact_dir.iterdir() if path.is_file()}
    if names != set(EXPECTED):
        raise ValueError(f"artifact names changed: {sorted(names)}")
    for name, expected in EXPECTED.items():
        path = args.artifact_dir / name
        actual = sha256(path)
        if actual != expected:
            raise ValueError(f"{name} hash {actual} != {expected}")
        if stat.S_IMODE(path.stat().st_mode) & 0o077:
            raise ValueError(f"{path} is not private")

    manifest = json.loads((args.artifact_dir / "manifest.json").read_text())
    if manifest.get("source_commit") != SOURCE_COMMIT or manifest.get("seed") != SEED:
        raise ValueError("source commit or seed changed")
    if manifest.get("sealed_holdouts") != "not read by this script":
        raise ValueError("sealed-holdout declaration changed")
    if manifest.get("counts") != {
            "canonical_development": 100,
            "harmful_train_canonical_overlap_excluded": 1,
            "harmful_train_source": 260,
            "train_harmful_unique": 359,
            "train_harmless_sample": 359,
    }:
        raise ValueError("prompt counts changed")
    if manifest.get("artifact_sha256") != {
            name: digest for name, digest in EXPECTED.items() if name != "manifest.json"}:
        raise ValueError("manifest artifact binding changed")

    harmful = load_jsonl(args.artifact_dir / "train.harmful.jsonl")
    harmless = load_jsonl(args.artifact_dir / "train.harmless.jsonl")
    if len(harmful) != 359 or len(harmless) != 359:
        raise ValueError("JSONL count changed")
    if [row.get("source") for row in harmful[:100]] != ["jailbreakbench"] * 100:
        raise ValueError("canonical development prefix changed")
    if [row.get("source_index") for row in harmful[:100]] != list(range(100)):
        raise ValueError("canonical source order changed")
    if {row.get("source") for row in harmful[100:]} != {"harmful_train"}:
        raise ValueError("harmful-train suffix changed")
    if {row.get("source") for row in harmless} != {"harmless_train"}:
        raise ValueError("harmless source changed")
    for label, rows in (("harmful", harmful), ("harmless", harmless)):
        if (any(row.get("label") != label or not isinstance(row.get("instruction"), str)
                or not isinstance(row.get("source_index"), int) for row in rows)
                or len({row["id"] for row in rows}) != len(rows)
                or len({" ".join(row["instruction"].casefold().split()) for row in rows}) != len(rows)):
            raise ValueError(f"malformed or duplicate {label} rows")
    print("PASS: exact private v5 manifold prompts: 359 harmful + 359 harmless")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise SystemExit(f"FAIL: {exc}") from exc
