#!/usr/bin/env python3
"""Verify the immutable K3 V9 startup state before calibration requests."""

import argparse
import hashlib
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


ARTIFACTS = {
    "alpha0": {
        "alias": "kimi-k3-q5attn-abl-v9-alpha0-cal",
        "alpha": 0.0,
        "bytes": 230272,
        "path": Path(
            "/models/.abliteration/k3/v9-affine-35db6bb3-d5c0a018/affine-alpha0.gguf"
        ),
        "sha256": "9f8c1184a91c0492d10d95af5fea22624171b5c4b23641bd32ee2667dc6cf611",
    },
    "alpha-m0p5": {
        "alias": "kimi-k3-q5attn-abl-v9-alpha-m0p5-cal",
        "alpha": -0.5,
        "bytes": 230272,
        "path": Path(
            "/models/.abliteration/k3/v9-affine-35db6bb3-d5c0a018/affine-alpha-m0p5.gguf"
        ),
        "sha256": "581e359359d0c1b7b642b015a7bd4355078314d0e890d8879522b64df262bfe8",
    },
}

HOT_ERROR = "Hot control-vector mutation is disabled while an immutable startup edit is active"
REQUEST_PREFIX = [
    {"status": 200, "method": "GET", "path": "/health"},
    {"status": 200, "method": "GET", "path": "/v1/models"},
    {"status": 200, "method": "GET", "path": "/control-vectors"},
    {"status": 409, "method": "POST", "path": "/control-vectors/load"},
    {"status": 409, "method": "POST", "path": "/control-vectors/unload"},
    {"status": 409, "method": "POST", "path": "/control-vectors/apply"},
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


def expected_state(artifact):
    return [{
        "alpha": artifact["alpha"],
        "applied": True,
        "id": -1,
        "layer": 61,
        "path": str(artifact["path"]),
        "rank": 7,
        "read_only": True,
        "type": "affine_subspace",
    }]


def validate_state(status, body, artifact):
    expected = expected_state(artifact)
    if status != 200 or body != expected:
        raise ValueError(
            f"immutable affine state differs: status={status}, "
            f"observed={body!r}, expected={expected!r}"
        )


def validate_hot_response(endpoint, status, body):
    expected = {"success": False, "error": HOT_ERROR}
    if status != 409 or body != expected:
        raise ValueError(
            f"hot {endpoint} did not fail closed: status={status}, "
            f"observed={body!r}, expected={expected!r}"
        )


def request_json(method, url, api_key, body=None, timeout=30):
    data = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
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
    parser.add_argument("coefficient", choices=tuple(ARTIFACTS))
    parser.add_argument("--base-url", default="http://127.0.0.1:8081")
    parser.add_argument("--api-key-file", type=Path, default=Path("/home/chuck/.glm-api-key"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)

    try:
        base_url = validate_base_url(args.base_url)
        artifact = ARTIFACTS[args.coefficient]
        if not artifact["path"].is_file():
            raise ValueError(f"missing affine artifact: {artifact['path']}")
        stat = artifact["path"].stat()
        if stat.st_size != artifact["bytes"] or sha256(artifact["path"]) != artifact["sha256"]:
            raise ValueError(f"affine artifact identity changed: {artifact['path']}")
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
        validate_models(status, models, artifact["alias"])
        observed.append({"status": status, "method": "GET", "path": "/v1/models"})

        status, state = request_json("GET", f"{base_url}/control-vectors", api_key)
        validate_state(status, state, artifact)
        observed.append({"status": status, "method": "GET", "path": "/control-vectors"})

        hot_results = {}
        for endpoint in ("load", "unload", "apply"):
            status, response = request_json(
                "POST", f"{base_url}/control-vectors/{endpoint}", api_key, {}
            )
            validate_hot_response(endpoint, status, response)
            hot_results[endpoint] = {"status": status, "response": response}
            observed.append({
                "status": status,
                "method": "POST",
                "path": f"/control-vectors/{endpoint}",
            })

        if observed != REQUEST_PREFIX:
            raise ValueError(f"request prefix differs: {observed!r}")
        if sha256(artifact["path"]) != artifact["sha256"]:
            raise ValueError("affine artifact changed during state verification")

        receipt = {
            "schema": 1,
            "captured_utc": datetime.now(timezone.utc).isoformat(),
            "base_url": base_url,
            "coefficient": args.coefficient,
            "model": artifact["alias"],
            "artifact": {
                "path": str(artifact["path"]),
                "bytes": artifact["bytes"],
                "sha256": artifact["sha256"],
            },
            "state": state,
            "hot_mutation": hot_results,
            "request_prefix": observed,
        }
        write_exclusive(args.output, receipt)
        print(
            f"PASS immutable V9 state coefficient={args.coefficient} "
            f"model={artifact['alias']} artifact={artifact['sha256']}"
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
