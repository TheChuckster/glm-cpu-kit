#!/usr/bin/env python3
"""Prepare reproducible K3 refusal-direction and held-out evaluation prompts.

The source is the dataset shipped with Arditi et al.'s refusal-direction paper.
This script deliberately consumes their existing train/validation/test split,
uses their seed and default sample counts, and never copies evaluation prompts
into the direction-training files.
"""

import argparse
import hashlib
import json
import os
import random
import subprocess
from pathlib import Path


SOURCE_COMMIT = "9d852fae1a9121c78b29142de733cb1340770cc3"
SOURCE_URL = "https://github.com/andyrdt/refusal_direction"
SOURCE_HASHES = {
    "dataset/splits/harmful_train.json": "8f5c0eac0efd2a7f99084bbe8d0de2c465e31b1997184783c917969d9de9ece1",
    "dataset/splits/harmful_val.json": "305f1d1e6dfa6c50a32d24a18ef815f42b5441eb83e6d7767d242107162fd9f4",
    "dataset/splits/harmful_test.json": "5e12ae102c3791dee083a69ab6269a78e033411c629bc3f66f75d2fde196d9ef",
    "dataset/splits/harmless_train.json": "86623b1f8a25aa35df153fc97a556dbcebb6a7c881538ae43ee479ca17f2e002",
    "dataset/splits/harmless_val.json": "772010758e7d771ef4c7e5e4acdfd7598dcece1a6f383f20d382f640913a2a4d",
    "dataset/splits/harmless_test.json": "1b5930ce5e855ada758b3116ce7c4aaea9b9d05f8cdd77b385511d4c84173b19",
    "dataset/processed/jailbreakbench.json": "3a5aefa80c4a35f75a5306303aa8e69801e4ca4584e1f598bd2ddcf3e7fbbcdd",
}


def arguments():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "source",
        type=Path,
        help="checkout of andyrdt/refusal_direction at the pinned commit",
    )
    parser.add_argument("output", type=Path, help="new or empty artifact directory")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--train", type=int, default=128, help="pairs used by the paper (default: 128)")
    parser.add_argument("--validation", type=int, default=32, help="held-out selection prompts per class")
    parser.add_argument("--test", type=int, default=100, help="held-out final prompts per class")
    return parser.parse_args()


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_source(source, relative_path):
    path = source / relative_path
    actual = sha256(path)
    expected = SOURCE_HASHES[relative_path]
    if actual != expected:
        raise SystemExit(f"source hash mismatch for {path}: {actual} != {expected}")
    data = json.loads(path.read_text())
    if not isinstance(data, list) or not all(isinstance(row.get("instruction"), str) for row in data):
        raise SystemExit(f"malformed source split: {path}")
    return data


def line_escape(text):
    """Encode one instruction for cvector-generator's string_process_escapes."""
    return (text.replace("\\", "\\\\")
                .replace("\n", "\\n")
                .replace("\r", "\\r")
                .replace("\t", "\\t"))


def write_prompt_file(path, rows):
    path.write_text("".join(line_escape(row["instruction"]) + "\n" for row in rows))


def write_jsonl(path, rows, split, label):
    with path.open("w") as output:
        for index, row in enumerate(rows):
            item = {
                "id": f"{split}-{label}-{index:03d}",
                "split": split,
                "label": label,
                "category": row.get("category"),
                "instruction": row["instruction"],
            }
            output.write(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n")


def checked_sample(rng, rows, count, name):
    if count < 1 or count > len(rows):
        raise SystemExit(f"requested {count} rows from {name}, which contains {len(rows)}")
    return rng.sample(rows, count)


def main():
    os.umask(0o077)
    args = arguments()
    if args.output.exists() and any(args.output.iterdir()):
        raise SystemExit(f"refusing to overwrite non-empty artifact directory: {args.output}")

    try:
        commit = subprocess.run(
            ["git", "-C", str(args.source), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise SystemExit(f"cannot verify source checkout: {exc}") from exc
    if commit != SOURCE_COMMIT:
        raise SystemExit(f"source checkout is {commit}; expected pinned commit {SOURCE_COMMIT}")

    datasets = {
        relative_path.removeprefix("dataset/splits/").removesuffix(".json"):
            load_source(args.source, relative_path)
        for relative_path in SOURCE_HASHES
        if relative_path.startswith("dataset/splits/")
    }
    jailbreakbench = load_source(args.source, "dataset/processed/jailbreakbench.json")

    # Match load_and_sample_datasets() in the reference implementation exactly:
    # one seed followed by these four sample calls in this order. Its final
    # harmful evaluation uses all of JailbreakBench; its harmless evaluation
    # then samples harmless_test from the still-advancing Python RNG.
    rng = random.Random(args.seed)
    train_harmful = checked_sample(rng, datasets["harmful_train"], args.train, "harmful_train")
    train_harmless = checked_sample(rng, datasets["harmless_train"], args.train, "harmless_train")
    val_harmful = checked_sample(rng, datasets["harmful_val"], args.validation, "harmful_val")
    val_harmless = checked_sample(rng, datasets["harmless_val"], args.validation, "harmless_val")
    if args.test != len(jailbreakbench):
        raise SystemExit(
            f"canonical harmful evaluation contains {len(jailbreakbench)} JailbreakBench prompts; "
            f"requested --test {args.test}"
        )
    test_harmful = jailbreakbench
    test_harmless = checked_sample(rng, datasets["harmless_test"], args.test, "harmless_test")

    selected = {
        "train_harmful": train_harmful,
        "train_harmless": train_harmless,
        "validation_harmful": val_harmful,
        "validation_harmless": val_harmless,
        "test_harmful": test_harmful,
        "test_harmless": test_harmless,
    }
    instruction_sets = {name: {row["instruction"] for row in rows} for name, rows in selected.items()}
    for left, left_set in instruction_sets.items():
        if len(left_set) != len(selected[left]):
            raise SystemExit(f"duplicate instruction in sampled set {left}")
        for right, right_set in instruction_sets.items():
            if left < right and left_set & right_set:
                raise SystemExit(f"instruction leakage between {left} and {right}")

    args.output.mkdir(parents=True, exist_ok=True)
    write_prompt_file(args.output / "train.harmful.txt", train_harmful)
    write_prompt_file(args.output / "train.harmless.txt", train_harmless)
    write_jsonl(args.output / "validation.harmful.jsonl", val_harmful, "validation", "harmful")
    write_jsonl(args.output / "validation.harmless.jsonl", val_harmless, "validation", "harmless")
    write_jsonl(args.output / "test.harmful.jsonl", test_harmful, "jailbreakbench", "harmful")
    write_jsonl(args.output / "test.harmless.jsonl", test_harmless, "test", "harmless")

    files = sorted(path for path in args.output.iterdir() if path.is_file())
    manifest = {
        "method": "Arditi et al. final templated-prompt-position difference in means",
        "source_url": SOURCE_URL,
        "source_commit": SOURCE_COMMIT,
        "source_sha256": SOURCE_HASHES,
        "seed": args.seed,
        "counts": {name: len(rows) for name, rows in selected.items()},
        "selection_order": list(selected),
        "filtering": "not applied; K3 has no published refusal-token mapping",
        "evaluation": {
            "harmful": "all 100 dataset/processed/jailbreakbench.json prompts, in source order",
            "harmless": "100 seeded samples from dataset/splits/harmless_test.json after the four canonical train/validation sample calls",
        },
        "artifact_sha256": {path.name: sha256(path) for path in files},
    }
    (args.output / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")

    print(f"prepared {args.train} training pairs, {2 * args.validation} validation prompts, "
          f"and {2 * args.test} test prompts in {args.output}")
    for path in sorted(args.output.iterdir()):
        print(f"{sha256(path)}  {path.name}")


if __name__ == "__main__":
    main()
