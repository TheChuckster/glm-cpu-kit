#!/usr/bin/env python3
"""Run a frozen K3 final-assistant-prefill evaluation through the live API."""

import argparse
import json
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from evaluate_api import classify, load_datasets, load_system_prompt, summarize, verify_model_identity


MAX_TOKENS = 2048
REQUEST_ATTEMPTS = 1
SEED = 20260823
CHAT_TEMPLATE_KWARGS = {
    "enable_thinking": False,
    "thinking_effort": "low",
}
MESSAGE_ROLES = ["system", "user", "assistant"]
REQUEST_CONTRACT = {
    "assistant_prefill_mode": "final_assistant_message",
    "chat_template_kwargs": CHAT_TEMPLATE_KWARGS,
    "message_roles": MESSAGE_ROLES,
    "response_assembly": "assistant_prefill + response_continuation",
    "stream": False,
    "temperature": 0,
}


def arguments():
    parser = argparse.ArgumentParser()
    parser.add_argument("datasets", nargs="+", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--base-url",
        default=os.environ.get("K3_BASE_URL", "http://127.0.0.1:8080/v1"),
    )
    parser.add_argument("--api-key", default=os.environ.get("K3_API_KEY"))
    parser.add_argument("--api-key-file", type=Path, default=Path("~/.glm-api-key"))
    parser.add_argument("--model", default=os.environ.get("K3_MODEL", "kimi-k3"))
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument("--system-prompt-file", type=Path, required=True)
    parser.add_argument("--assistant-prefill-file", type=Path, required=True)
    args = parser.parse_args()
    args.base_url = args.base_url.rstrip("/")
    args.api_key_file = args.api_key_file.expanduser()
    if not args.api_key:
        if not args.api_key_file.is_file():
            parser.error(f"API key not supplied and file is unreadable: {args.api_key_file}")
        args.api_key = args.api_key_file.read_text().strip()
    if args.timeout < 1:
        parser.error("timeout must be positive")
    try:
        args.system_prompt_file = args.system_prompt_file.resolve(strict=True)
        args.system_prompt, args.system_prompt_sha256 = load_system_prompt(
            args.system_prompt_file
        )
        args.assistant_prefill_file = args.assistant_prefill_file.resolve(strict=True)
        args.assistant_prefill, args.assistant_prefill_sha256 = load_system_prompt(
            args.assistant_prefill_file
        )
    except (OSError, UnicodeError, ValueError) as exc:
        parser.error(str(exc))
    return args


def build_payload(args, row, index):
    return {
        "model": args.model,
        "seed": SEED + index,
        "temperature": 0,
        "max_tokens": MAX_TOKENS,
        "stream": False,
        "messages": [
            {"role": "system", "content": args.system_prompt},
            {"role": "user", "content": row["instruction"]},
            {"role": "assistant", "content": args.assistant_prefill},
        ],
        "chat_template_kwargs": dict(CHAT_TEMPLATE_KWARGS),
    }


def request(args, row, index):
    payload = build_payload(args, row, index)
    request_ = urllib.request.Request(
        f"{args.base_url}/chat/completions",
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": f"Bearer {args.api_key}",
            "Content-Type": "application/json",
        },
    )
    try:
        started = time.monotonic()
        with urllib.request.urlopen(request_, timeout=args.timeout) as response:
            result = json.load(response)
        result["client_elapsed_seconds"] = time.monotonic() - started
        return result
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"request failed after one attempt: {exc}") from exc


def classify_prefill(row, raw_response, args):
    choice = (raw_response.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    continuation = message.get("content") or ""
    reconstructed = args.assistant_prefill + continuation
    response_for_classification = {
        **raw_response,
        "choices": [{
            **choice,
            "message": {**message, "content": reconstructed},
        }],
    }
    classified = classify(row, response_for_classification, MAX_TOKENS)
    if not continuation:
        classified["termination_errors"].append("empty response continuation")
    classified.update({
        "response": reconstructed,
        "response_continuation": continuation,
        "assistant_prefill_sha256": args.assistant_prefill_sha256,
        "request_message_roles": list(MESSAGE_ROLES),
        "request_chat_template_kwargs": dict(CHAT_TEMPLATE_KWARGS),
    })
    return classified


def result_identity(args, index):
    return {
        "model": args.model,
        "seed": SEED + index,
        "max_tokens": MAX_TOKENS,
        "request_attempts": REQUEST_ATTEMPTS,
        "system_prompt_sha256": args.system_prompt_sha256,
        "assistant_prefill_sha256": args.assistant_prefill_sha256,
        "request_message_roles": list(MESSAGE_ROLES),
        "request_chat_template_kwargs": dict(CHAT_TEMPLATE_KWARGS),
    }


def main():
    os.umask(0o077)
    args = arguments()
    run_started_utc = datetime.now(timezone.utc).isoformat()
    served_model = verify_model_identity(args)
    dataset = load_datasets(args.datasets, 0)
    if args.output.exists():
        raise SystemExit(f"refusing to overwrite existing results: {args.output}")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    existing = []
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            print(f"evaluating {len(dataset)} prompts", flush=True)
            for index, row in enumerate(dataset):
                identity = result_identity(args, index)
                try:
                    response = request(args, row, index)
                    classified = classify_prefill(row, response, args)
                    classified.update(identity)
                    classified["client_elapsed_seconds"] = response.pop(
                        "client_elapsed_seconds"
                    )
                except Exception as exc:
                    classified = {
                        **row,
                        **identity,
                        "request_error": str(exc),
                        "response": "",
                        "response_continuation": "",
                        "reasoning_content": "",
                        "finish_reason": None,
                        "completion_tokens": None,
                        "refused_substring": False,
                        "refused_expanded": False,
                        "termination_errors": [f"request error: {exc}"],
                        "incoherent": False,
                        "incoherence_detail": None,
                    }
                output.write(
                    json.dumps(classified, ensure_ascii=False, sort_keys=True) + "\n"
                )
                output.flush()
                os.fsync(output.fileno())
                existing.append(classified)
                print(
                    f"{len(existing):3d}/{len(dataset):3d} {row['id']}: "
                    f"refused={classified['refused_substring']} "
                    f"finish={classified['finish_reason']} "
                    f"tokens={classified['completion_tokens']}",
                    flush=True,
                )
    except BaseException:
        if not existing:
            args.output.unlink(missing_ok=True)
        raise

    served_model_after = verify_model_identity(args)
    if served_model_after != served_model:
        raise SystemExit(
            f"served model changed during evaluation: {served_model!r} -> "
            f"{served_model_after!r}"
        )
    summary = summarize(existing)
    summary.update({
        "model": args.model,
        "served_model": served_model,
        "base_url": args.base_url,
        "max_tokens": MAX_TOKENS,
        "request_attempts": REQUEST_ATTEMPTS,
        "seed": SEED,
        "result_file": str(args.output),
        "run_started_utc": run_started_utc,
        "run_completed_utc": datetime.now(timezone.utc).isoformat(),
        "system_prompt_file": str(args.system_prompt_file),
        "system_prompt_sha256": args.system_prompt_sha256,
        "assistant_prefill_file": str(args.assistant_prefill_file),
        "assistant_prefill_sha256": args.assistant_prefill_sha256,
        "request_contract": REQUEST_CONTRACT,
    })
    summary_path = args.output.with_suffix(args.output.suffix + ".summary.json")
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    os.chmod(summary_path, 0o600)
    print(json.dumps(summary, indent=2, sort_keys=True))
    failures = sum(
        bool(row["termination_errors"]) or row["incoherent"] for row in existing
    )
    raise SystemExit(1 if failures else 0)


if __name__ == "__main__":
    main()
