#!/usr/bin/env python3
"""Apply one preregistered K3 v7 control-vector setting and seal a receipt."""

import argparse
import hashlib
import json
import math
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


VECTOR_PATH = Path("/models/.abliteration/k3/run/k3-refusal-direction.gguf")
VECTOR_SHA256 = "7ce9aee3339ee267fa3de8017bba933168467a3d5a59f6d4c7da080b0b0588ad"
LAYER_START = 56
LAYER_END = 73
CALIBRATION_SCALES = (-0.125, -0.25, -0.5, -0.8, -1.0)
ALLOWED_SCALES = CALIBRATION_SCALES + (0.0,)


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def request_json(base_url, api_key, method, path, payload=None, timeout=30):
    body = None if payload is None else json.dumps(payload, separators=(",", ":")).encode()
    request = urllib.request.Request(
        f"{base_url}{path}",
        data=body,
        method=method,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.load(response)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"{method} {path} failed: {exc}") from exc


def verify_loopback(base_url):
    parsed = urllib.parse.urlparse(base_url)
    if parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "localhost", "::1"):
        raise ValueError("v7 control changes are restricted to a loopback HTTP server")
    if parsed.path not in ("", "/") or parsed.params or parsed.query or parsed.fragment:
        raise ValueError("base URL must not contain a path, query, or fragment")
    return base_url.rstrip("/")


def verify_model(payload, expected):
    ids = [row.get("id") for row in payload.get("data", []) if isinstance(row, dict)]
    if ids != [expected]:
        raise ValueError(f"server reports models {ids!r}, expected exactly [{expected!r}]")


def scale_allowed(value):
    return any(math.isclose(value, allowed, rel_tol=0.0, abs_tol=1e-9)
               for allowed in ALLOWED_SCALES)


def verify_single_vector(rows, scale):
    if not isinstance(rows, list) or len(rows) != 1:
        raise ValueError(f"expected exactly one loaded vector, got {rows!r}")
    row = rows[0]
    expected = {
        "id": 0,
        "path": str(VECTOR_PATH),
        "layer_start": LAYER_START,
        "layer_end": LAYER_END,
        "applied": scale != 0.0,
    }
    mismatches = [key for key, value in expected.items() if row.get(key) != value]
    if not isinstance(row.get("scale"), (int, float)) or not math.isclose(
            float(row["scale"]), scale, rel_tol=0.0, abs_tol=1e-7):
        mismatches.append("scale")
    if mismatches:
        raise ValueError(f"control-vector state mismatch in {sorted(set(mismatches))}: {row!r}")


def write_receipt(path, receipt):
    payload = (json.dumps(receipt, indent=2, sort_keys=True) + "\n").encode()
    with path.open("xb") as output:
        os.chmod(path, 0o600)
        output.write(payload)
        output.flush()
        os.fsync(output.fileno())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8081")
    parser.add_argument("--api-key", default=os.environ.get("K3_API_KEY"))
    parser.add_argument("--api-key-file", type=Path, default=Path("~/.glm-api-key"))
    parser.add_argument("--model", required=True)
    parser.add_argument("--scale", type=float, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--require-empty-before", action="store_true")
    args = parser.parse_args()

    os.umask(0o077)
    base_url = verify_loopback(args.base_url)
    if not math.isfinite(args.scale) or not scale_allowed(args.scale):
        parser.error(f"scale must be one of {ALLOWED_SCALES}")
    if not VECTOR_PATH.is_file() or sha256(VECTOR_PATH) != VECTOR_SHA256:
        raise SystemExit("v7 control-vector path or hash mismatch")
    args.api_key_file = args.api_key_file.expanduser()
    api_key = args.api_key
    if not api_key:
        if not args.api_key_file.is_file():
            parser.error(f"API key file is unreadable: {args.api_key_file}")
        api_key = args.api_key_file.read_text().strip()
    if not api_key:
        parser.error("empty API key")
    if args.output.exists():
        raise SystemExit(f"refusing to overwrite receipt: {args.output}")
    args.output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)

    started = datetime.now(timezone.utc).isoformat()
    model_before = request_json(base_url, api_key, "GET", "/v1/models")
    verify_model(model_before, args.model)
    before = request_json(base_url, api_key, "GET", "/control-vectors")
    if args.require_empty_before and before:
        raise SystemExit(f"control-vector list was not empty: {before!r}")

    body = {
        "path": str(VECTOR_PATH),
        "scale": args.scale,
        "layer_start": LAYER_START,
        "layer_end": LAYER_END,
    }
    if not before:
        transition = request_json(base_url, api_key, "POST", "/control-vectors/load", body)
        if transition != {"success": True, "id": 0}:
            raise ValueError(f"unexpected vector-load response: {transition!r}")
        action = "load"
    else:
        verify_single_vector(before, float(before[0].get("scale", float("nan"))))
        transition = request_json(
            base_url,
            api_key,
            "POST",
            "/control-vectors/apply",
            [{
                "id": 0,
                "scale": args.scale,
                "layer_start": LAYER_START,
                "layer_end": LAYER_END,
            }],
        )
        if transition != {"success": True}:
            raise ValueError(f"unexpected vector-apply response: {transition!r}")
        action = "apply"

    after = request_json(base_url, api_key, "GET", "/control-vectors")
    verify_single_vector(after, args.scale)
    model_after = request_json(base_url, api_key, "GET", "/v1/models")
    verify_model(model_after, args.model)
    if sha256(VECTOR_PATH) != VECTOR_SHA256:
        raise SystemExit("control-vector changed during transition")

    receipt = {
        "schema_version": 1,
        "action": action,
        "base_url": base_url,
        "model": args.model,
        "scale": args.scale,
        "layer_start": LAYER_START,
        "layer_end": LAYER_END,
        "vector_path": str(VECTOR_PATH),
        "vector_sha256": VECTOR_SHA256,
        "state_before": before,
        "transition_response": transition,
        "state_after": after,
        "started_utc": started,
        "completed_utc": datetime.now(timezone.utc).isoformat(),
    }
    write_receipt(args.output, receipt)
    print(json.dumps(receipt, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
