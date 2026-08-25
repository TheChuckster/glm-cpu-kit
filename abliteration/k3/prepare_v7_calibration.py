#!/usr/bin/env python3
"""Materialize the fixed, already-consumed K3 v7 calibration subsets."""

import argparse
import hashlib
import json
import os
from pathlib import Path


SOURCE_SHA256 = "118fd80d5c58516d2e4b6c62f440329a59e2b558e1039381fd6a6580e9920d93"
FAILURE_IDS = (
    "jailbreakbench-harmful-000",
    "jailbreakbench-harmful-002",
)
STABILITY_IDS = (
    "jailbreakbench-harmful-006",
    "jailbreakbench-harmful-010",
)
V2_DECISIONS_SHA256 = "3aa670c1abf2c602e907361064d7048874bac2990ea36a3b024a76c92e1c442a"
V2_REVIEW_SHA256 = "6ad1a77c55d387d54dfdbd083aa91757b6f354db8004dcab64ee68a828827a03"
V2_RESULTS_SHA256 = "c1133227ff4d30bed46a2a16cd3b1b2019934223460add1a2ced114096d95c82"


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_rows(path):
    rows = []
    seen = set()
    for line_number, line in enumerate(path.read_text().splitlines(), 1):
        if not line.strip():
            continue
        row = json.loads(line)
        row_id = row.get("id")
        if not isinstance(row_id, str) or not isinstance(row.get("instruction"), str):
            raise ValueError(f"{path}:{line_number}: missing string id/instruction")
        if row.get("label") != "harmful":
            raise ValueError(f"{path}:{line_number}: expected harmful label")
        if row_id in seen:
            raise ValueError(f"{path}:{line_number}: duplicate id {row_id}")
        seen.add(row_id)
        rows.append(row)
    if len(rows) != 100:
        raise ValueError(f"expected exactly 100 canonical rows, found {len(rows)}")
    return rows


def select_rows(rows, selected_ids):
    by_id = {row["id"]: row for row in rows}
    missing = [row_id for row_id in selected_ids if row_id not in by_id]
    if missing:
        raise ValueError(f"missing selected ids: {missing}")
    return [by_id[row_id] for row_id in selected_ids]


def encode_jsonl(rows):
    return b"".join(
        (json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n").encode()
        for row in rows
    )


def write_exclusive(path, payload):
    with path.open("xb") as output:
        os.chmod(path, 0o600)
        output.write(payload)
        output.flush()
        os.fsync(output.fileno())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()

    os.umask(0o077)
    if sha256(args.source) != SOURCE_SHA256:
        raise SystemExit("canonical harmful source hash mismatch")
    if args.output_dir.exists():
        raise SystemExit(f"refusing existing output directory: {args.output_dir}")

    rows = load_rows(args.source)
    failures = encode_jsonl(select_rows(rows, FAILURE_IDS))
    stability = encode_jsonl(select_rows(rows, STABILITY_IDS))

    args.output_dir.mkdir(mode=0o700)
    failures_path = args.output_dir / "calibration.failures.jsonl"
    stability_path = args.output_dir / "calibration.stability.jsonl"
    write_exclusive(failures_path, failures)
    write_exclusive(stability_path, stability)

    manifest = {
        "schema_version": 1,
        "source_sha256": SOURCE_SHA256,
        "source_row_count": len(rows),
        "failure_ids": list(FAILURE_IDS),
        "failure_sha256": hashlib.sha256(failures).hexdigest(),
        "stability_ids": list(STABILITY_IDS),
        "stability_sha256": hashlib.sha256(stability).hexdigest(),
        "v2_decisions_sha256": V2_DECISIONS_SHA256,
        "v2_manual_review_sha256": V2_REVIEW_SHA256,
        "v2_results_sha256": V2_RESULTS_SHA256,
    }
    manifest_payload = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
    write_exclusive(args.output_dir / "manifest.json", manifest_payload)
    print(json.dumps(manifest, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
