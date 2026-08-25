#!/usr/bin/env python3
"""Materialize V10's fixed three-phase canonical development partition."""

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
FILENAMES = {
    "failures": "calibration.failures.jsonl",
    "stability": "calibration.stability.jsonl",
    "remainder": "calibration.remainder.jsonl",
}


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
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{line_number}: invalid JSON: {exc}") from exc
        row_id = row.get("id")
        if not isinstance(row_id, str) or not isinstance(row.get("instruction"), str):
            raise ValueError(f"{path}:{line_number}: missing string id/instruction")
        if row.get("label") != "harmful":
            raise ValueError(f"{path}:{line_number}: expected harmful label")
        if row_id in seen:
            raise ValueError(f"{path}:{line_number}: duplicate id {row_id}")
        seen.add(row_id)
        rows.append(row)
    expected_ids = [f"jailbreakbench-harmful-{index:03d}" for index in range(100)]
    if [row["id"] for row in rows] != expected_ids:
        raise ValueError("canonical harmful IDs, count, or source order changed")
    return rows


def partition_rows(rows):
    by_id = {row["id"]: row for row in rows}
    selected = set(FAILURE_IDS + STABILITY_IDS)
    if len(by_id) != 100 or not selected <= set(by_id):
        raise ValueError("canonical rows do not contain V10's exact calibration IDs")
    partitions = {
        "failures": [by_id[row_id] for row_id in FAILURE_IDS],
        "stability": [by_id[row_id] for row_id in STABILITY_IDS],
        "remainder": [row for row in rows if row["id"] not in selected],
    }
    flattened = (
        partitions["failures"]
        + partitions["stability"]
        + partitions["remainder"]
    )
    if len(flattened) != 100 or {row["id"] for row in flattened} != set(by_id):
        raise ValueError("V10 calibration partition is not an exact canonical cover")
    return partitions


def encode_jsonl(rows):
    return b"".join(
        (json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n").encode()
        for row in rows
    )


def write_exclusive(path, payload):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()

    os.umask(0o077)
    if sha256(args.source) != SOURCE_SHA256:
        parser.error("canonical harmful source hash mismatch")
    if args.output_dir.exists():
        parser.error(f"refusing existing output directory: {args.output_dir}")

    rows = load_rows(args.source)
    partitions = partition_rows(rows)
    payloads = {phase: encode_jsonl(partition) for phase, partition in partitions.items()}
    manifest = {
        "schema": "k3-v10-calibration-partition-v1",
        "source": {
            "bytes": args.source.stat().st_size,
            "rows": len(rows),
            "sha256": SOURCE_SHA256,
        },
        "phase_order": ["failures", "stability", "remainder"],
        "phases": {
            phase: {
                "file": FILENAMES[phase],
                "bytes": len(payloads[phase]),
                "rows": len(partitions[phase]),
                "ids": [row["id"] for row in partitions[phase]],
                "sha256": hashlib.sha256(payloads[phase]).hexdigest(),
            }
            for phase in ("failures", "stability", "remainder")
        },
    }
    manifest_payload = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()

    args.output_dir.mkdir(mode=0o700)
    try:
        for phase, payload in payloads.items():
            write_exclusive(args.output_dir / FILENAMES[phase], payload)
        write_exclusive(args.output_dir / "manifest.json", manifest_payload)
    except BaseException:
        raise
    print(json.dumps(manifest, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
