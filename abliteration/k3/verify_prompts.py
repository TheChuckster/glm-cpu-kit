#!/usr/bin/env python3
"""Verify the exact canonical K3 direction/evaluation prompt artifacts."""

import argparse
import hashlib
import json
import stat
from pathlib import Path


SOURCE_COMMIT = "9d852fae1a9121c78b29142de733cb1340770cc3"
EXPECTED_MANIFEST_SHA256 = "3786a6cbd4316aa7bca7ffb74b43c57fa0d0f6dc4f3d62478670c2da0d2ec89e"
EXPECTED_COUNTS = {
    "train_harmful": 128,
    "train_harmless": 128,
    "validation_harmful": 32,
    "validation_harmless": 32,
    "test_harmful": 100,
    "test_harmless": 100,
}
EXPECTED_ARTIFACTS = {
    "test.harmful.jsonl": "118fd80d5c58516d2e4b6c62f440329a59e2b558e1039381fd6a6580e9920d93",
    "test.harmless.jsonl": "651571939495d1bd347aa3e32373d24fa0d7e67514bd23dd6770dd9dc43802eb",
    "train.harmful.txt": "2ee6bbcd0a99a60a527c83c83ff6def2704c4dcb67dea449287587ff67a03993",
    "train.harmless.txt": "6b17a2be1ef25dab8e09e4ab2ac7243d15abcefc831836f199f3139b4816c17e",
    "validation.harmful.jsonl": "02e1f942cadae2876fe1fe711072fcc13efb2a951daf1a62797cd1d0303e7656",
    "validation.harmless.jsonl": "d29b99755a0db28a0b8bef71d7243c720d632f437cf318812f5566dcbd3ae498",
}


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require_private(path):
    if stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise ValueError(f"{path} is accessible by group or other users")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()

    require_private(args.directory)
    manifest_path = args.directory / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    require_private(manifest_path)
    actual_manifest_hash = sha256(manifest_path)
    if actual_manifest_hash != EXPECTED_MANIFEST_SHA256:
        raise ValueError(
            f"prompt manifest hash mismatch: {actual_manifest_hash} != "
            f"{EXPECTED_MANIFEST_SHA256}"
        )

    if manifest.get("source_commit") != SOURCE_COMMIT:
        raise ValueError("prompt manifest has the wrong reference commit")
    if manifest.get("seed") != 42:
        raise ValueError("prompt manifest has the wrong sampling seed")
    if manifest.get("counts") != EXPECTED_COUNTS:
        raise ValueError("prompt manifest has non-canonical split counts")
    if manifest.get("artifact_sha256") != EXPECTED_ARTIFACTS:
        raise ValueError("prompt manifest has non-canonical artifact hashes")
    if manifest.get("filtering") != "not applied; K3 has no published refusal-token mapping":
        raise ValueError("prompt manifest has an unexpected filtering methodology")

    for name, expected in EXPECTED_ARTIFACTS.items():
        path = args.directory / name
        require_private(path)
        actual = sha256(path)
        if actual != expected:
            raise ValueError(f"prompt artifact hash mismatch for {path}: {actual} != {expected}")

    print(
        f"PASS: canonical seed-42 prompt artifacts at {SOURCE_COMMIT}; "
        "128+128 train, 32+32 validation, 100+100 final test"
    )


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise SystemExit(f"FAIL: {exc}") from exc
