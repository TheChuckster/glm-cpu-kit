#!/usr/bin/env python3
"""Regression tests for V10's exact system-prompt input contract."""

import hashlib
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from evaluate_api import build_payload, load_system_prompt


class SystemPromptTests(unittest.TestCase):
    def write(self, data):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "prompt.txt"
        path.write_bytes(data)
        return path

    def test_loads_exact_utf8_and_hashes_raw_file(self):
        raw = "Exact π prompt.\n".encode()
        text, digest = load_system_prompt(self.write(raw))
        self.assertEqual(text, "Exact π prompt.")
        self.assertEqual(digest, hashlib.sha256(raw).hexdigest())

    def test_rejects_empty(self):
        with self.assertRaisesRegex(ValueError, "empty"):
            load_system_prompt(self.write(b""))

    def test_rejects_missing_or_duplicate_terminal_lf(self):
        for raw in (b"prompt", b"prompt\n\n"):
            with self.subTest(raw=raw), self.assertRaisesRegex(ValueError, "terminal LF"):
                load_system_prompt(self.write(raw))

    def test_rejects_cr_nul_and_edge_whitespace(self):
        for raw in (b"prompt\r\n", b"pro\x00mpt\n", b" prompt\n", b"prompt \n"):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                load_system_prompt(self.write(raw))

    def test_rejects_invalid_utf8_and_oversize(self):
        for raw in (b"\xff\n", b"x" * (16 * 1024) + b"\n"):
            with self.subTest(length=len(raw)), self.assertRaises((UnicodeError, ValueError)):
                load_system_prompt(self.write(raw))

    def test_builds_exact_system_then_user_payload(self):
        args = SimpleNamespace(
            model="exact-model",
            seed=20260823,
            max_tokens=2048,
            system_prompt="Exact system contract.",
        )
        payload = build_payload(args, {"instruction": "Unchanged user text."}, 7)
        self.assertEqual(payload, {
            "model": "exact-model",
            "seed": 20260830,
            "temperature": 0,
            "max_tokens": 2048,
            "stream": False,
            "messages": [
                {"role": "system", "content": "Exact system contract."},
                {"role": "user", "content": "Unchanged user text."},
            ],
        })

    def test_builds_legacy_user_only_payload(self):
        args = SimpleNamespace(
            model="exact-model",
            seed=10,
            max_tokens=32,
            system_prompt=None,
        )
        payload = build_payload(args, {"instruction": "User only."}, 0)
        self.assertEqual(payload["messages"], [
            {"role": "user", "content": "User only."},
        ])


if __name__ == "__main__":
    unittest.main()
