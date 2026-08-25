#!/usr/bin/env python3
"""Response-free control/candidate proof for K3 V13 Thought Token Forcing."""

import argparse
import hashlib
import json
import os
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from capture_server_provenance import process_started_utc, runtime_closure, unit_properties
from evaluate_api import load_system_prompt


SERVER_PATH = "/home/chuck/ik_llama-v13-98de9a7f/build-v13/bin/llama-server"
SERVER_WORKING_DIRECTORY = "/home/chuck/ik_llama-v13-98de9a7f"
MODEL_PATH = (
    "/models/Kimi-K3-Q5attn-Abliterated-V2/"
    "Kimi-K3-Q5attn-Abliterated-V2-00001-of-00019.gguf"
)
SERVER_SHA256 = "b0a26936cfe28302dbfd9d51ffaddaceba9771e3ac15878a1994f7bab57a44a6"
ENGINE_COMMIT = "98de9a7f69ef3d387b676ad4a3ee14946ac88f94"
CONTROL_ALIAS = "kimi-k3-q5attn-abl-v13-control-preflight"
CANDIDATE_ALIAS = "kimi-k3-q5attn-abl-v13-ttf-preflight"
CONTROL_UNIT = "kimi-k3-q5attn-abl-v13-control-preflight.service"
CANDIDATE_UNIT = "kimi-k3-q5attn-abl-v13-feature-preflight.service"
SYSTEM_PROMPT_SHA256 = "44fc73623eb35a4b19b9cbfdf682a015af832ed8954233c68b8cf5845ab116f9"
REASONING_PREFILL_SHA256 = (
    "61185bb05cc1732c653ff21bcbaf1f7b38a0b1823b13422c25877940a0184fb5"
)
REASONING_PREFILL = "I know that."
TEMPLATE_USER = "response-free thinking-template check"
THINKING_SUFFIX = '<|open|>message role="assistant"<|sep|><|open|>think<|sep|>'
BASELINE_PROMPT_BYTES = 1152
BASELINE_PROMPT_SHA256 = (
    "70667c925b21854812675b3c3acd5055218495adbe816e1e6636116081e2fe22"
)
BASELINE_SUFFIX_SHA256 = (
    "28c554ed113067c06e907127add0266f39a34d17ca195baba5786c916ee6e350"
)
EXTENDED_PROMPT_BYTES = 1164
EXTENDED_PROMPT_SHA256 = (
    "aa5dd4313ea8579d7f032e5a02bb39c4efd10c7757d9d02125c34a26c2f98ec2"
)
EXTENDED_SUFFIX_SHA256 = (
    "fec6854a667b667357ec502c5d7d51fdef1a3ab404365447b37e2de229a4f644"
)
CHAT_TEMPLATE_KWARGS = {"enable_thinking": True, "thinking_effort": "low"}


def sha256_bytes(value):
    return hashlib.sha256(value).hexdigest()


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def artifact_record(path):
    resolved = path.resolve(strict=True)
    if not resolved.is_file():
        raise ValueError(f"not a regular file: {resolved}")
    return {
        "path": str(resolved),
        "bytes": resolved.stat().st_size,
        "sha256": sha256(resolved),
    }


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


def request_json(method, url, api_key, payload=None, timeout=30):
    data = None if payload is None else json.dumps(payload).encode()
    request = urllib.request.Request(
        url,
        method=method,
        data=data,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            return response.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            body = json.loads(raw) if raw else None
        except json.JSONDecodeError:
            body = raw.decode("utf-8", errors="replace")
        return exc.code, body


def validate_health(status, body):
    if status != 200 or not isinstance(body, dict) or body.get("status") != "ok":
        raise ValueError(f"server health is not ready: status={status}, body={body!r}")


def validate_models(status, body, alias):
    if status != 200 or not isinstance(body, dict):
        raise ValueError(f"invalid models response: status={status}, body={body!r}")
    ids = [row.get("id") for row in body.get("data", []) if isinstance(row, dict)]
    if ids != [alias]:
        raise ValueError(f"server reports models {ids!r}, expected [{alias!r}]")


def base_payload(model, system_prompt):
    return {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": TEMPLATE_USER},
        ],
        "chat_template_kwargs": dict(CHAT_TEMPLATE_KWARGS),
    }


def expected_server_argv(mode):
    alias = CONTROL_ALIAS if mode == "control" else CANDIDATE_ALIAS
    argv = [
        SERVER_PATH,
        "--model", MODEL_PATH,
        "--alias", alias,
        "--host", "127.0.0.1", "--port", "8081",
        "--numa", "distribute",
        "--ctx-size", "131072",
        "--defrag-thold", "0.1",
        "--parallel", "1",
        "--threads", "64", "--threads-batch", "64",
        "--batch-size", "2048", "--ubatch-size", "2048",
        "-fa", "on",
        "--cache-type-k", "q8_0", "--cache-type-v", "q8_0",
        "--mlock",
        "--jinja",
        "--repeat-penalty", "1.1", "--repeat-last-n", "256",
        "--metrics",
        "--api-key-file", "/home/chuck/.glm-api-key",
        "--reasoning-format", "deepseek",
        "--cache-type-v", "f16",
        "--repeat-penalty", "1.0",
        "--temp", "1.0", "--top-p", "0.95",
        "--chat-template-kwargs", '{"thinking_effort": "low"}',
        "--reasoning-budget", "1024",
        "--spec-type", "ngram-mod:n_max=16,n_min=2",
        "--cache-ram", "0",
    ]
    if mode == "candidate":
        argv.extend(("--reasoning-prefill", REASONING_PREFILL))
    return argv


def runtime_record(mode):
    unit = CONTROL_UNIT if mode == "control" else CANDIDATE_UNIT
    pid = unit_properties(unit)
    proc = Path(f"/proc/{pid}")
    executable = (proc / "exe").resolve(strict=True)
    working_directory = (proc / "cwd").resolve(strict=True)
    argv = [
        item.decode("utf-8", errors="surrogateescape")
        for item in (proc / "cmdline").read_bytes().split(b"\0")
        if item
    ]
    if str(executable) != SERVER_PATH or sha256(executable) != SERVER_SHA256:
        raise ValueError("live server executable differs from the frozen V13 build")
    if str(working_directory) != SERVER_WORKING_DIRECTORY:
        raise ValueError("live server working directory differs")
    if argv != expected_server_argv(mode):
        raise ValueError("live server argv differs from the frozen response-free contract")
    libraries, closure_hash = runtime_closure(pid, executable)
    restart_result = subprocess.run(
        ["systemctl", "show", unit, "--property=NRestarts", "--value"],
        check=True,
        capture_output=True,
        text=True,
    )
    if restart_result.stdout.strip() != "0":
        raise ValueError(f"{unit}: NRestarts is not zero")
    return {
        "unit": unit,
        "pid": pid,
        "started_utc": process_started_utc(pid),
        "engine_commit": ENGINE_COMMIT,
        "executable": {"path": str(executable), "sha256": sha256(executable)},
        "working_directory": str(working_directory),
        "argv": argv,
        "n_restarts": 0,
        "runtime_executable_closure": libraries,
        "runtime_executable_closure_sha256": closure_hash,
    }


def request_audit(unit, expected):
    result = subprocess.run(
        ["journalctl", "-u", unit, "--no-pager", "--output=cat"],
        check=True,
        capture_output=True,
        text=True,
    )
    pattern = re.compile(r'status=(\d+) method="([A-Z]+)" path="([^"]+)"')
    observed = []
    for line in result.stdout.splitlines():
        match = pattern.search(line)
        if match:
            observed.append({
                "status": int(match.group(1)),
                "method": match.group(2),
                "path": match.group(3),
            })
    if observed != expected:
        raise ValueError(f"{unit}: response-free request audit differs: {observed!r}")
    return {
        "requests": observed,
        "normalized_sequence_sha256": sha256_bytes(
            json.dumps(observed, separators=(",", ":"), sort_keys=True).encode()
        ),
    }


def validate_error(name, status, body, expected_message):
    body_text = json.dumps(body, ensure_ascii=False, sort_keys=True)
    if status != 500 or expected_message not in body_text:
        raise ValueError(
            f"{name}: expected HTTP 500 containing {expected_message!r}, "
            f"got status={status}, body={body!r}"
        )
    return {
        "status": status,
        "body_sha256": sha256_bytes(body_text.encode()),
        "required_error": expected_message,
    }


def load_control_receipt(path, system_prompt_record, prefill_record):
    resolved = path.resolve(strict=True)
    receipt = json.loads(resolved.read_text())
    if receipt.get("schema") != "k3-v13-response-free-control-v1":
        raise ValueError("control receipt schema differs")
    if receipt.get("system_prompt") != system_prompt_record:
        raise ValueError("control receipt system prompt differs")
    if receipt.get("reasoning_prefill") != prefill_record:
        raise ValueError("control receipt reasoning prefill differs")
    prompt = receipt.get("prompt")
    if (
        not isinstance(prompt, str)
        or len(prompt.encode()) != BASELINE_PROMPT_BYTES
        or sha256_bytes(prompt.encode()) != BASELINE_PROMPT_SHA256
        or not prompt.endswith(THINKING_SUFFIX)
    ):
        raise ValueError("control receipt baseline prompt differs")
    return receipt


def token_record(tokens):
    if (
        not isinstance(tokens, list)
        or not tokens
        or any(not isinstance(token, int) or isinstance(token, bool) for token in tokens)
    ):
        raise ValueError("invalid tokenizer response")
    encoded = json.dumps(tokens, separators=(",", ":")).encode()
    return {"count": len(tokens), "sha256": sha256_bytes(encoded)}


def write_exclusive(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            json.dump(payload, output, indent=2, sort_keys=True)
            output.write("\n")
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def run_control(args, api_key, system_prompt, system_record, prefill_record):
    runtime = runtime_record("control")
    observed = []
    status, body = request_json("GET", f"{args.base_url}/health", api_key)
    validate_health(status, body)
    observed.append({"status": status, "method": "GET", "path": "/health"})
    status, body = request_json("GET", f"{args.base_url}/v1/models", api_key)
    validate_models(status, body, CONTROL_ALIAS)
    observed.append({"status": status, "method": "GET", "path": "/v1/models"})
    payload = base_payload(CONTROL_ALIAS, system_prompt)
    status, body = request_json(
        "POST", f"{args.base_url}/apply-template", api_key, payload
    )
    observed.append({"status": status, "method": "POST", "path": "/apply-template"})
    if status != 200 or not isinstance(body, dict) or not isinstance(body.get("prompt"), str):
        raise ValueError(f"control /apply-template failed: status={status}, body={body!r}")
    prompt = body["prompt"]
    if len(prompt.encode()) != BASELINE_PROMPT_BYTES:
        raise ValueError("control baseline prompt byte count differs")
    if sha256_bytes(prompt.encode()) != BASELINE_PROMPT_SHA256:
        raise ValueError("control baseline prompt SHA-256 differs")
    if not prompt.endswith(THINKING_SUFFIX):
        raise ValueError("control baseline prompt suffix differs")
    if sha256_bytes(THINKING_SUFFIX.encode()) != BASELINE_SUFFIX_SHA256:
        raise ValueError("frozen baseline suffix constant is internally inconsistent")
    expected = [
        {"status": 200, "method": "GET", "path": "/health"},
        {"status": 200, "method": "GET", "path": "/v1/models"},
        {"status": 200, "method": "POST", "path": "/apply-template"},
    ]
    if observed != expected:
        raise ValueError("control request sequence differs")
    receipt = {
        "schema": "k3-v13-response-free-control-v1",
        "captured_utc": datetime.now(timezone.utc).isoformat(),
        "base_url": args.base_url,
        "model": CONTROL_ALIAS,
        "system_prompt": system_record,
        "reasoning_prefill": prefill_record,
        "request_payload_sha256": sha256_bytes(
            json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
        ),
        "prompt": prompt,
        "prompt_bytes": BASELINE_PROMPT_BYTES,
        "prompt_sha256": BASELINE_PROMPT_SHA256,
        "terminal_fragment": THINKING_SUFFIX,
        "terminal_fragment_sha256": BASELINE_SUFFIX_SHA256,
        "runtime": runtime,
        "request_audit": request_audit(CONTROL_UNIT, expected),
    }
    write_exclusive(args.output, receipt)
    print(f"PASS V13 response-free control: {BASELINE_PROMPT_SHA256}")


def run_candidate(args, api_key, system_prompt, system_record, prefill_record):
    control = load_control_receipt(args.control_receipt, system_record, prefill_record)
    baseline_prompt = control["prompt"]
    runtime = runtime_record("candidate")
    observed = []
    status, body = request_json("GET", f"{args.base_url}/health", api_key)
    validate_health(status, body)
    observed.append({"status": status, "method": "GET", "path": "/health"})
    status, body = request_json("GET", f"{args.base_url}/v1/models", api_key)
    validate_models(status, body, CANDIDATE_ALIAS)
    observed.append({"status": status, "method": "GET", "path": "/v1/models"})

    payload = base_payload(CANDIDATE_ALIAS, system_prompt)
    status, body = request_json(
        "POST", f"{args.base_url}/apply-template", api_key, payload
    )
    observed.append({"status": status, "method": "POST", "path": "/apply-template"})
    if status != 200 or not isinstance(body, dict) or not isinstance(body.get("prompt"), str):
        raise ValueError(f"candidate /apply-template failed: status={status}, body={body!r}")
    prompt = body["prompt"]
    expected_prompt = baseline_prompt + REASONING_PREFILL
    expected_suffix = THINKING_SUFFIX + REASONING_PREFILL
    if prompt != expected_prompt:
        raise ValueError("candidate prompt is not baseline plus the exact reasoning prefill")
    if len(prompt.encode()) != EXTENDED_PROMPT_BYTES:
        raise ValueError("candidate extended prompt byte count differs")
    if sha256_bytes(prompt.encode()) != EXTENDED_PROMPT_SHA256:
        raise ValueError("candidate extended prompt SHA-256 differs")
    if not prompt.endswith(expected_suffix):
        raise ValueError("candidate extended prompt suffix differs")
    if sha256_bytes(expected_suffix.encode()) != EXTENDED_SUFFIX_SHA256:
        raise ValueError("candidate extended suffix SHA-256 differs")

    negative = {}
    disabled = base_payload(CANDIDATE_ALIAS, system_prompt)
    disabled["chat_template_kwargs"]["enable_thinking"] = False
    status, body = request_json(
        "POST", f"{args.base_url}/apply-template", api_key, disabled
    )
    observed.append({"status": status, "method": "POST", "path": "/apply-template"})
    negative["thinking_disabled"] = validate_error(
        "thinking_disabled", status, body,
        "Reasoning prefill requires enable_thinking.",
    )

    assistant = base_payload(CANDIDATE_ALIAS, system_prompt)
    assistant["messages"].append({"role": "assistant", "content": "prefill"})
    status, body = request_json(
        "POST", f"{args.base_url}/apply-template", api_key, assistant
    )
    observed.append({"status": status, "method": "POST", "path": "/apply-template"})
    negative["assistant_prefill"] = validate_error(
        "assistant_prefill", status, body,
        "Reasoning prefill is incompatible with assistant response prefill.",
    )

    no_generation = base_payload(CANDIDATE_ALIAS, system_prompt)
    no_generation["add_generation_prompt"] = False
    status, body = request_json(
        "POST", f"{args.base_url}/apply-template", api_key, no_generation
    )
    observed.append({"status": status, "method": "POST", "path": "/apply-template"})
    negative["generation_prompt_disabled"] = validate_error(
        "generation_prompt_disabled", status, body,
        "Reasoning prefill requires add_generation_prompt=true.",
    )

    client_override = base_payload(CANDIDATE_ALIAS, system_prompt)
    client_override["reasoning_prefill"] = "client override"
    status, body = request_json(
        "POST", f"{args.base_url}/apply-template", api_key, client_override
    )
    observed.append({"status": status, "method": "POST", "path": "/apply-template"})
    negative["client_override"] = validate_error(
        "client_override", status, body,
        "reasoning_prefill is a server-only option; use --reasoning-prefill at startup",
    )

    status, body = request_json(
        "POST", f"{args.base_url}/tokenize", api_key,
        {"content": prompt, "add_special": True},
    )
    observed.append({"status": status, "method": "POST", "path": "/tokenize"})
    if status != 200 or not isinstance(body, dict):
        raise ValueError("combined prompt tokenization failed")
    combined_tokens = body.get("tokens")
    combined_record = token_record(combined_tokens)

    status, body = request_json(
        "POST", f"{args.base_url}/tokenize", api_key,
        {"content": [baseline_prompt, REASONING_PREFILL], "add_special": True},
    )
    observed.append({"status": status, "method": "POST", "path": "/tokenize"})
    if status != 200 or not isinstance(body, dict):
        raise ValueError("split native-prompt/seed tokenization failed")
    split_tokens = body.get("tokens")
    split_record = token_record(split_tokens)
    if combined_tokens != split_tokens:
        raise ValueError(
            "string-appended prompt tokens differ from native prompt plus raw seed tokens"
        )

    expected = [
        {"status": 200, "method": "GET", "path": "/health"},
        {"status": 200, "method": "GET", "path": "/v1/models"},
        {"status": 200, "method": "POST", "path": "/apply-template"},
        *[
            {"status": 500, "method": "POST", "path": "/apply-template"}
            for _ in range(4)
        ],
        {"status": 200, "method": "POST", "path": "/tokenize"},
        {"status": 200, "method": "POST", "path": "/tokenize"},
    ]
    if observed != expected:
        raise ValueError("candidate request sequence differs")
    receipt = {
        "schema": "k3-v13-response-free-preflight-v1",
        "captured_utc": datetime.now(timezone.utc).isoformat(),
        "base_url": args.base_url,
        "model": CANDIDATE_ALIAS,
        "control_receipt": artifact_record(args.control_receipt),
        "system_prompt": system_record,
        "reasoning_prefill": prefill_record,
        "request_payload_sha256": sha256_bytes(
            json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
        ),
        "baseline_prompt": {
            "bytes": BASELINE_PROMPT_BYTES,
            "sha256": BASELINE_PROMPT_SHA256,
            "terminal_fragment_sha256": BASELINE_SUFFIX_SHA256,
        },
        "extended_prompt": {
            "bytes": EXTENDED_PROMPT_BYTES,
            "sha256": EXTENDED_PROMPT_SHA256,
            "terminal_fragment_sha256": EXTENDED_SUFFIX_SHA256,
            "relationship": "exact baseline bytes plus exact reasoning-prefill text",
        },
        "negative_checks": negative,
        "token_equivalence": {
            "combined_prompt": combined_record,
            "native_prompt_plus_raw_seed": split_record,
            "equal": True,
            "add_special": True,
        },
        "runtime": runtime,
        "request_audit": request_audit(CANDIDATE_UNIT, expected),
    }
    write_exclusive(args.output, receipt)
    print(
        f"PASS V13 response-free feature preflight: {EXTENDED_PROMPT_SHA256} "
        f"tokens={combined_record['count']}"
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("control", "candidate"))
    parser.add_argument("--base-url", default="http://127.0.0.1:8081")
    parser.add_argument("--api-key-file", type=Path, default=Path("/home/chuck/.glm-api-key"))
    parser.add_argument("--system-prompt-file", type=Path, required=True)
    parser.add_argument("--reasoning-prefill-file", type=Path, required=True)
    parser.add_argument("--control-receipt", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    try:
        args.base_url = validate_base_url(args.base_url)
        if args.mode == "candidate" and args.control_receipt is None:
            raise ValueError("candidate mode requires --control-receipt")
        if args.mode == "control" and args.control_receipt is not None:
            raise ValueError("control mode does not accept --control-receipt")
        if not args.api_key_file.is_file():
            raise ValueError(f"API key file is unreadable: {args.api_key_file}")
        api_key = args.api_key_file.read_text().strip()
        if not api_key:
            raise ValueError("API key is empty")
        args.system_prompt_file = args.system_prompt_file.resolve(strict=True)
        system_prompt, system_hash = load_system_prompt(args.system_prompt_file)
        if system_hash != SYSTEM_PROMPT_SHA256:
            raise ValueError("system prompt SHA-256 differs")
        args.reasoning_prefill_file = args.reasoning_prefill_file.resolve(strict=True)
        reasoning_prefill, prefill_hash = load_system_prompt(
            args.reasoning_prefill_file
        )
        if reasoning_prefill != REASONING_PREFILL or prefill_hash != REASONING_PREFILL_SHA256:
            raise ValueError("reasoning-prefill artifact differs")
        system_record = artifact_record(args.system_prompt_file)
        prefill_record = artifact_record(args.reasoning_prefill_file)
        if args.mode == "control":
            run_control(args, api_key, system_prompt, system_record, prefill_record)
        else:
            run_candidate(args, api_key, system_prompt, system_record, prefill_record)
    except (
        json.JSONDecodeError,
        OSError,
        subprocess.CalledProcessError,
        urllib.error.URLError,
        ValueError,
    ) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
