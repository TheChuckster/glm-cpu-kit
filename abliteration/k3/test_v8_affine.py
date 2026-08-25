#!/usr/bin/env python3

import json
import io
import os
import sys
import tempfile
import unittest
from contextlib import ExitStack, redirect_stdout
from pathlib import Path
from unittest import mock

import numpy as np

import prepare_v8_affine as affine


class V8AffineTests(unittest.TestCase):
    width = 4
    samples = 10
    layer = 2

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.gguf_py = Path(__file__).resolve().parents[3] / "ik_llama.cpp-fork/gguf-py"
        if not (self.gguf_py / "gguf").is_dir():
            self.skipTest("sibling ik_llama.cpp-fork GGUF Python package is unavailable")

        negative = np.asarray([
            [-0.09, 0.02, 0.01, 0.00],
            [-0.07, 0.01, -0.02, 0.01],
            [-0.05, 0.00, 0.00, -0.01],
            [-0.03, -0.01, 0.02, 0.00],
            [-0.01, -0.02, -0.01, 0.01],
            [0.01, 0.02, 0.01, -0.01],
            [0.03, 0.01, -0.02, 0.00],
            [0.05, 0.00, 0.00, 0.01],
            [0.07, -0.01, 0.02, -0.01],
            [0.09, -0.02, -0.01, 0.00],
        ], dtype="<f4")
        positive = negative + np.asarray([2.0, 0.0, 0.0, 0.0], dtype="<f4")
        self.v2 = self.root / "v2.gguf"
        self.q5 = self.root / "q5.gguf"
        self._write_capture(self.v2, positive, negative)
        self._write_capture(self.q5, positive, negative)
        self.v2_hash = affine.sha256(self.v2)
        self.q5_hash = affine.sha256(self.q5)

    def tearDown(self):
        self.temp.cleanup()

    def _write_capture(self, path, positive, negative):
        sys.path.insert(0, str(self.gguf_py))
        from gguf import GGUFWriter
        writer = GGUFWriter(path, "activationcapture")
        writer.add_string("activationcapture.model_hint", "kimi-k3")
        writer.add_string(
            "activationcapture.method", "final-templated-prompt-position")
        writer.add_string("activationcapture.layer_spec", str(self.layer))
        writer.add_uint32("activationcapture.sample_count", self.samples)
        writer.add_uint32("activationcapture.tensor_count", 2)
        writer.add_tensor(f"positive.{self.layer}", positive)
        writer.add_tensor(f"negative.{self.layer}", negative)
        writer.write_header_to_file()
        writer.write_kv_data_to_file()
        writer.write_tensors_to_file()
        writer.close()
        os.chmod(path, 0o600)

    def _patch_constants(self):
        stack = ExitStack()
        stack.enter_context(mock.patch.object(affine, "WIDTH", self.width))
        stack.enter_context(mock.patch.object(affine, "SAMPLES", self.samples))
        stack.enter_context(mock.patch.object(affine, "LAYER", self.layer))
        stack.enter_context(mock.patch.object(affine, "BOOTSTRAP_SAMPLES", 8))
        stack.enter_context(mock.patch.object(
            affine, "BOOTSTRAP_SEEDS", (20260827, 20260828)))
        stack.enter_context(mock.patch.object(
            affine, "EXPECTED_Q5_SHA256", self.q5_hash))
        return stack

    def _run(self, output, expected_hash=None):
        argv = [
            "prepare_v8_affine.py", str(self.v2), str(self.q5), str(output),
            "--expected-v2-sha256", expected_hash or self.v2_hash,
            "--gguf-py", str(self.gguf_py),
        ]
        with self._patch_constants(), mock.patch.object(sys, "argv", argv):
            with redirect_stdout(io.StringIO()):
                affine.main()

    def _load_vector(self, path):
        _, tensors, data_start = affine.gguf_index(path)
        tensor = tensors[f"direction.{self.layer}"]
        return np.asarray(np.memmap(
            path, mode="r", dtype="<f4",
            offset=data_start + tensor["offset"], shape=(self.width,))).copy()

    def test_writes_exact_projection_and_offsets(self):
        output = self.root / "out"
        self._run(output)
        projection = self._load_vector(output / "projection.gguf")
        offset_zero = self._load_vector(output / "offset-alpha0.gguf")
        offset_negative = self._load_vector(output / "offset-alpha-m0p5.gguf")
        np.testing.assert_allclose(projection, [1.0, 0.0, 0.0, 0.0], atol=1e-7)
        np.testing.assert_allclose(offset_zero, [0.0, 0.0, 0.0, 0.0], atol=1e-7)
        np.testing.assert_allclose(offset_negative, [-1.0, 0.0, 0.0, 0.0], atol=1e-7)
        manifest = json.loads((output / "manifest.json").read_text())
        self.assertEqual(manifest["alphas"], [0.0, -0.5])
        self.assertEqual(len(manifest["geometry"]["bootstrap"]), 2)
        self.assertEqual(oct(output.stat().st_mode & 0o777), "0o700")
        for path in output.iterdir():
            self.assertEqual(oct(path.stat().st_mode & 0o777), "0o600")

    def test_rejects_wrong_v2_hash_before_output(self):
        output = self.root / "wrong-hash"
        with self.assertRaisesRegex(ValueError, "activation hash mismatch"):
            self._run(output, "0" * 64)
        self.assertFalse(output.exists())

    def test_rejects_public_capture(self):
        output = self.root / "public"
        os.chmod(self.v2, 0o644)
        with self.assertRaisesRegex(ValueError, "not private"):
            self._run(output)
        self.assertFalse(output.exists())

    def test_rejects_reused_output_path(self):
        output = self.root / "existing"
        output.mkdir()
        with self.assertRaisesRegex(ValueError, "refusing to reuse"):
            self._run(output)


if __name__ == "__main__":
    unittest.main()
