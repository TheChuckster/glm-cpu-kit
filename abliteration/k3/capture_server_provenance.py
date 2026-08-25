#!/usr/bin/env python3
"""Bind a completed API evaluation to its live systemd service runtime."""

import argparse
import hashlib
import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def artifact_record(path):
    resolved = path.resolve(strict=True)
    if not resolved.is_file():
        raise ValueError(f"protocol artifact is not a regular file: {resolved}")
    return {
        "path": str(resolved),
        "bytes": resolved.stat().st_size,
        "sha256": sha256(resolved),
    }


def process_started_utc(pid):
    stat = Path(f"/proc/{pid}/stat").read_text()
    close = stat.rfind(")")
    if close < 0:
        raise ValueError(f"could not parse /proc/{pid}/stat")
    fields_after_comm = stat[close + 2:].split()
    # /proc/PID/stat field 22 is starttime; fields_after_comm begins at field 3.
    start_ticks = int(fields_after_comm[19])
    boot_seconds = None
    for line in Path("/proc/stat").read_text().splitlines():
        if line.startswith("btime "):
            boot_seconds = int(line.split()[1])
            break
    if boot_seconds is None:
        raise ValueError("/proc/stat has no btime")
    epoch = boot_seconds + start_ticks / os.sysconf("SC_CLK_TCK")
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat()


def parse_utc(value, field):
    if not isinstance(value, str):
        raise ValueError(f"evaluation summary has no {field}")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError(f"evaluation summary {field} has no timezone")
    return parsed


def request_audit(unit, total):
    result = subprocess.run(
        ["journalctl", "-u", unit, "--no-pager", "--output=cat"],
        check=True,
        capture_output=True,
        text=True,
    )
    pattern = re.compile(r'status=(\d+) method="([A-Z]+)" path="([^"]+)"')
    sequence = []
    for line in result.stdout.splitlines():
        match = pattern.search(line)
        if match:
            sequence.append({
                "status": int(match.group(1)),
                "method": match.group(2),
                "path": match.group(3),
            })
    expected = (
        [{"status": 200, "method": "GET", "path": "/v1/models"}]
        + [{"status": 200, "method": "POST", "path": "/v1/chat/completions"}] * total
        + [{"status": 200, "method": "GET", "path": "/v1/models"}]
    )
    if sequence != expected:
        raise ValueError(
            f"server request history is not a fresh evaluator-only run: "
            f"observed {len(sequence)} requests, expected {len(expected)}"
        )
    encoded = json.dumps(sequence, separators=(",", ":"), sort_keys=True).encode()
    return {
        "policy": "exactly GET /v1/models, N successful chat requests, GET /v1/models",
        "total_requests": len(sequence),
        "models_requests": 2,
        "chat_completion_requests": total,
        "normalized_sequence_sha256": hashlib.sha256(encoded).hexdigest(),
    }


def argv_option(argv, option):
    values = []
    index = 0
    while index < len(argv):
        value = argv[index]
        if value == option:
            if index + 1 >= len(argv):
                raise ValueError(f"{option} has no value in server argv")
            values.append(argv[index + 1])
            index += 2
            continue
        if value.startswith(option + "="):
            values.append(value.split("=", 1)[1])
        index += 1
    if len(values) != 1:
        raise ValueError(f"server argv must contain exactly one {option}: {values!r}")
    return values[0]


def evaluation_summary(evaluation, server_alias, server_host, server_port, server_started):
    summary_path = evaluation.with_suffix(evaluation.suffix + ".summary.json")
    if not summary_path.is_file():
        raise ValueError(f"completed evaluation summary is missing: {summary_path}")
    summary = json.loads(summary_path.read_text())
    rows = [
        json.loads(line)
        for line in evaluation.read_text().splitlines()
        if line.strip()
    ]
    models = {row.get("model") for row in rows}
    if models != {server_alias}:
        raise ValueError(
            f"evaluation models {models!r} do not match server alias {server_alias!r}"
        )
    if summary.get("model") != server_alias or summary.get("served_model") != server_alias:
        raise ValueError("evaluation summary model does not match the live server alias")
    if summary.get("total") != len(rows):
        raise ValueError("evaluation summary total does not match the result JSONL")
    expected_counts = {
        label: sum(row.get("label") == label for row in rows)
        for label in ("harmful", "harmless")
    }
    for label, count in expected_counts.items():
        label_summary = (summary.get("by_label") or {}).get(label)
        observed_count = 0 if label_summary is None else label_summary.get("count")
        if observed_count != count:
            raise ValueError(f"evaluation summary {label} count does not match the JSONL")
    result_file = summary.get("result_file")
    if not isinstance(result_file, str) or Path(result_file).resolve() != evaluation.resolve():
        raise ValueError("evaluation summary result path does not match the result JSONL")
    expected_base_url = f"http://{server_host}:{server_port}/v1"
    if summary.get("base_url") != expected_base_url:
        raise ValueError(
            f"evaluation base URL {summary.get('base_url')!r} does not match "
            f"the live server {expected_base_url!r}"
        )
    if not isinstance(summary.get("seed"), int) or not isinstance(
            summary.get("max_tokens"), int):
        raise ValueError("evaluation summary seed or max_tokens is not an integer")
    run_started = parse_utc(summary.get("run_started_utc"), "run_started_utc")
    run_completed = parse_utc(summary.get("run_completed_utc"), "run_completed_utc")
    server_started_parsed = parse_utc(server_started, "server_started_utc")
    if not server_started_parsed < run_started <= run_completed:
        raise ValueError("server/evaluator timestamps are not strictly ordered")
    for index, row in enumerate(rows):
        if row.get("seed") != summary["seed"] + index:
            raise ValueError(
                f"evaluation row {row.get('id')!r} seed does not match the summary sequence"
            )
        if row.get("max_tokens") != summary["max_tokens"]:
            raise ValueError(
                f"evaluation row {row.get('id')!r} token limit does not match the summary"
            )
    configuration = {
        "base_url": summary["base_url"],
        "max_tokens": summary["max_tokens"],
        "model": summary["model"],
        "result_file": summary["result_file"],
        "seed": summary["seed"],
        "served_model": summary["served_model"],
        "total": summary["total"],
    }
    normalized = {
        **configuration,
        "model": "<MODEL>",
        "result_file": "<EVALUATION>",
        "served_model": "<MODEL>",
    }
    return {
        **artifact_record(summary_path),
        "configuration": configuration,
        "normalized_configuration": normalized,
        "timing": {
            "server_started_utc": server_started,
            "evaluation_started_utc": summary["run_started_utc"],
            "evaluation_completed_utc": summary["run_completed_utc"],
        },
    }


def unit_properties(unit):
    result = subprocess.run(
        [
            "systemctl", "show", unit,
            "--property=ActiveState", "--property=MainPID", "--value",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    values = result.stdout.splitlines()
    if len(values) != 2:
        raise ValueError(f"unexpected systemctl output for {unit!r}: {values!r}")
    # systemctl emits values in property-name order rather than request order.
    active = next((value for value in values if value in {
        "active", "activating", "deactivating", "inactive", "failed"
    }), None)
    pid_text = next((value for value in values if value.isdigit()), None)
    if active != "active" or not pid_text or int(pid_text) <= 0:
        raise ValueError(f"unit {unit!r} is not an active service: {values!r}")
    return int(pid_text)


def normalize_argv(argv):
    normalized = []
    replaced = {"--model": 0, "--alias": 0}
    index = 0
    while index < len(argv):
        value = argv[index]
        matched = False
        for option, placeholder in (("--model", "<MODEL>"), ("--alias", "<ALIAS>")):
            if value == option:
                if index + 1 >= len(argv):
                    raise ValueError(f"{option} has no value in server argv")
                normalized.extend((option, placeholder))
                replaced[option] += 1
                index += 2
                matched = True
                break
            if value.startswith(option + "="):
                normalized.append(option + "=" + placeholder)
                replaced[option] += 1
                index += 1
                matched = True
                break
        if not matched:
            normalized.append(value)
            index += 1
    if replaced != {"--model": 1, "--alias": 1}:
        raise ValueError(f"server argv must contain exactly one model and alias: {replaced}")
    return normalized


def runtime_closure(pid, executable):
    paths = {executable}
    maps = Path(f"/proc/{pid}/maps").read_text().splitlines()
    for line in maps:
        fields = line.split(maxsplit=5)
        if len(fields) < 6 or "x" not in fields[1]:
            continue
        mapped = fields[5]
        if not mapped.startswith("/"):
            continue
        if mapped.endswith(" (deleted)"):
            raise ValueError(f"runtime mapping was replaced after launch: {mapped}")
        path = Path(mapped).resolve()
        if path.is_file():
            paths.add(path)
    records = [
        {"path": str(path), "sha256": sha256(path)}
        for path in sorted(paths, key=str)
    ]
    fingerprint_input = "".join(
        f"{record['sha256']}  {record['path']}\n" for record in records
    ).encode()
    return records, hashlib.sha256(fingerprint_input).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("evaluation", type=Path)
    parser.add_argument("--unit", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--protocol-artifact",
        action="append",
        type=Path,
        required=True,
        help="evaluator or prompt artifact to hash; repeat for every protocol input",
    )
    args = parser.parse_args()
    if not args.evaluation.is_file():
        parser.error(f"evaluation is not a file: {args.evaluation}")

    try:
        pid = unit_properties(args.unit)
        proc = Path(f"/proc/{pid}")
        executable = (proc / "exe").resolve(strict=True)
        working_directory = (proc / "cwd").resolve(strict=True)
        argv = [
            item.decode("utf-8", errors="surrogateescape")
            for item in (proc / "cmdline").read_bytes().split(b"\0")
            if item
        ]
        if not argv:
            raise ValueError(f"service PID {pid} has an empty command line")
        if Path(argv[0]).resolve(strict=True) != executable:
            raise ValueError(
                f"argv executable {argv[0]!r} does not resolve to /proc/{pid}/exe {executable}"
            )
        normalized = normalize_argv(argv)
        server_alias = argv_option(argv, "--alias")
        server_host = argv_option(argv, "--host")
        server_port = argv_option(argv, "--port")
        libraries, closure_hash = runtime_closure(pid, executable)
        server_started = process_started_utc(pid)
        protocol_paths = sorted(
            {path.resolve(strict=True) for path in args.protocol_artifact}, key=str
        )
        protocol_artifacts = [artifact_record(path) for path in protocol_paths]
        summary = evaluation_summary(
            args.evaluation, server_alias, server_host, server_port, server_started
        )
        requests = request_audit(args.unit, summary["configuration"]["total"])
    except (OSError, subprocess.CalledProcessError, ValueError) as exc:
        parser.error(str(exc))

    result = {
        "schema": 2,
        "captured_utc": datetime.now(timezone.utc).isoformat(),
        "unit": args.unit,
        "pid": pid,
        "server_started_utc": server_started,
        "evaluation": {
            "path": str(args.evaluation),
            "bytes": args.evaluation.stat().st_size,
            "sha256": sha256(args.evaluation),
        },
        "evaluation_summary": summary,
        "request_audit": requests,
        "executable": {
            "path": str(executable),
            "sha256": sha256(executable),
        },
        "working_directory": str(working_directory),
        "argv": argv,
        "normalized_argv": normalized,
        "normalization": "replace exactly one --model and --alias value",
        "evaluation_protocol_artifacts": protocol_artifacts,
        "runtime_executable_closure": libraries,
        "runtime_executable_closure_sha256": closure_hash,
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        parser.error(f"refusing to overwrite existing provenance: {args.output}")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            json.dump(result, output, indent=2, sort_keys=True)
            output.write("\n")
    except BaseException:
        args.output.unlink(missing_ok=True)
        raise
    print(
        f"bound {args.evaluation} to PID {pid}, {executable}, "
        f"closure {closure_hash}"
    )


if __name__ == "__main__":
    main()
