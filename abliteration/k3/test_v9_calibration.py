#!/usr/bin/env python3
"""Focused fail-closed tests for the K3 V9 calibration controls."""

import contextlib
import hashlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import capture_server_provenance as provenance
import verify_v9_calibration_state as state


class StateValidationTests(unittest.TestCase):
    def setUp(self):
        self.artifact = state.ARTIFACTS["alpha0"]

    def test_exact_state(self):
        state.validate_state(200, state.expected_state(self.artifact), self.artifact)

    def test_state_rejects_wrong_layer_rank_alpha_or_extra_key(self):
        for key, value in (("layer", 60), ("rank", 6), ("alpha", -0.5)):
            observed = state.expected_state(self.artifact)
            observed[0][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                state.validate_state(200, observed, self.artifact)
        observed = state.expected_state(self.artifact)
        observed[0]["scale"] = 1.0
        with self.assertRaises(ValueError):
            state.validate_state(200, observed, self.artifact)

    def test_hot_mutation_requires_exact_409(self):
        expected = {"success": False, "error": state.HOT_ERROR}
        state.validate_hot_response("load", 409, expected)
        for status, body in ((200, expected), (409, {"success": False}), (500, None)):
            with self.subTest(status=status, body=body), self.assertRaises(ValueError):
                state.validate_hot_response("load", status, body)

    def test_base_url_is_exact_loopback(self):
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

    def test_request_prefix_matches_sealed_json(self):
        path = Path(__file__).with_name("v9-calibration-request-prefix.json")
        self.assertEqual(json.loads(path.read_text()), state.REQUEST_PREFIX)

    def test_main_writes_receipt_only_after_exact_sequence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifact_path = root / "affine.gguf"
            artifact_path.write_bytes(b"sealed-affine")
            api_key_path = root / "api-key"
            api_key_path.write_text("secret\n")
            output = root / "receipt.json"
            artifact = {
                "alias": "test-alias",
                "alpha": 0.0,
                "bytes": artifact_path.stat().st_size,
                "path": artifact_path,
                "sha256": hashlib.sha256(artifact_path.read_bytes()).hexdigest(),
            }
            responses = [
                (200, {"status": "ok"}),
                (200, {"data": [{"id": "test-alias"}]}),
                (200, state.expected_state(artifact)),
                (409, {"success": False, "error": state.HOT_ERROR}),
                (409, {"success": False, "error": state.HOT_ERROR}),
                (409, {"success": False, "error": state.HOT_ERROR}),
            ]
            argv = [
                "verify_v9_calibration_state.py", "test",
                "--api-key-file", str(api_key_path),
                "--output", str(output),
            ]
            with (
                mock.patch.dict(state.ARTIFACTS, {"test": artifact}, clear=True),
                mock.patch.object(state, "request_json", side_effect=responses) as request,
                mock.patch.object(sys, "argv", argv),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                state.main()
            receipt = json.loads(output.read_text())
            self.assertEqual(receipt["request_prefix"], state.REQUEST_PREFIX)
            self.assertEqual(receipt["state"], state.expected_state(artifact))
            self.assertEqual(request.call_count, 6)


class ProvenancePrefixTests(unittest.TestCase):
    @staticmethod
    def journal(sequence):
        return "\n".join(
            f'log status={row["status"]} method="{row["method"]}" path="{row["path"]}"'
            for row in sequence
        )

    def test_request_audit_accepts_exact_prefix(self):
        evaluator = [
            {"status": 200, "method": "GET", "path": "/v1/models"},
            {"status": 200, "method": "POST", "path": "/v1/chat/completions"},
            {"status": 200, "method": "GET", "path": "/v1/models"},
        ]
        completed = mock.Mock(stdout=self.journal(state.REQUEST_PREFIX + evaluator))
        with mock.patch.object(provenance.subprocess, "run", return_value=completed):
            result = provenance.request_audit("unit", 1, prefix=state.REQUEST_PREFIX)
        self.assertEqual(result["prefix_requests"], 6)
        self.assertEqual(result["chat_completion_requests"], 1)

    def test_request_audit_preserves_legacy_no_prefix_sequence(self):
        evaluator = [
            {"status": 200, "method": "GET", "path": "/v1/models"},
            {"status": 200, "method": "POST", "path": "/v1/chat/completions"},
            {"status": 200, "method": "GET", "path": "/v1/models"},
        ]
        completed = mock.Mock(stdout=self.journal(evaluator))
        with mock.patch.object(provenance.subprocess, "run", return_value=completed):
            result = provenance.request_audit("unit", 1)
        self.assertEqual(result["prefix_requests"], 0)

    def test_request_audit_rejects_extra_or_reordered_request(self):
        evaluator = [
            {"status": 200, "method": "GET", "path": "/v1/models"},
            {"status": 200, "method": "POST", "path": "/v1/chat/completions"},
            {"status": 200, "method": "GET", "path": "/v1/models"},
        ]
        bad = list(state.REQUEST_PREFIX)
        bad[0], bad[1] = bad[1], bad[0]
        completed = mock.Mock(stdout=self.journal(bad + evaluator))
        with mock.patch.object(provenance.subprocess, "run", return_value=completed):
            with self.assertRaises(ValueError):
                provenance.request_audit("unit", 1, prefix=state.REQUEST_PREFIX)

    def test_write_exclusive_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "receipt.json"
            state.write_exclusive(output, {"first": True})
            with self.assertRaises(ValueError):
                state.write_exclusive(output, {"second": True})


if __name__ == "__main__":
    unittest.main()
