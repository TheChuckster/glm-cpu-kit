#!/usr/bin/env python3
"""Create a write-once, hash-bound review or template for harmful API responses."""

import argparse
import hashlib
import json
import os
import re
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("evaluation", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    source = parser.add_mutually_exclusive_group()
    source.add_argument(
        "--decisions-tsv",
        type=Path,
        help=("optional completed review with tab-separated id, classification, and notes; "
              "must cover every harmful response exactly once"),
    )
    source.add_argument(
        "--reuse-review",
        type=Path,
        help=("optional prior hash-bound review; reuse decisions only where the id and "
              "response SHA-256 are unchanged, and mark changed responses REVIEW_REQUIRED"),
    )
    args = parser.parse_args()

    decisions = {}
    if args.decisions_tsv:
        for line_number, line in enumerate(args.decisions_tsv.read_text().splitlines(), 1):
            if not line.strip():
                continue
            fields = line.split("\t", 2)
            if len(fields) != 3:
                parser.error(
                    f"{args.decisions_tsv}:{line_number}: expected three tab-separated fields"
                )
            item_id, classification, notes = fields
            if not item_id or item_id in decisions:
                parser.error(
                    f"{args.decisions_tsv}:{line_number}: missing or duplicate id {item_id!r}"
                )
            if classification not in {"refusal", "mixed", "compliance"}:
                parser.error(
                    f"{args.decisions_tsv}:{line_number}: invalid classification "
                    f"{classification!r}"
                )
            if not notes.strip():
                parser.error(f"{args.decisions_tsv}:{line_number}: notes are empty")
            decisions[item_id] = (classification, notes.strip())

    prior_reviews = {}
    if args.reuse_review:
        for line_number, line in enumerate(args.reuse_review.read_text().splitlines(), 1):
            if not line.strip():
                continue
            review = json.loads(line)
            item_id = review.get("id")
            if not isinstance(item_id, str) or not item_id or item_id in prior_reviews:
                parser.error(
                    f"{args.reuse_review}:{line_number}: missing or duplicate id {item_id!r}"
                )
            response_hash = review.get("response_sha256")
            if not isinstance(response_hash, str) or not re.fullmatch(
                    r"[0-9a-fA-F]{64}", response_hash):
                parser.error(
                    f"{args.reuse_review}:{line_number}: invalid response SHA-256 for {item_id}"
                )
            classification = review.get("classification")
            if classification not in {"refusal", "mixed", "compliance"}:
                parser.error(
                    f"{args.reuse_review}:{line_number}: invalid classification for {item_id}"
                )
            notes = review.get("notes")
            if not isinstance(notes, str) or not notes.strip():
                parser.error(f"{args.reuse_review}:{line_number}: notes are empty for {item_id}")
            prior_reviews[item_id] = (
                response_hash.lower(), classification, notes.strip()
            )

    rows = []
    seen = set()
    reused = 0
    for line_number, line in enumerate(args.evaluation.read_text().splitlines(), 1):
        if not line.strip():
            continue
        row = json.loads(line)
        item_id = row.get("id")
        if not isinstance(item_id, str) or not item_id:
            parser.error(f"{args.evaluation}:{line_number}: missing string id")
        if item_id in seen:
            parser.error(f"{args.evaluation}:{line_number}: duplicate id {item_id!r}")
        seen.add(item_id)
        if row.get("label") != "harmful":
            continue
        response = row.get("response")
        if not isinstance(response, str):
            parser.error(f"{args.evaluation}:{line_number}: {item_id} has no string response")
        response_hash = hashlib.sha256(response.encode("utf-8")).hexdigest()
        classification, notes = decisions.get(item_id, ("REVIEW_REQUIRED", ""))
        prior = prior_reviews.get(item_id)
        if prior and prior[0] == response_hash:
            classification, notes = prior[1], prior[2]
            reused += 1
        rows.append({
            "id": item_id,
            "response_sha256": response_hash,
            "classification": classification,
            "notes": notes,
        })

    if not rows:
        parser.error("evaluation contains no harmful responses")
    harmful_ids = {row["id"] for row in rows}
    if args.decisions_tsv:
        missing = sorted(harmful_ids - set(decisions))
        extra = sorted(set(decisions) - harmful_ids)
        if missing or extra:
            parser.error(
                f"decision ids differ from harmful evaluation ids: "
                f"missing={missing[:5]}, extra={extra[:5]}"
            )
    if args.reuse_review:
        missing = sorted(harmful_ids - set(prior_reviews))
        extra = sorted(set(prior_reviews) - harmful_ids)
        if missing or extra:
            parser.error(
                f"prior review ids differ from harmful evaluation ids: "
                f"missing={missing[:5]}, extra={extra[:5]}"
            )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        parser.error(f"refusing to overwrite existing review: {args.output}")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            for row in rows:
                output.write(json.dumps(row, sort_keys=True) + "\n")
    except BaseException:
        args.output.unlink(missing_ok=True)
        raise
    print(f"wrote {len(rows)} hash-bound review rows to {args.output}")
    if args.decisions_tsv:
        print(f"materialized completed decisions from {args.decisions_tsv}")
    elif args.reuse_review:
        print(
            f"reused {reused} unchanged decisions from {args.reuse_review}; "
            f"{len(rows) - reused} changed responses require review"
        )
    else:
        print("replace REVIEW_REQUIRED with refusal, mixed, or compliance and add notes")


if __name__ == "__main__":
    main()
