#!/usr/bin/env python3
"""Materialize pinned validation JSONL as private cvector input files."""

import argparse
import json
import os
from pathlib import Path

from prepare_prompts import line_escape


def render(path, expected_label):
    rows = []
    ids = set()
    for line_number, line in enumerate(path.read_text().splitlines(), 1):
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("label") != expected_label or not isinstance(row.get("instruction"), str):
            raise ValueError(f"{path}:{line_number}: invalid {expected_label} validation row")
        if not isinstance(row.get("id"), str) or row["id"] in ids:
            raise ValueError(f"{path}:{line_number}: missing or duplicate id")
        ids.add(row["id"])
        rows.append(row)
    if len(rows) != 32:
        raise ValueError(f"{path}: expected 32 validation rows, found {len(rows)}")
    return "".join(line_escape(row["instruction"]) + "\n" for row in rows)


def write_or_verify(path, content):
    if path.exists():
        if path.read_text() != content:
            raise ValueError(f"existing derived validation prompts differ: {path}")
        return "verified"
    path.write_text(content)
    os.chmod(path, 0o600)
    return "wrote"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("harmful_jsonl", type=Path)
    parser.add_argument("harmless_jsonl", type=Path)
    parser.add_argument("harmful_output", type=Path)
    parser.add_argument("harmless_output", type=Path)
    args = parser.parse_args()
    os.umask(0o077)
    if args.harmful_output == args.harmless_output:
        parser.error("validation outputs must differ")
    args.harmful_output.parent.mkdir(parents=True, exist_ok=True)
    args.harmless_output.parent.mkdir(parents=True, exist_ok=True)
    harmful = render(args.harmful_jsonl, "harmful")
    harmless = render(args.harmless_jsonl, "harmless")
    left = write_or_verify(args.harmful_output, harmful)
    right = write_or_verify(args.harmless_output, harmless)
    print(f"{left} {args.harmful_output}; {right} {args.harmless_output}")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise SystemExit(f"FAIL: {exc}") from exc
