#!/usr/bin/env python3
"""Focused response-free closure tests for V20 canonical DRY."""

import hashlib
import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import capture_server_provenance as provenance
import gate_v20_calibration as gate
import preflight_v20_reasoning_prefill as preflight
import verify_v20_calibration_state as state
from evaluate_api import load_system_prompt
from evaluate_reasoning_prefill_api_v20 import (
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


class V20ConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).parent
        self.prompt = self.root / "v10-system-prompt-02-semantic-contract.txt"
        self.prefill = self.root / "v20-reasoning-prefill.txt"

    def args(self):
        system_prompt, system_hash = load_system_prompt(self.prompt)
        prefill, prefill_hash = load_system_prompt(self.prefill)
        return type("Args", (), {
            "model": gate.PROMPTS["prompt20"]["alias"],
            "system_prompt": system_prompt,
            "system_prompt_sha256": system_hash,
            "reasoning_prefill": prefill,
            "reasoning_prefill_sha256": prefill_hash,
        })()

    def test_exact_candidate_and_artifact_identity(self):
        prompt_hash = hashlib.sha256(self.prompt.read_bytes()).hexdigest()
        prefill_hash = hashlib.sha256(self.prefill.read_bytes()).hexdigest()
        expected = {
            "alias": "kimi-k3-q5attn-abl-v20-dry-ttf-cal",
            "filename": self.prompt.name,
            "sha256": prompt_hash,
        }
        self.assertEqual(prompt_hash, gate.PROMPTS["prompt20"]["sha256"])
        self.assertEqual(prefill_hash, gate.REASONING_PREFILL_SHA256)
        self.assertEqual(
            self.prefill.read_bytes(),
            (gate.REASONING_PREFILL_TEXT + "\n").encode(),
        )
        self.assertEqual(gate.PROMPT_ORDER, ("prompt20",))
        self.assertEqual(gate.PROMPTS, {"prompt20": expected})
        self.assertEqual(state.PROMPTS, {"prompt20": expected})
        self.assertEqual(
            hashlib.sha256(
                (self.root / "evaluate_reasoning_prefill_api_v20.py").read_bytes()
            ).hexdigest(),
            gate.EVALUATOR_SHA256,
        )
        self.assertEqual(
            hashlib.sha256((self.root / "evaluate_api.py").read_bytes()).hexdigest(),
            gate.BASE_EVALUATOR_SHA256,
        )
        self.assertEqual(
            hashlib.sha256(
                (self.root / "verify_v20_calibration_state.py").read_bytes()
            ).hexdigest(),
            gate.STATE_HELPER_SHA256,
        )
        self.assertEqual(
            hashlib.sha256(
                (self.root / "v20-engine-sources.sha256").read_bytes()
            ).hexdigest(),
            gate.ENGINE_SOURCE_MANIFEST_SHA256,
        )

    def test_exact_payload_enables_thinking_without_request_prefill(self):
        args = self.args()
        payload = build_payload(args, {"instruction": "unchanged"}, 3)
        self.assertEqual(payload, {
            "model": gate.PROMPTS["prompt20"]["alias"],
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
                    "reasoning_content": (
                        gate.REASONING_PREFILL_TEXT + " Continue reasoning."
                    ),
                },
            }],
            "usage": {"completion_tokens": 7},
        }
        row = {"id": "row", "instruction": "x", "label": "harmful"}
        result = classify_reasoning_prefill(row, raw, args)
        self.assertEqual(result["response"], "Visible answer.")
        self.assertEqual(
            result["reasoning_content"],
            gate.REASONING_PREFILL_TEXT + " Continue reasoning.",
        )
        self.assertEqual(result["reasoning_continuation"], " Continue reasoning.")
        self.assertEqual(result["termination_errors"], [])

    def test_missing_empty_and_duplicated_reasoning_fail_closed(self):
        args = self.args()
        row = {"id": "row", "instruction": "x", "label": "harmful"}
        for reasoning, expected in (
            ("generated only", "does not begin"),
            (gate.REASONING_PREFILL_TEXT, "empty generated"),
            (
                gate.REASONING_PREFILL_TEXT
                + gate.REASONING_PREFILL_TEXT
                + "more",
                "duplicated",
            ),
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
        self.assertEqual(REQUEST_CONTRACT["dry_sampler"], gate.DRY_SAMPLER)
        self.assertEqual(REQUEST_CONTRACT["dry_sampler_mode"], "server_cli")
        argv = gate.expected_server_argv(gate.PROMPTS["prompt20"]["alias"])
        self.assertEqual(
            argv[-2:], ["--reasoning-prefill", gate.REASONING_PREFILL_TEXT]
        )
        self.assertEqual(argv.count("--reasoning-prefill"), 1)
        dry_start = argv.index("--dry-multiplier")
        self.assertEqual(
            argv[dry_start:dry_start + len(gate.DRY_ARGV)],
            list(gate.DRY_ARGV),
        )
        self.assertLess(dry_start, argv.index("--reasoning-prefill"))

    def test_dry_argv_is_exact_and_all_mutations_fail_closed(self):
        self.assertEqual(preflight.DRY_ARGV, gate.DRY_ARGV)
        control = preflight.expected_server_argv("control")
        candidate = preflight.expected_server_argv("candidate")
        behavior = gate.expected_server_argv(gate.PROMPTS["prompt20"]["alias"])
        preflight.validate_dry_argv(control, required=False)
        preflight.validate_dry_argv(candidate, required=True)
        preflight.validate_dry_argv(behavior, required=True)
        for option in preflight.DRY_OPTIONS:
            self.assertEqual(control.count(option), 0)
            self.assertEqual(candidate.count(option), 1)
            self.assertEqual(behavior.count(option), 1)

        missing = candidate.copy()
        start = missing.index("--dry-base")
        del missing[start:start + 2]
        duplicated = candidate + list(preflight.DRY_ARGV)
        reordered = candidate.copy()
        start = reordered.index("--dry-multiplier")
        reordered[start:start + len(preflight.DRY_ARGV)] = [
            *preflight.DRY_ARGV[2:4],
            *preflight.DRY_ARGV[0:2],
            *preflight.DRY_ARGV[4:],
        ]
        altered = candidate.copy()
        altered[altered.index("--dry-base") + 1] = "1.76"
        for name, argv in (
            ("missing", missing),
            ("duplicated", duplicated),
            ("reordered", reordered),
            ("altered", altered),
        ):
            with self.subTest(name=name), self.assertRaises(ValueError):
                preflight.validate_dry_argv(argv, required=True)
        with self.assertRaises(ValueError):
            preflight.validate_dry_argv(
                [*control, "--dry-multiplier", "0.8"], required=False
            )

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
                "reasoning_content": gate.REASONING_PREFILL_TEXT + " continuation",
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
                    gate.core.artifact_record(self.root / "v20-engine-sources.sha256"),
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
        self.assertEqual(preflight.EXTENDED_PROMPT_BYTES, 3349)
        self.assertEqual(
            preflight.EXTENDED_PROMPT_SHA256,
            "772faf9144a562d3c6f9df22191dd6ca550390d80d922ad18188873c8621b4f0",
        )
        self.assertEqual(
            preflight.expected_server_argv("candidate")[-2:],
            ["--reasoning-prefill", gate.REASONING_PREFILL_TEXT],
        )
        self.assertNotIn("--dry-multiplier", preflight.expected_server_argv("control"))
        tokens = [1, 2, 3]
        self.assertEqual(preflight.token_record(tokens), {
            "count": 3,
            "sha256": hashlib.sha256(b"[1,2,3]").hexdigest(),
        })

    def test_provenance_request_audit_is_scoped_to_the_live_pid(self):
        sequence = [
            *state.REQUEST_PREFIX,
            {"status": 200, "method": "GET", "path": "/v1/models"},
            {
                "status": 200,
                "method": "POST",
                "path": "/v1/chat/completions",
            },
            {"status": 200, "method": "GET", "path": "/v1/models"},
        ]
        journal = "\n".join(
            f'log status={row["status"]} method="{row["method"]}" '
            f'path="{row["path"]}"'
            for row in sequence
        )
        completed = mock.Mock(stdout=journal)
        with mock.patch.object(
            provenance.subprocess, "run", return_value=completed
        ) as run:
            result = provenance.request_audit(
                "unique.service", 1, prefix=state.REQUEST_PREFIX, pid=4242
            )
        run.assert_called_once_with(
            [
                "journalctl",
                "-u",
                "unique.service",
                "_PID=4242",
                "--no-pager",
                "--output=cat",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result["total_requests"], len(sequence))
        for invalid in (0, -1, True, "4242"):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                provenance.request_audit("unique.service", 1, pid=invalid)

    def test_prompt20_phase_unit_and_run_root_are_unique(self):
        launcher = (self.root / "run_v20_calibration_server.sh").read_text()
        self.assertIn("RUN_ROOT=/models/.abliteration/k3/v20-calibration-run-v1", launcher)
        self.assertIn(
            "UNIT=kimi-k3-q5attn-abl-v10-${PROMPT}-${PHASE}-cal.service",
            launcher,
        )
        self.assertIn("prompt20)", launcher)
        self.assertIn("ALIAS=kimi-k3-q5attn-abl-v20-dry-ttf-cal", launcher)
        self.assertIn("--dry-multiplier 0.8", launcher)
        self.assertIn("--dry-base 1.75", launcher)
        self.assertIn("--dry-allowed-length 4", launcher)
        self.assertIn("--dry-penalty-last-n -1", launcher)
        self.assertNotIn("prompt16", launcher)

    def test_launcher_literal_sha256_values_are_well_formed(self):
        for filename in (
            "run_v20_response_free_preflight.sh",
            "run_v20_calibration_server.sh",
        ):
            launcher = (self.root / filename).read_text()
            values = re.findall(r"check_sha256 ([0-9a-f]+) ", launcher)
            self.assertGreater(len(values), 20)
            self.assertTrue(
                all(re.fullmatch(r"[0-9a-f]{64}", value) for value in values),
                filename,
            )

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
