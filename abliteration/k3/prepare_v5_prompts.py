#!/usr/bin/env python3
"""Prepare K3 v5 manifold-extraction prompts from the pinned Apache-2.0 source.

V5 deliberately treats canonical JailbreakBench as development/training data:
it exposed the semantic-deflection failures in v2-v4 and is no longer an
untouched confirmation set. The three already sealed StrongREJECT sets are not
read or copied by this script.
"""

import argparse
import hashlib
import json
import os
import random
import stat
import subprocess
from pathlib import Path


SOURCE_URL = "https://github.com/andyrdt/refusal_direction"
SOURCE_COMMIT = "9d852fae1a9121c78b29142de733cb1340770cc3"
SOURCE_HASHES = {
    "LICENSE": "c71d239df91726fc519c6eb72d318ec65820627232b2f796219e87dcf35d0ab4",
    "dataset/splits/harmful_train.json": "8f5c0eac0efd2a7f99084bbe8d0de2c465e31b1997184783c917969d9de9ece1",
    "dataset/splits/harmless_train.json": "86623b1f8a25aa35df153fc97a556dbcebb6a7c881538ae43ee479ca17f2e002",
    "dataset/processed/jailbreakbench.json": "3a5aefa80c4a35f75a5306303aa8e69801e4ca4584e1f598bd2ddcf3e7fbbcdd",
}
SEED = 20260827


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load(source, relative):
    path = source / relative
    actual = sha256(path)
    if actual != SOURCE_HASHES[relative]:
        raise ValueError(f"source hash changed for {relative}: {actual}")
    if relative == "LICENSE":
        return None
    rows = json.loads(path.read_text())
    if (not isinstance(rows, list)
            or not all(isinstance(row.get("instruction"), str) for row in rows)):
        raise ValueError(f"malformed source dataset {relative}")
    return rows


def normalized(text):
    return " ".join(text.casefold().split())


def escaped(text):
    return (text.replace("\\", "\\\\")
                .replace("\n", "\\n")
                .replace("\r", "\\r")
                .replace("\t", "\\t"))


def write_txt(path, rows):
    path.write_text("".join(escaped(row["instruction"]) + "\n" for row in rows))


def write_jsonl(path, rows, label):
    with path.open("w") as output:
        for index, row in enumerate(rows):
            output.write(json.dumps({
                "id": f"v5-manifold-{label}-{index:03d}",
                "label": label,
                "source": row["v5_source"],
                "source_index": row["v5_source_index"],
                "category": row.get("category"),
                "instruction": row["instruction"],
            }, ensure_ascii=False, sort_keys=True) + "\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    os.umask(0o077)

    commit = subprocess.run(
        ["git", "-C", str(args.source), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True).stdout.strip()
    if commit != SOURCE_COMMIT:
        raise ValueError(f"source checkout is {commit}; expected {SOURCE_COMMIT}")
    if args.output.exists() and any(args.output.iterdir()):
        raise ValueError(f"refusing to overwrite non-empty {args.output}")

    load(args.source, "LICENSE")
    harmful_train = load(args.source, "dataset/splits/harmful_train.json")
    harmless_train = load(args.source, "dataset/splits/harmless_train.json")
    canonical = load(args.source, "dataset/processed/jailbreakbench.json")

    canonical_keys = {normalized(row["instruction"]) for row in canonical}
    if len(canonical) != 100 or len(canonical_keys) != 100:
        raise ValueError("canonical JailbreakBench is not 100 unique prompts")
    unique_harmful_train = [
        (source_index, row) for source_index, row in enumerate(harmful_train)
        if normalized(row["instruction"]) not in canonical_keys
    ]
    overlap = len(harmful_train) - len(unique_harmful_train)
    if len(harmful_train) != 260 or overlap != 1:
        raise ValueError(
            f"expected 260 harmful-train rows and one canonical overlap; got overlap={overlap}")

    harmful = []
    for source_index, row in enumerate(canonical):
        harmful.append({**row, "v5_source": "jailbreakbench",
                        "v5_source_index": source_index})
    for source_index, row in unique_harmful_train:
        harmful.append({**row, "v5_source": "harmful_train",
                        "v5_source_index": source_index})
    harmful_keys = {normalized(row["instruction"]) for row in harmful}
    if len(harmful) != 359 or len(harmful_keys) != 359:
        raise ValueError("v5 harmful manifold set is not 359 unique prompts")

    rng = random.Random(SEED)
    harmless_indices = rng.sample(range(len(harmless_train)), len(harmful))
    harmless = [{**harmless_train[source_index], "v5_source": "harmless_train",
                 "v5_source_index": source_index}
                for source_index in harmless_indices]
    harmless_keys = {normalized(row["instruction"]) for row in harmless}
    if len(harmless_keys) != len(harmless) or harmful_keys & harmless_keys:
        raise ValueError("duplicate or cross-class prompt in v5 manifold set")

    args.output.mkdir(parents=True, exist_ok=True)
    write_txt(args.output / "train.harmful.txt", harmful)
    write_txt(args.output / "train.harmless.txt", harmless)
    write_jsonl(args.output / "train.harmful.jsonl", harmful, "harmful")
    write_jsonl(args.output / "train.harmless.jsonl", harmless, "harmless")

    artifacts = sorted(path for path in args.output.iterdir() if path.is_file())
    manifest = {
        "method": (
            "SOM manifold training: all 100 canonical JailbreakBench development "
            "prompts followed by all 260 published harmful-train prompts except "
            "their one exact canonical duplicate; pair with a deterministic 359-row "
            "sample of published harmless-train prompts"
        ),
        "source_url": SOURCE_URL,
        "source_commit": SOURCE_COMMIT,
        "source_sha256": SOURCE_HASHES,
        "source_license": "Apache-2.0 repository LICENSE (hash pinned above)",
        "seed": SEED,
        "counts": {
            "canonical_development": 100,
            "harmful_train_source": 260,
            "harmful_train_canonical_overlap_excluded": 1,
            "train_harmful_unique": 359,
            "train_harmless_sample": 359,
        },
        "ordering": "100 canonical rows in source order, then 259 non-overlapping harmful_train rows in source order",
        "sealed_holdouts": "not read by this script",
        "artifact_sha256": {path.name: sha256(path) for path in artifacts},
    }
    manifest_path = args.output / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    for path in args.output.iterdir():
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    print(f"prepared {len(harmful)} harmful + {len(harmless)} harmless v5 manifold prompts")
    for path in sorted(args.output.iterdir()):
        print(f"{sha256(path)}  {path.name}")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        raise SystemExit(f"FAIL: {exc}") from exc
