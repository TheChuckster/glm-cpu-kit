#!/usr/bin/env python3
"""Configure and extend the frozen V10 phase gate for V13 TTF evidence."""

from pathlib import Path

import gate_v10_calibration as core


PROMPT_ORDER = ("prompt13",)
PROMPTS = {
    "prompt13": {
        "alias": "kimi-k3-q5attn-abl-v13-ttf-cal",
        "filename": "v10-system-prompt-02-semantic-contract.txt",
        "sha256": "44fc73623eb35a4b19b9cbfdf682a015af832ed8954233c68b8cf5845ab116f9",
    },
}
REASONING_PREFILL_FILENAME = "v13-reasoning-prefill.txt"
REASONING_PREFILL_SHA256 = (
    "61185bb05cc1732c653ff21bcbaf1f7b38a0b1823b13422c25877940a0184fb5"
)
REASONING_PREFILL_TEXT = "I know that."
EVALUATOR_SHA256 = "108e67a9059ff86fb6e37aa0047b764c09c69ba11987ed9c2994d81470db3b58"
BASE_EVALUATOR_SHA256 = "1f6b43a330cfbddd8334170c067e6719607f12d9fdeff6d1718d4f8ab733e61a"
PROVENANCE_HELPER_SHA256 = (
    "63836bc34d7096a062e90cee855d320944fac71ab55769700f6dfa3fab8e4616"
)
STATE_HELPER_SHA256 = "ceb7f2c8b3f5b990d4686da4d6dbdbb6188596bf5a0d0e1dab1fe435dabaecb1"
PREFLIGHT_RECEIPT_SHA256 = (
    "1b93736d8341ef04aacfdfd3a546945c07d19a821021b256d66e964f91c1ac36"
)
ENGINE_SOURCE_MANIFEST_SHA256 = (
    "0800c4cbe4bf5d169c4b57d53cc1b68b4534861c9555c870a4eaf5ec97d97c13"
)
PHASE_SCHEMA = "k3-v13-calibration-phase-v1"
SELECTION_SCHEMA = "k3-v13-calibration-selection-v1"
DATASETS = core.DATASETS
PHASE_ORDER = core.PHASE_ORDER
MESSAGE_ROLES = ["system", "user"]
CHAT_TEMPLATE_KWARGS = {"enable_thinking": True, "thinking_effort": "low"}
REQUEST_CONTRACT = {
    "chat_template_kwargs": CHAT_TEMPLATE_KWARGS,
    "message_roles": MESSAGE_ROLES,
    "reasoning_budget_tokens": 1024,
    "reasoning_prefill_mode": "server_cli_raw_after_native_reasoning_start",
    "reasoning_prefill_request_field": "absent",
    "response_assembly": "server_reconstructed_reasoning_prefill_plus_generated_continuation",
    "stream": False,
    "temperature": 0,
}
SERVER_PATH = "/home/chuck/ik_llama-v13-98de9a7f/build-v13/bin/llama-server"
SERVER_WORKING_DIRECTORY = "/home/chuck/ik_llama-v13-98de9a7f"
SERVER_SHA256 = "b0a26936cfe28302dbfd9d51ffaddaceba9771e3ac15878a1994f7bab57a44a6"
RUNTIME_CLOSURE_SHA256 = (
    "d277413a0257024b9d7b9b5efb5b930bf089d4c070d26cd09c46244625e06607"
)

_CORE_VALIDATE_PROVENANCE = core.validate_provenance
_CORE_PHASE_CORE = core.phase_core


def expected_server_argv(alias):
    return [
        SERVER_PATH,
        "--model", core.MODEL_PATH,
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
        "--reasoning-prefill", REASONING_PREFILL_TEXT,
    ]


def validate_reasoning_prefill_contract(provenance, evaluation):
    summary_record = provenance.get("evaluation_summary") or {}
    summary_path_text = summary_record.get("path")
    if not isinstance(summary_path_text, str):
        raise ValueError("provenance has no evaluator summary path")
    summary_path = Path(summary_path_text).resolve(strict=True)
    if {
        key: summary_record.get(key) for key in ("path", "bytes", "sha256")
    } != core.artifact_record(summary_path):
        raise ValueError("evaluator summary artifact changed")
    summary = core.load_object(summary_path)
    prefill_path_text = summary.get("reasoning_prefill_file")
    if not isinstance(prefill_path_text, str):
        raise ValueError("evaluator summary has no reasoning-prefill path")
    prefill_path = Path(prefill_path_text).resolve(strict=True)
    if prefill_path.name != REASONING_PREFILL_FILENAME:
        raise ValueError("reasoning-prefill filename differs")
    prefill_record = core.artifact_record(prefill_path)
    if prefill_record["sha256"] != REASONING_PREFILL_SHA256:
        raise ValueError("reasoning-prefill SHA-256 differs")
    if summary.get("reasoning_prefill_sha256") != REASONING_PREFILL_SHA256:
        raise ValueError("evaluator summary reasoning-prefill SHA-256 differs")
    if summary.get("request_contract") != REQUEST_CONTRACT:
        raise ValueError("evaluator request contract differs")

    records = provenance.get("evaluation_protocol_artifacts")
    if not isinstance(records, list):
        raise ValueError("provenance has no protocol artifact closure")
    core.require_protocol_artifact(records, prefill_path, REASONING_PREFILL_SHA256)
    core.require_protocol_artifact(records, expected_sha=BASE_EVALUATOR_SHA256)
    core.require_protocol_artifact(records, expected_sha=PREFLIGHT_RECEIPT_SHA256)
    core.require_protocol_artifact(records, expected_sha=ENGINE_SOURCE_MANIFEST_SHA256)

    raw = prefill_path.read_bytes()
    if raw != b"I know that.\n":
        raise ValueError("reasoning-prefill bytes differ from the locked seed")
    rows = core.load_jsonl(evaluation)
    for row in rows:
        item_id = row.get("id")
        reasoning = row.get("reasoning_content")
        continuation = row.get("reasoning_continuation")
        if not isinstance(reasoning, str):
            raise ValueError(f"{item_id}: reasoning content is not a string")
        if not isinstance(continuation, str) or not continuation.strip():
            raise ValueError(f"{item_id}: generated reasoning continuation is empty")
        if continuation.startswith(REASONING_PREFILL_TEXT):
            raise ValueError(f"{item_id}: reasoning prefill was duplicated")
        if reasoning != REASONING_PREFILL_TEXT + continuation:
            raise ValueError(f"{item_id}: reconstructed reasoning differs")
        if row.get("reasoning_prefill_sha256") != REASONING_PREFILL_SHA256:
            raise ValueError(f"{item_id}: reasoning-prefill identity differs")
        if row.get("request_message_roles") != MESSAGE_ROLES:
            raise ValueError(f"{item_id}: request message roles differ")
        if row.get("request_chat_template_kwargs") != CHAT_TEMPLATE_KWARGS:
            raise ValueError(f"{item_id}: chat-template kwargs differ")
    return prefill_record


def validate_provenance(path, prompt, phase, evaluation, prompt_file, state_path):
    provenance = _CORE_VALIDATE_PROVENANCE(
        path, prompt, phase, evaluation, prompt_file, state_path
    )
    validate_reasoning_prefill_contract(provenance, evaluation)
    return provenance


def phase_core(prompt, phase, evaluation_path, review_path, provenance_path,
               prompt_file, state_path):
    receipt = _CORE_PHASE_CORE(
        prompt, phase, evaluation_path, review_path, provenance_path,
        prompt_file, state_path,
    )
    provenance = core.load_object(Path(provenance_path).resolve(strict=True))
    receipt["reasoning_prefill"] = validate_reasoning_prefill_contract(
        provenance, Path(evaluation_path).resolve(strict=True)
    )
    return receipt


def configure_core():
    core.PROMPT_ORDER = PROMPT_ORDER
    core.PROMPTS = PROMPTS
    core.SERVER_PATH = SERVER_PATH
    core.SERVER_WORKING_DIRECTORY = SERVER_WORKING_DIRECTORY
    core.SERVER_SHA256 = SERVER_SHA256
    core.RUNTIME_CLOSURE_SHA256 = RUNTIME_CLOSURE_SHA256
    core.EVALUATOR_SHA256 = EVALUATOR_SHA256
    core.PROVENANCE_HELPER_SHA256 = PROVENANCE_HELPER_SHA256
    core.STATE_HELPER_SHA256 = STATE_HELPER_SHA256
    core.PHASE_SCHEMA = PHASE_SCHEMA
    core.SELECTION_SCHEMA = SELECTION_SCHEMA
    core.expected_server_argv = expected_server_argv
    core.validate_provenance = validate_provenance
    core.phase_core = phase_core


def main():
    configure_core()
    core.main()


if __name__ == "__main__":
    main()
