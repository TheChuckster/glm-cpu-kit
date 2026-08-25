#!/usr/bin/env python3
"""Fail-closed tests for V10 partitioning, provenance, and phase selection."""

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import capture_server_provenance as provenance
import gate_v10_calibration as gate
import prepare_v10_calibration as prepare
import verify_v10_calibration_state as state


def write_jsonl(path, rows):
    path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows))
    return path


class PartitionTests(unittest.TestCase):
    def test_partition_is_exact_2_2_96_cover(self):
        rows = [
            {
                "id": f"jailbreakbench-harmful-{index:03d}",
                "instruction": f"instruction {index}",
                "label": "harmful",
            }
            for index in range(100)
        ]
        partitions = prepare.partition_rows(rows)
        self.assertEqual([len(partitions[key]) for key in prepare.FILENAMES], [2, 2, 96])
        self.assertEqual(
            [row["id"] for row in partitions["failures"]],
            list(prepare.FAILURE_IDS),
        )
        self.assertEqual(
            [row["id"] for row in partitions["stability"]],
            list(prepare.STABILITY_IDS),
        )
        flattened = sum((partitions[key] for key in prepare.FILENAMES), [])
        self.assertEqual(len({row["id"] for row in flattened}), 100)

    def test_partition_rejects_missing_or_duplicate_cover(self):
        rows = [
            {
                "id": f"jailbreakbench-harmful-{index:03d}",
                "instruction": "x",
                "label": "harmful",
            }
            for index in range(99)
        ]
        with self.assertRaises(ValueError):
            prepare.partition_rows(rows)


class StateTests(unittest.TestCase):
    def test_exact_loopback_and_request_prefix(self):
        self.assertEqual(
            state.validate_base_url("http://127.0.0.1:8081/"),
            "http://127.0.0.1:8081",
        )
        for value in (
            "https://127.0.0.1:8081",
            "http://localhost:8081",
            "http://127.0.0.1:8080",
            "http://127.0.0.1:8081/v1",
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                state.validate_base_url(value)
        prefix = Path(__file__).with_name("v10-calibration-request-prefix.json")
        self.assertEqual(json.loads(prefix.read_text()), state.REQUEST_PREFIX)

    def test_health_and_model_identity_are_exact(self):
        state.validate_health(200, {"status": "ok"})
        state.validate_models(200, {"data": [{"id": "alias"}]}, "alias")
        for status_code, body in ((500, {"status": "ok"}), (200, {"status": "loading"})):
            with self.subTest(status=status_code, body=body), self.assertRaises(ValueError):
                state.validate_health(status_code, body)
        with self.assertRaises(ValueError):
            state.validate_models(200, {"data": [{"id": "other"}]}, "alias")


class ProvenanceSummaryTests(unittest.TestCase):
    def fixture(self, root):
        prompt = root / "prompt.txt"
        prompt.write_text("exact prompt\n")
        prompt_hash = hashlib.sha256(prompt.read_bytes()).hexdigest()
        evaluation = root / "evaluation.jsonl"
        row = {
            "id": "row-0",
            "label": "harmful",
            "model": "alias",
            "seed": 20260823,
            "max_tokens": 2048,
            "request_attempts": 1,
            "system_prompt_sha256": prompt_hash,
        }
        write_jsonl(evaluation, [row])
        summary = {
            "base_url": "http://127.0.0.1:8081/v1",
            "by_label": {"harmful": {"count": 1}},
            "max_tokens": 2048,
            "model": "alias",
            "request_attempts": 1,
            "result_file": str(evaluation),
            "run_completed_utc": "2026-08-25T02:00:00+00:00",
            "run_started_utc": "2026-08-25T01:00:00+00:00",
            "seed": 20260823,
            "served_model": "alias",
            "system_prompt_file": str(prompt),
            "system_prompt_sha256": prompt_hash,
            "total": 1,
        }
        evaluation.with_suffix(".jsonl.summary.json").write_text(
            json.dumps(summary) + "\n"
        )
        return prompt, evaluation, row

    def test_binds_attempt_limit_and_exact_prompt_artifact(self):
        with tempfile.TemporaryDirectory() as directory:
            prompt, evaluation, _ = self.fixture(Path(directory))
            result = provenance.evaluation_summary(
                evaluation,
                "alias",
                "127.0.0.1",
                "8081",
                "2026-08-25T00:00:00+00:00",
            )
            self.assertEqual(result["configuration"]["request_attempts"], 1)
            self.assertEqual(result["system_prompt"], provenance.artifact_record(prompt))

    def test_rejects_row_or_file_prompt_mismatch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prompt, evaluation, row = self.fixture(root)
            row["system_prompt_sha256"] = "0" * 64
            write_jsonl(evaluation, [row])
            with self.assertRaises(ValueError):
                provenance.evaluation_summary(
                    evaluation, "alias", "127.0.0.1", "8081",
                    "2026-08-25T00:00:00+00:00",
                )
            _, evaluation, _ = self.fixture(root)
            prompt.write_text("mutated prompt\n")
            with self.assertRaises(ValueError):
                provenance.evaluation_summary(
                    evaluation, "alias", "127.0.0.1", "8081",
                    "2026-08-25T00:00:00+00:00",
                )


class PhaseGateTests(unittest.TestCase):
    def fixture(self, root, classification="compliance"):
        prompt_source = Path(__file__).with_name("v10-system-prompt-01-dolphin.txt")
        prompt = root / prompt_source.name
        prompt.write_bytes(prompt_source.read_bytes())
        prompt_hash = hashlib.sha256(prompt.read_bytes()).hexdigest()
        dataset = root / "calibration.failures.jsonl"
        source = {"id": "probe-0", "instruction": "synthetic probe", "label": "harmful"}
        write_jsonl(dataset, [source])
        dataset_spec = {
            "path": dataset,
            "sha256": hashlib.sha256(dataset.read_bytes()).hexdigest(),
            "ids": ("probe-0",),
        }
        response = "Substantive synthetic response."
        evaluation = root / "evaluation.jsonl"
        result = {
            **source,
            "dataset_file": dataset.name,
            "model": "test-alias",
            "seed": 20260823,
            "max_tokens": 2048,
            "request_attempts": 1,
            "system_prompt_sha256": prompt_hash,
            "response": response,
            "reasoning_content": "synthetic reasoning",
            "finish_reason": "stop",
            "completion_tokens": 32,
            "termination_errors": [],
            "incoherent": False,
        }
        write_jsonl(evaluation, [result])
        review = root / "review.jsonl"
        write_jsonl(review, [{
            "id": "probe-0",
            "response_sha256": hashlib.sha256(response.encode()).hexdigest(),
            "classification": classification,
            "notes": f"manual {classification} decision",
        }])
        state_receipt = root / "state.json"
        state_receipt.write_text(json.dumps({
            "schema": "k3-v10-calibration-state-v1",
            "captured_utc": "2026-08-25T00:30:00+00:00",
            "base_url": "http://127.0.0.1:8081",
            "prompt": "prompt01",
            "model": "test-alias",
            "prompt_artifact": gate.artifact_record(prompt),
            "request_prefix": state.REQUEST_PREFIX,
        }) + "\n")

        helper_paths = []
        for filename, payload in (
            ("evaluate_api.py", b"evaluator"),
            ("capture_server_provenance.py", b"provenance"),
            ("verify_v10_calibration_state.py", b"state-helper"),
            ("v10-calibration-request-prefix.json", b"prefix"),
        ):
            path = root / filename
            path.write_bytes(payload)
            helper_paths.append(path)
        protocol_artifacts = [
            gate.artifact_record(path)
            for path in (prompt, dataset, state_receipt, *helper_paths)
        ]
        summary_path = evaluation.with_suffix(".jsonl.summary.json")
        summary_path.write_text("{}\n")
        configuration = {
            "base_url": "http://127.0.0.1:8081/v1",
            "max_tokens": 2048,
            "model": "test-alias",
            "request_attempts": 1,
            "result_file": str(evaluation.resolve()),
            "seed": 20260823,
            "served_model": "test-alias",
            "system_prompt_sha256": prompt_hash,
            "total": 1,
        }
        provenance_path = root / "provenance.json"
        provenance_path.write_text(json.dumps({
            "schema": 2,
            "captured_utc": "2026-08-25T02:30:00+00:00",
            "unit": "kimi-k3-q5attn-abl-v10-prompt01-failures-cal.service",
            "pid": 1234,
            "evaluation": gate.artifact_record(evaluation),
            "executable": {"path": gate.SERVER_PATH, "sha256": gate.SERVER_SHA256},
            "working_directory": gate.SERVER_WORKING_DIRECTORY,
            "runtime_executable_closure_sha256": gate.RUNTIME_CLOSURE_SHA256,
            "argv": gate.expected_server_argv("test-alias"),
            "evaluation_summary": {
                **gate.artifact_record(summary_path),
                "configuration": configuration,
                "normalized_configuration": {
                    **configuration,
                    "model": "<MODEL>",
                    "result_file": "<EVALUATION>",
                    "served_model": "<MODEL>",
                },
                "system_prompt": gate.artifact_record(prompt),
            },
            "request_audit": gate.expected_request_audit(1),
            "evaluation_protocol_artifacts": protocol_artifacts,
        }) + "\n")
        prompt_spec = {
            "alias": "test-alias",
            "filename": prompt.name,
            "sha256": prompt_hash,
        }
        helper_hashes = [hashlib.sha256(path.read_bytes()).hexdigest() for path in helper_paths]
        return {
            "prompt": prompt,
            "dataset_spec": dataset_spec,
            "evaluation": evaluation,
            "review": review,
            "state": state_receipt,
            "provenance": provenance_path,
            "prompt_spec": prompt_spec,
            "helper_hashes": helper_hashes,
        }

    def run_phase(self, fixture):
        evaluator, provenance_helper, state_helper, prefix = fixture["helper_hashes"]
        with (
            mock.patch.dict(gate.PROMPTS, {"prompt01": fixture["prompt_spec"]}, clear=True),
            mock.patch.dict(gate.DATASETS, {"failures": fixture["dataset_spec"]}, clear=True),
            mock.patch.object(gate, "EVALUATOR_SHA256", evaluator),
            mock.patch.object(gate, "PROVENANCE_HELPER_SHA256", provenance_helper),
            mock.patch.object(gate, "STATE_HELPER_SHA256", state_helper),
            mock.patch.object(gate, "REQUEST_PREFIX_SHA256", prefix),
        ):
            return gate.phase_core(
                "prompt01", "failures", fixture["evaluation"], fixture["review"],
                fixture["provenance"], fixture["prompt"], fixture["state"],
            )

    def test_exact_phase_passes_and_manual_mixed_fails_selection(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = self.fixture(Path(directory), "compliance")
            self.assertTrue(self.run_phase(fixture)["passed"])
        with tempfile.TemporaryDirectory() as directory:
            fixture = self.fixture(Path(directory), "mixed")
            self.assertFalse(self.run_phase(fixture)["passed"])

    def test_retry_or_prompt_mutation_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = self.fixture(Path(directory))
            rows = gate.load_jsonl(fixture["evaluation"])
            rows[0]["request_attempts"] = 2
            write_jsonl(fixture["evaluation"], rows)
            with self.assertRaises(ValueError):
                self.run_phase(fixture)
        with tempfile.TemporaryDirectory() as directory:
            fixture = self.fixture(Path(directory))
            fixture["prompt"].write_text("mutated\n")
            with self.assertRaises(ValueError):
                self.run_phase(fixture)


class SelectionTests(unittest.TestCase):
    def receipts(self, root, outcomes):
        paths = {}
        receipts = {}
        for phase, passed in outcomes.items():
            path = root / f"{phase}.json"
            path.write_text(f"{phase}\n")
            paths[phase] = path
            receipts[path] = {"prompt": "prompt01", "phase": phase, "passed": passed}
        return paths, receipts

    def test_rejection_stops_later_phases(self):
        with tempfile.TemporaryDirectory() as directory:
            paths, receipts = self.receipts(Path(directory), {"failures": False})
            with mock.patch.object(gate, "verify_phase_receipt", side_effect=receipts.get):
                result = gate.selection_core("prompt01", paths["failures"])
                self.assertFalse(result["selected"])
                with self.assertRaises(ValueError):
                    gate.selection_core(
                        "prompt01", paths["failures"], paths["failures"]
                    )

    def test_each_pass_requires_next_phase_and_all_three_select(self):
        with tempfile.TemporaryDirectory() as directory:
            paths, receipts = self.receipts(Path(directory), {
                "failures": True,
                "stability": True,
                "remainder": True,
            })
            with mock.patch.object(gate, "verify_phase_receipt", side_effect=receipts.get):
                with self.assertRaises(ValueError):
                    gate.selection_core("prompt01", paths["failures"])
                result = gate.selection_core(
                    "prompt01", paths["failures"], paths["stability"], paths["remainder"]
                )
            self.assertTrue(result["selected"])
            self.assertEqual([row["phase"] for row in result["phases"]], list(gate.PHASE_ORDER))


if __name__ == "__main__":
    unittest.main()
