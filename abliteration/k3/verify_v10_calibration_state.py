#!/usr/bin/env python3
"""Verify one exact V10 prompt candidate and its isolated V2 server."""

import argparse
import hashlib
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


PROMPTS = {
    "prompt01": {
        "alias": "kimi-k3-q5attn-abl-v10-p01-cal",
        "filename": "v10-system-prompt-01-dolphin.txt",
        "sha256": "c6eb732f6dde39117b88c7be335b9f48d10b886440653a681f2ef0b266cbcb05",
    },
    "prompt02": {
        "alias": "kimi-k3-q5attn-abl-v10-p02-cal",
        "filename": "v10-system-prompt-02-semantic-contract.txt",
        "sha256": "44fc73623eb35a4b19b9cbfdf682a015af832ed8954233c68b8cf5845ab116f9",
    },
    "prompt03": {
        "alias": "kimi-k3-q5attn-abl-v10-p03-cal",
        "filename": "v10-system-prompt-03-semantic-contract-reinforced.txt",
        "sha256": "408dae29014a0bab5f0de22a0d78442e6cc77505c5302cd841b2c73c6b051463",
    },
}
REQUEST_PREFIX = [
    {"status": 200, "method": "GET", "path": "/health"},
    {"status": 200, "method": "GET", "path": "/v1/models"},
]


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_base_url(value):
    parsed = urllib.parse.urlsplit(value.rstrip("/"))
    if (
        parsed.scheme != "http"
        or parsed.hostname != "127.0.0.1"
        or parsed.port != 8081
        or parsed.path
        or parsed.query
        or parsed.fragment
        or parsed.username
        or parsed.password
    ):
        raise ValueError("base URL must be exactly loopback HTTP on port 8081")
    return value.rstrip("/")


def validate_health(status, body):
    if status != 200 or not isinstance(body, dict) or body.get("status") != "ok":
        raise ValueError(f"candidate health is not ready: status={status}, body={body!r}")


def validate_models(status, body, expected_alias):
    if status != 200 or not isinstance(body, dict):
        raise ValueError(f"invalid models response: status={status}, body={body!r}")
    ids = [row.get("id") for row in body.get("data", []) if isinstance(row, dict)]
    if ids != [expected_alias]:
        raise ValueError(f"server reports models {ids!r}, expected [{expected_alias!r}]")


def request_json(method, url, api_key, timeout=30):
    request = urllib.request.Request(
        url,
        method=method,
        headers={"Authorization": f"Bearer {api_key}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as exc:
        payload = exc.read()
        return exc.code, json.loads(payload) if payload else None


def write_exclusive(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise ValueError(f"refusing to overwrite state receipt: {path}") from exc
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            json.dump(payload, output, indent=2, sort_keys=True)
            output.write("\n")
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("prompt", choices=tuple(PROMPTS))
    parser.add_argument("--prompt-file", type=Path, required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:8081")
    parser.add_argument("--api-key-file", type=Path, default=Path("/home/chuck/.glm-api-key"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)

    try:
        base_url = validate_base_url(args.base_url)
        specification = PROMPTS[args.prompt]
        prompt_file = args.prompt_file.resolve(strict=True)
        if prompt_file.name != specification["filename"]:
            raise ValueError("prompt filename differs from the locked ladder")
        prompt_hash = sha256(prompt_file)
        if prompt_hash != specification["sha256"]:
            raise ValueError("prompt SHA-256 differs from the locked ladder")
        if not args.api_key_file.is_file():
            raise ValueError(f"API key file is unreadable: {args.api_key_file}")
        api_key = args.api_key_file.read_text().strip()
        if not api_key:
            raise ValueError("API key is empty")

        observed = []
        status, health = request_json("GET", f"{base_url}/health", api_key)
        validate_health(status, health)
        observed.append({"status": status, "method": "GET", "path": "/health"})

        status, models = request_json("GET", f"{base_url}/v1/models", api_key)
        validate_models(status, models, specification["alias"])
        observed.append({"status": status, "method": "GET", "path": "/v1/models"})
        if observed != REQUEST_PREFIX:
            raise ValueError(f"request prefix differs: {observed!r}")
        if sha256(prompt_file) != specification["sha256"]:
            raise ValueError("prompt changed during server verification")

        receipt = {
            "schema": "k3-v10-calibration-state-v1",
            "captured_utc": datetime.now(timezone.utc).isoformat(),
            "base_url": base_url,
            "prompt": args.prompt,
            "model": specification["alias"],
            "prompt_artifact": {
                "path": str(prompt_file),
                "bytes": prompt_file.stat().st_size,
                "sha256": prompt_hash,
            },
            "request_prefix": observed,
        }
        write_exclusive(args.output, receipt)
        print(
            f"PASS V10 state prompt={args.prompt} model={specification['alias']} "
            f"system_prompt={prompt_hash}"
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
