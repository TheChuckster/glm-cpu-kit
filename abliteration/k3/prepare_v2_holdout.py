#!/usr/bin/env python3
"""Lock a fresh, non-overlapping StrongREJECT confirmation sample for K3 v2."""

import argparse
import hashlib
import json
import os
import subprocess
from collections import Counter
from pathlib import Path


SOURCE_COMMIT = "9d852fae1a9121c78b29142de733cb1340770cc3"
SOURCE_URL = "https://github.com/andyrdt/refusal_direction"
SOURCE_HASHES = {
    "dataset/processed/strongreject.json": "9786304587f3d860ab01d1dd2de4aff721ed7538ce4e2af715cbb74efe2a6f10",
    "dataset/splits/harmful_train.json": "8f5c0eac0efd2a7f99084bbe8d0de2c465e31b1997184783c917969d9de9ece1",
    "dataset/splits/harmful_val.json": "305f1d1e6dfa6c50a32d24a18ef815f42b5441eb83e6d7767d242107162fd9f4",
    "dataset/processed/jailbreakbench.json": "3a5aefa80c4a35f75a5306303aa8e69801e4ca4584e1f598bd2ddcf3e7fbbcdd",
}
SEED = 20260824
COUNT = 100


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def normalized(text):
    return " ".join(text.casefold().split())


def load(source, relative):
    path = source / relative
    actual = sha256(path)
    if actual != SOURCE_HASHES[relative]:
        raise SystemExit(f"source hash mismatch for {path}: {actual}")
    rows = json.loads(path.read_text())
    if not isinstance(rows, list) or not all(
            isinstance(row, dict) and isinstance(row.get("instruction"), str)
            for row in rows):
        raise SystemExit(f"malformed source dataset: {path}")
    return rows


def score(category, index, instruction):
    value = f"{SEED}\0{category}\0{index}\0{instruction}".encode()
    return hashlib.sha256(value).hexdigest()


def quotas(counts, total):
    population = sum(counts.values())
    exact = {category: total * count / population for category, count in counts.items()}
    result = {category: int(value) for category, value in exact.items()}
    remaining = total - sum(result.values())
    order = sorted(counts, key=lambda category: (-(exact[category] - result[category]), category))
    for category in order[:remaining]:
        result[category] += 1
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path, help="pinned refusal_direction checkout")
    parser.add_argument("output", type=Path, help="new or empty artifact directory")
    args = parser.parse_args()
    os.umask(0o077)
    if args.output.exists() and any(args.output.iterdir()):
        raise SystemExit(f"refusing to overwrite non-empty output: {args.output}")
    commit = subprocess.run(
        ["git", "-C", str(args.source), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True).stdout.strip()
    if commit != SOURCE_COMMIT:
        raise SystemExit(f"source checkout is {commit}; expected {SOURCE_COMMIT}")

    strongreject = load(args.source, "dataset/processed/strongreject.json")
    comparison = []
    for relative in (
            "dataset/splits/harmful_train.json",
            "dataset/splits/harmful_val.json",
            "dataset/processed/jailbreakbench.json"):
        comparison.extend(load(args.source, relative))
    forbidden = {normalized(row["instruction"]) for row in comparison}

    eligible = []
    seen = set()
    overlap_count = 0
    for source_index, row in enumerate(strongreject):
        instruction = normalized(row["instruction"])
        if instruction in forbidden:
            overlap_count += 1
            continue
        if instruction in seen:
            raise SystemExit(f"duplicate eligible StrongREJECT instruction at index {source_index}")
        seen.add(instruction)
        category = row.get("category")
        if not isinstance(category, str) or not category:
            raise SystemExit(f"StrongREJECT row {source_index} has no category")
        eligible.append({**row, "source_index": source_index})

    counts = Counter(row["category"] for row in eligible)
    allocation = quotas(counts, COUNT)
    selected = []
    for category in sorted(counts):
        rows = [row for row in eligible if row["category"] == category]
        rows.sort(key=lambda row: score(
            category, row["source_index"], row["instruction"]))
        selected.extend(rows[:allocation[category]])
    selected.sort(key=lambda row: score(
        row["category"], row["source_index"], row["instruction"]))
    if len(selected) != COUNT:
        raise SystemExit(f"selection produced {len(selected)} rows, expected {COUNT}")

    args.output.mkdir(parents=True, exist_ok=True)
    prompt_path = args.output / "test.strongreject.jsonl"
    with prompt_path.open("w") as output:
        for row in selected:
            item = {
                "id": f"strongreject-harmful-{row['source_index']:03d}",
                "split": "strongreject-confirmatory-v2",
                "label": "harmful",
                "category": row["category"],
                "source_index": row["source_index"],
                "instruction": row["instruction"],
            }
            output.write(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n")
    os.chmod(prompt_path, 0o600)

    manifest = {
        "source_url": SOURCE_URL,
        "source_commit": SOURCE_COMMIT,
        "source_sha256": SOURCE_HASHES,
        "method": (
            "exclude normalized exact overlap with direction harmful train/validation "
            "and canonical JailbreakBench; allocate 100 proportionally by category "
            "using largest remainders; choose/order by SHA-256 of seed, category, "
            "source index, and exact instruction"
        ),
        "seed": SEED,
        "source_count": len(strongreject),
        "overlap_excluded_count": overlap_count,
        "eligible_count": len(eligible),
        "selected_count": len(selected),
        "eligible_category_counts": dict(sorted(counts.items())),
        "selected_category_counts": dict(sorted(Counter(
            row["category"] for row in selected).items())),
        "selected_source_indices_in_evaluation_order": [
            row["source_index"] for row in selected],
        "artifact_sha256": {prompt_path.name: sha256(prompt_path)},
    }
    manifest_path = args.output / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    os.chmod(manifest_path, 0o600)
    print(f"prepared {COUNT} StrongREJECT prompts from {len(eligible)} eligible rows")
    print(f"{sha256(prompt_path)}  {prompt_path.name}")
    print(f"{sha256(manifest_path)}  {manifest_path.name}")


if __name__ == "__main__":
    main()
