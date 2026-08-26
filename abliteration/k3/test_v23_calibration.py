#!/usr/bin/env python3
"""Focused response-free closure tests for V23 high-multiplier DRY."""

import hashlib
import json
import os
import re
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest import mock

import capture_server_provenance as provenance
import gate_v23_calibration as gate
import preflight_v23_reasoning_prefill as preflight
import verify_v23_calibration_state as state
from evaluate_api import load_system_prompt
from evaluate_reasoning_prefill_api_v23 import (
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


class V23ConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).parent
        self.prompt = self.root / "v10-system-prompt-02-semantic-contract.txt"
        self.prefill = self.root / "v23-reasoning-prefill.txt"

    def args(self):
        system_prompt, system_hash = load_system_prompt(self.prompt)
        prefill, prefill_hash = load_system_prompt(self.prefill)
        return type("Args", (), {
            "model": gate.PROMPTS["prompt23"]["alias"],
            "system_prompt": system_prompt,
            "system_prompt_sha256": system_hash,
            "reasoning_prefill": prefill,
            "reasoning_prefill_sha256": prefill_hash,
        })()

    def test_exact_candidate_and_artifact_identity(self):
        prompt_hash = hashlib.sha256(self.prompt.read_bytes()).hexdigest()
        prefill_hash = hashlib.sha256(self.prefill.read_bytes()).hexdigest()
        expected = {
            "alias": "kimi-k3-q5attn-abl-v23-dry-ttf-cal",
            "filename": self.prompt.name,
            "sha256": prompt_hash,
        }
        self.assertEqual(prompt_hash, gate.PROMPTS["prompt23"]["sha256"])
        self.assertEqual(prefill_hash, gate.REASONING_PREFILL_SHA256)
        self.assertEqual(
            self.prefill.read_bytes(),
            (gate.REASONING_PREFILL_TEXT + "\n").encode(),
        )
        self.assertEqual(gate.PROMPT_ORDER, ("prompt23",))
        self.assertEqual(gate.PROMPTS, {"prompt23": expected})
        self.assertEqual(state.PROMPTS, {"prompt23": expected})
        self.assertEqual(
            hashlib.sha256(
                (self.root / "evaluate_reasoning_prefill_api_v23.py").read_bytes()
            ).hexdigest(),
            gate.EVALUATOR_SHA256,
        )
        self.assertEqual(
            hashlib.sha256((self.root / "evaluate_api.py").read_bytes()).hexdigest(),
            gate.BASE_EVALUATOR_SHA256,
        )
        self.assertEqual(
            hashlib.sha256(
                (self.root / "verify_v23_calibration_state.py").read_bytes()
            ).hexdigest(),
            gate.STATE_HELPER_SHA256,
        )
        self.assertEqual(
            hashlib.sha256(
                (self.root / "v23-engine-sources.sha256").read_bytes()
            ).hexdigest(),
            gate.ENGINE_SOURCE_MANIFEST_SHA256,
        )

    def test_engine_correction_identity_and_source_manifest_are_frozen(self):
        self.assertEqual(
            preflight.ENGINE_COMMIT,
            "23695c7a444dcfaaf892bebfefb4a4a8394e3c37",
        )
        self.assertEqual(preflight.SERVER_PATH, gate.SERVER_PATH)
        self.assertEqual(preflight.SERVER_SHA256, gate.SERVER_SHA256)
        self.assertEqual(
            gate.SERVER_PATH,
            "/home/chuck/ik_llama-v21-23695c7a/build-v21/bin/llama-server",
        )
        self.assertEqual(
            gate.SERVER_SHA256,
            "13dbafbeab3bb9438bdf784b2df4b211bddbd167c9ab4e236f3c23b78180508a",
        )

        records = {}
        for line in (self.root / "v23-engine-sources.sha256").read_text().splitlines():
            digest, filename = line.split("  ", 1)
            self.assertRegex(digest, r"^[0-9a-f]{64}$")
            self.assertNotIn(filename, records)
            records[filename] = digest
        self.assertEqual(len(records), 13)
        self.assertEqual(
            records["common/sampling.cpp"],
            "46749557b2f7a97acc46b6fc3ab6daffcf9b69389c21e62d3355935c059e27cc",
        )
        self.assertEqual(
            records["common/sampling.h"],
            "185766ba88248cda3c35c1f3d96079ba7ee1f2752f4a6c3d13448a40167fb7a6",
        )
        self.assertEqual(
            records["tests/test-greedy-dry.cpp"],
            "d1c2474a0505d70c74cb197806e4c151147239d0e070ff7fc93af9348f183283",
        )

    def test_all_engine_test_receipts_are_green(self):
        receipts_root = Path(
            os.environ.get(
                "K3_V23_RECEIPTS_DIR",
                self.root / "receipts",
            )
        ).resolve(strict=True)
        receipts = {
            "v23-local-normal-greedy-dry.xml": ("test-greedy-dry", "charbro"),
            "v23-local-normal-reasoning-prefill.xml": (
                "test-reasoning-prefill", "charbro"
            ),
            "v23-local-asan-ubsan-greedy-dry.xml": (
                "test-greedy-dry", "charbro"
            ),
            "v23-local-asan-ubsan-reasoning-prefill.xml": (
                "test-reasoning-prefill", "charbro"
            ),
            "v23-remote-normal-greedy-dry.xml": (
                "test-greedy-dry", "chuckdancer"
            ),
            "v23-remote-normal-reasoning-prefill.xml": (
                "test-reasoning-prefill", "chuckdancer"
            ),
        }
        for filename, (test_name, hostname) in receipts.items():
            with self.subTest(filename=filename):
                suite = ET.parse(receipts_root / filename).getroot()
                self.assertEqual(suite.tag, "testsuite")
                self.assertEqual(suite.attrib["tests"], "1")
                self.assertEqual(suite.attrib["failures"], "0")
                self.assertEqual(suite.attrib["disabled"], "0")
                self.assertEqual(suite.attrib["skipped"], "0")
                self.assertEqual(suite.attrib["hostname"], hostname)
                cases = suite.findall("testcase")
                self.assertEqual(len(cases), 1)
                self.assertEqual(cases[0].attrib["name"], test_name)
                self.assertEqual(cases[0].attrib["status"], "run")
                self.assertEqual(cases[0].findall("failure"), [])
                self.assertEqual(cases[0].findall("error"), [])

    def test_exact_payload_enables_thinking_without_request_prefill(self):
        args = self.args()
        payload = build_payload(args, {"instruction": "unchanged"}, 3)
        self.assertEqual(payload, {
            "model": gate.PROMPTS["prompt23"]["alias"],
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
        self.assertEqual(gate.DRY_SAMPLER["multiplier"], 2.0)
        self.assertEqual(gate.DRY_SAMPLER["base"], 1.75)
        self.assertEqual(gate.DRY_SAMPLER["allowed_length"], 4)
        self.assertEqual(gate.DRY_SAMPLER["penalty_last_n"], -1)
        self.assertEqual(
            gate.DRY_SAMPLER["sequence_breakers"], ["\n", ":", "\"", "*"]
        )
        argv = gate.expected_server_argv(gate.PROMPTS["prompt23"]["alias"])
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
        behavior = gate.expected_server_argv(gate.PROMPTS["prompt23"]["alias"])
        preflight.validate_dry_argv(control, required=False)
        preflight.validate_dry_argv(candidate, required=True)
        preflight.validate_dry_argv(behavior, required=True)
        for option in preflight.DRY_OPTIONS:
            self.assertEqual(control.count(option), 0)
            self.assertEqual(candidate.count(option), 1)
            self.assertEqual(behavior.count(option), 1)
        for argv in (control, candidate, behavior):
            self.assertNotIn(preflight.DRY_SEQUENCE_BREAKER_OPTION, argv)

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
        changed_breakers = [
            *candidate,
            preflight.DRY_SEQUENCE_BREAKER_OPTION,
            "none",
        ]
        for name, argv in (
            ("missing", missing),
            ("duplicated", duplicated),
            ("reordered", reordered),
            ("altered", altered),
            ("changed_breakers", changed_breakers),
        ):
            with self.subTest(name=name), self.assertRaises(ValueError):
                preflight.validate_dry_argv(argv, required=True)
        with self.assertRaises(ValueError):
            preflight.validate_dry_argv(
                [*control, "--dry-multiplier", "2.0"], required=False
            )
        with self.assertRaises(ValueError):
            preflight.validate_dry_argv(
                [*control, "--dry-sequence-breaker=none"], required=False
            )

    def test_effective_props_bind_control_and_feature_dry_settings(self):
        def body(alias, dry):
            return {
                "model_alias": alias,
                "default_generation_settings": {
                    "dry_multiplier": dry["multiplier"],
                    "dry_base": dry["base"],
                    "dry_allowed_length": dry["allowed_length"],
                    "dry_penalty_last_n": dry["penalty_last_n"],
                    "dry_sequence_breakers": list(dry["sequence_breakers"]),
                    "temperature": 1.0,
                },
            }

        control = body(preflight.CONTROL_ALIAS, preflight.CONTROL_DRY_SAMPLER)
        feature = body(preflight.CANDIDATE_ALIAS, preflight.DRY_SAMPLER)
        self.assertEqual(
            preflight.validate_props(
                200, control, preflight.CONTROL_ALIAS,
                preflight.CONTROL_DRY_SAMPLER,
            )["dry_sampler"],
            preflight.CONTROL_DRY_SAMPLER,
        )
        self.assertEqual(
            preflight.validate_props(
                200, feature, preflight.CANDIDATE_ALIAS, preflight.DRY_SAMPLER,
            )["dry_sampler"],
            preflight.DRY_SAMPLER,
        )
        altered = body(preflight.CANDIDATE_ALIAS, preflight.DRY_SAMPLER)
        altered["default_generation_settings"]["dry_multiplier"] = 0.8
        with self.assertRaisesRegex(ValueError, "effective DRY settings differ"):
            preflight.validate_props(
                200, altered, preflight.CANDIDATE_ALIAS, preflight.DRY_SAMPLER,
            )
        with self.assertRaisesRegex(ValueError, "model alias"):
            preflight.validate_props(
                200, feature, preflight.CONTROL_ALIAS, preflight.DRY_SAMPLER,
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
                    gate.core.artifact_record(self.root / "v23-engine-sources.sha256"),
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

    def test_prompt23_phase_unit_and_run_root_are_unique(self):
        launcher = (self.root / "run_v23_calibration_server.sh").read_text()
        self.assertIn("RUN_ROOT=/models/.abliteration/k3/v23-calibration-run-v1", launcher)
        self.assertIn(
            "UNIT=kimi-k3-q5attn-abl-v10-${PROMPT}-${PHASE}-cal.service",
            launcher,
        )
        self.assertIn("prompt23)", launcher)
        self.assertIn("ALIAS=kimi-k3-q5attn-abl-v23-dry-ttf-cal", launcher)
        self.assertIn("--dry-multiplier 2.0", launcher)
        self.assertIn("--dry-base 1.75", launcher)
        self.assertIn("--dry-allowed-length 4", launcher)
        self.assertIn("--dry-penalty-last-n -1", launcher)
        self.assertNotIn("--dry-sequence-breaker", launcher)
        self.assertNotIn("prompt16", launcher)

    def test_launcher_literal_sha256_values_are_well_formed(self):
        for filename in (
            "run_v23_response_free_preflight.sh",
            "run_v23_calibration_server.sh",
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
