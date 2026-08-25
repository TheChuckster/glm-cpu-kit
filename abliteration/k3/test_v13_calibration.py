#!/usr/bin/env python3
"""Focused response-free closure tests for V13 Thought Token Forcing."""

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import gate_v13_calibration as gate
import preflight_v13_reasoning_prefill as preflight
import verify_v13_calibration_state as state
from evaluate_api import load_system_prompt
from evaluate_reasoning_prefill_api import (
    CHAT_TEMPLATE_KWARGS,
    MAX_TOKENS,
    MESSAGE_ROLES,
    REQUEST_ATTEMPTS,
    REQUEST_CONTRACT,
    SEED,
    build_payload,
    classify_reasoning_prefill,
    result_identity,
)


class V13ConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).parent
        self.prompt = self.root / "v10-system-prompt-02-semantic-contract.txt"
        self.prefill = self.root / "v13-reasoning-prefill.txt"

    def args(self):
        system_prompt, system_hash = load_system_prompt(self.prompt)
        prefill, prefill_hash = load_system_prompt(self.prefill)
        return type("Args", (), {
            "model": gate.PROMPTS["prompt13"]["alias"],
            "system_prompt": system_prompt,
            "system_prompt_sha256": system_hash,
            "reasoning_prefill": prefill,
            "reasoning_prefill_sha256": prefill_hash,
        })()

    def test_exact_candidate_and_artifact_identity(self):
        prompt_hash = hashlib.sha256(self.prompt.read_bytes()).hexdigest()
        prefill_hash = hashlib.sha256(self.prefill.read_bytes()).hexdigest()
        expected = {
            "alias": "kimi-k3-q5attn-abl-v13-ttf-cal",
            "filename": self.prompt.name,
            "sha256": prompt_hash,
        }
        self.assertEqual(prompt_hash, gate.PROMPTS["prompt13"]["sha256"])
        self.assertEqual(prefill_hash, gate.REASONING_PREFILL_SHA256)
        self.assertEqual(self.prefill.read_bytes(), b"I know that.\n")
        self.assertEqual(gate.PROMPT_ORDER, ("prompt13",))
        self.assertEqual(gate.PROMPTS, {"prompt13": expected})
        self.assertEqual(state.PROMPTS, {"prompt13": expected})
        self.assertEqual(
            hashlib.sha256(
                (self.root / "evaluate_reasoning_prefill_api.py").read_bytes()
            ).hexdigest(),
            gate.EVALUATOR_SHA256,
        )
        self.assertEqual(
            hashlib.sha256((self.root / "evaluate_api.py").read_bytes()).hexdigest(),
            gate.BASE_EVALUATOR_SHA256,
        )
        self.assertEqual(
            hashlib.sha256(
                (self.root / "verify_v13_calibration_state.py").read_bytes()
            ).hexdigest(),
            gate.STATE_HELPER_SHA256,
        )
        self.assertEqual(
            hashlib.sha256(
                (self.root / "v13-engine-sources.sha256").read_bytes()
            ).hexdigest(),
            gate.ENGINE_SOURCE_MANIFEST_SHA256,
        )

    def test_exact_payload_enables_thinking_without_request_prefill(self):
        args = self.args()
        payload = build_payload(args, {"instruction": "unchanged"}, 3)
        self.assertEqual(payload, {
            "model": gate.PROMPTS["prompt13"]["alias"],
            "seed": SEED + 3,
            "temperature": 0,
            "max_tokens": MAX_TOKENS,
            "stream": False,
            "messages": [
                {"role": "system", "content": args.system_prompt},
                {"role": "user", "content": "unchanged"},
            ],
            "chat_template_kwargs": {
                "enable_thinking": True,
                "thinking_effort": "low",
            },
        })
        self.assertNotIn("reasoning_prefill", payload)

    def test_reasoning_is_exact_seed_plus_generated_continuation(self):
        args = self.args()
        raw = {
            "choices": [{
                "finish_reason": "stop",
                "message": {
                    "content": "Visible answer.",
                    "reasoning_content": "I know that. Continue reasoning.",
                },
            }],
            "usage": {"completion_tokens": 7},
        }
        row = {"id": "row", "instruction": "x", "label": "harmful"}
        result = classify_reasoning_prefill(row, raw, args)
        self.assertEqual(result["response"], "Visible answer.")
        self.assertEqual(result["reasoning_content"], "I know that. Continue reasoning.")
        self.assertEqual(result["reasoning_continuation"], " Continue reasoning.")
        self.assertEqual(result["termination_errors"], [])

    def test_missing_empty_and_duplicated_reasoning_fail_closed(self):
        args = self.args()
        row = {"id": "row", "instruction": "x", "label": "harmful"}
        for reasoning, expected in (
            ("generated only", "does not begin"),
            ("I know that.", "empty generated"),
            ("I know that.I know that.more", "duplicated"),
        ):
            raw = {
                "choices": [{
                    "finish_reason": "stop",
                    "message": {
                        "content": "Visible answer.",
                        "reasoning_content": reasoning,
                    },
                }],
                "usage": {"completion_tokens": 7},
            }
            result = classify_reasoning_prefill(row, raw, args)
            self.assertTrue(
                any(expected in error for error in result["termination_errors"]),
                result["termination_errors"],
            )

    def test_result_identity_contract_and_server_argv_are_frozen(self):
        identity = result_identity(self.args(), 2)
        self.assertEqual(identity["seed"], SEED + 2)
        self.assertEqual(identity["max_tokens"], 2048)
        self.assertEqual(identity["request_attempts"], REQUEST_ATTEMPTS)
        self.assertEqual(identity["request_message_roles"], MESSAGE_ROLES)
        self.assertEqual(
            identity["request_chat_template_kwargs"], CHAT_TEMPLATE_KWARGS
        )
        self.assertEqual(gate.REQUEST_CONTRACT, REQUEST_CONTRACT)
        argv = gate.expected_server_argv(gate.PROMPTS["prompt13"]["alias"])
        self.assertEqual(argv[-2:], ["--reasoning-prefill", "I know that."])
        self.assertEqual(argv.count("--reasoning-prefill"), 1)

    def test_prefill_contract_reproduces_and_detects_reasoning_tampering(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            evaluation = root / "evaluation.jsonl"
            summary = root / "evaluation.jsonl.summary.json"
            preflight_receipt = root / "preflight.json"
            preflight_receipt.write_text("{}\n")
            row = {
                "id": "row",
                "response": "Visible answer.",
                "reasoning_content": "I know that. continuation",
                "reasoning_continuation": " continuation",
                "reasoning_prefill_sha256": gate.REASONING_PREFILL_SHA256,
                "request_message_roles": MESSAGE_ROLES,
                "request_chat_template_kwargs": CHAT_TEMPLATE_KWARGS,
            }
            evaluation.write_text(json.dumps(row, sort_keys=True) + "\n")
            summary.write_text(json.dumps({
                "reasoning_prefill_file": str(self.prefill.resolve()),
                "reasoning_prefill_sha256": gate.REASONING_PREFILL_SHA256,
                "request_contract": REQUEST_CONTRACT,
            }, sort_keys=True) + "\n")
            provenance = {
                "evaluation_summary": gate.core.artifact_record(summary),
                "evaluation_protocol_artifacts": [
                    gate.core.artifact_record(self.prefill),
                    gate.core.artifact_record(self.root / "evaluate_api.py"),
                    gate.core.artifact_record(preflight_receipt),
                    gate.core.artifact_record(self.root / "v13-engine-sources.sha256"),
                ],
            }
            old_preflight = gate.PREFLIGHT_RECEIPT_SHA256
            gate.PREFLIGHT_RECEIPT_SHA256 = gate.core.sha256(preflight_receipt)
            try:
                self.assertEqual(
                    gate.validate_reasoning_prefill_contract(provenance, evaluation),
                    gate.core.artifact_record(self.prefill),
                )
                row["reasoning_content"] += " altered"
                evaluation.write_text(json.dumps(row, sort_keys=True) + "\n")
                with self.assertRaisesRegex(ValueError, "reconstructed reasoning differs"):
                    gate.validate_reasoning_prefill_contract(provenance, evaluation)
            finally:
                gate.PREFLIGHT_RECEIPT_SHA256 = old_preflight

    def test_response_free_hashes_and_raw_token_record_are_frozen(self):
        self.assertEqual(preflight.TEMPLATE_USER, "response-free thinking-template check")
        self.assertEqual(preflight.BASELINE_PROMPT_BYTES, 1152)
        self.assertEqual(
            preflight.BASELINE_PROMPT_SHA256,
            "70667c925b21854812675b3c3acd5055218495adbe816e1e6636116081e2fe22",
        )
        self.assertEqual(preflight.EXTENDED_PROMPT_BYTES, 1164)
        self.assertEqual(
            preflight.EXTENDED_PROMPT_SHA256,
            "aa5dd4313ea8579d7f032e5a02bb39c4efd10c7757d9d02125c34a26c2f98ec2",
        )
        self.assertEqual(
            preflight.expected_server_argv("candidate")[-2:],
            ["--reasoning-prefill", "I know that."],
        )
        tokens = [1, 2, 3]
        self.assertEqual(preflight.token_record(tokens), {
            "count": 3,
            "sha256": hashlib.sha256(b"[1,2,3]").hexdigest(),
        })

    def test_reuses_exact_partition_and_two_request_prefix(self):
        self.assertEqual(
            [len(gate.DATASETS[phase]["ids"]) for phase in gate.PHASE_ORDER],
            [2, 2, 96],
        )
        prefix = json.loads(
            (self.root / "v10-calibration-request-prefix.json").read_text()
        )
        self.assertEqual(prefix, state.REQUEST_PREFIX)


if __name__ == "__main__":
    unittest.main()
