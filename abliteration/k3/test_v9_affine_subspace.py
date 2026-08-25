#!/usr/bin/env python3

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import ExitStack, redirect_stdout
from pathlib import Path
from unittest import mock

import numpy as np

import prepare_v9_affine_subspace as affine


class V9AffineSubspaceTests(unittest.TestCase):
    width = 4
    samples = 10
    layer = 2
    rank = 2

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.gguf_py = Path(__file__).resolve().parents[3] / "ik_llama.cpp-fork/gguf-py"
        if not (self.gguf_py / "gguf").is_dir():
            self.skipTest("sibling ik_llama.cpp-fork GGUF Python package is unavailable")

        noise = np.asarray([
            [-0.09, 0.02], [-0.07, 0.01], [-0.05, 0.00], [-0.03, -0.01],
            [-0.01, -0.02], [0.01, 0.02], [0.03, 0.01], [0.05, 0.00],
            [0.07, -0.01], [0.09, -0.02],
        ], dtype="<f4")
        negative = np.column_stack((
            np.full(self.samples, 0.25, dtype="<f4"),
            np.full(self.samples, -0.5, dtype="<f4"), noise))
        positive = negative + np.asarray([2.0, 1.0, 0.0, 0.0], dtype="<f4")
        self.capture = self.root / "q5-activations.gguf"
        self.basis = self.root / "q5.gguf"
        self.basis_manifest = self.root / "q5.manifest.json"
        self._write_capture(self.capture, positive, negative)
        self._write_basis(self.basis, np.asarray([
            [1.0, 0.0, 0.0, 0.0],
            [0.0, 1.0, 0.0, 0.0],
        ], dtype="<f4"))
        self._write_basis_manifest()

    def tearDown(self):
        self.temp.cleanup()

    def _writer(self, path, architecture):
        sys.path.insert(0, str(self.gguf_py))
        from gguf import GGUFWriter
        return GGUFWriter(path, architecture)

    def _write_capture(self, path, positive, negative):
        writer = self._writer(path, "activationcapture")
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

    def _write_basis(self, path, basis):
        if path.exists():
            path.unlink()
        writer = self._writer(path, "controlvector")
        writer.add_string("controlvector.model_hint", "kimi-k3")
        writer.add_uint32("controlvector.layer_count", len(basis))
        for index, row in enumerate(basis, 1):
            writer.add_tensor(f"direction.{index}", np.asarray(row, dtype="<f4"))
        writer.write_header_to_file()
        writer.write_kv_data_to_file()
        writer.write_tensors_to_file()
        writer.close()
        os.chmod(path, 0o600)

    def _basis_payload(self):
        _, tensors, data_start = affine.gguf_index(self.basis)
        return np.asarray([
            np.asarray(np.memmap(
                self.basis, mode="r", dtype="<f4",
                offset=data_start + tensors[f"direction.{index}"]["offset"],
                shape=(self.width,))).copy()
            for index in range(1, len(tensors) + 1)
        ], dtype="<f4")

    def _write_basis_manifest(self, **overrides):
        basis = self._basis_payload()
        manifest = {
            "method_version": affine.SOURCE_METHOD_VERSION,
            "role": "q5-diagnostic",
            "q5_activation_sha256": affine.sha256(self.capture),
            "selected_layer_locked_from_source": self.layer,
            "selected_variant_locked_from_source": affine.SOURCE_VARIANT,
            "matrix_shape": [2 * self.samples, self.width],
            "rank": self.rank,
            "basis_payload_sha256": affine.payload_sha256(basis),
            "class_mean_retention": 1.0,
            "principal_cosines_to_source": [1.0] * self.rank,
            "artifact_sha256": {"q5.gguf": affine.sha256(self.basis)},
        }
        manifest.update(overrides)
        self.basis_manifest.write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n")
        os.chmod(self.basis_manifest, 0o600)

    def _patch_constants(self, expected_capture_hash=None):
        stack = ExitStack()
        stack.enter_context(mock.patch.object(affine, "WIDTH", self.width))
        stack.enter_context(mock.patch.object(affine, "SAMPLES", self.samples))
        stack.enter_context(mock.patch.object(affine, "LAYER", self.layer))
        stack.enter_context(mock.patch.object(affine, "RANK", self.rank))
        stack.enter_context(mock.patch.object(
            affine, "EXPECTED_CAPTURE_SHA256",
            expected_capture_hash or affine.sha256(self.capture)))
        stack.enter_context(mock.patch.object(
            affine, "EXPECTED_BASIS_SHA256", affine.sha256(self.basis)))
        stack.enter_context(mock.patch.object(
            affine, "EXPECTED_BASIS_MANIFEST_SHA256",
            affine.sha256(self.basis_manifest)))
        return stack

    def _run(self, output, expected_capture_hash=None):
        argv = [
            "prepare_v9_affine_subspace.py", str(self.capture), str(self.basis),
            str(self.basis_manifest), str(output), "--gguf-py", str(self.gguf_py),
        ]
        with self._patch_constants(expected_capture_hash), mock.patch.object(
                sys, "argv", argv), redirect_stdout(io.StringIO()):
            affine.main()

    def _load_artifact(self, path):
        metadata, tensors, data_start = affine.gguf_index(path)
        basis = np.asarray(np.memmap(
            path, mode="r", dtype="<f4",
            offset=data_start + tensors[f"basis.{self.layer}"]["offset"],
            shape=(self.rank, self.width))).copy()
        offset = np.asarray(np.memmap(
            path, mode="r", dtype="<f4",
            offset=data_start + tensors[f"offset.{self.layer}"]["offset"],
            shape=(self.width,))).copy()
        return metadata, basis, offset

    def test_writes_exact_basis_and_offsets(self):
        output = self.root / "out"
        self._run(output)
        zero_meta, zero_basis, zero_offset = self._load_artifact(
            output / "affine-alpha0.gguf")
        negative_meta, negative_basis, negative_offset = self._load_artifact(
            output / "affine-alpha-m0p5.gguf")
        expected_basis = np.asarray([
            [1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0]], dtype="<f4")
        np.testing.assert_array_equal(zero_basis, expected_basis)
        np.testing.assert_array_equal(negative_basis, expected_basis)
        np.testing.assert_allclose(zero_offset, [0.25, -0.5, 0.0, 0.0], atol=1e-7)
        np.testing.assert_allclose(
            negative_offset, [-0.75, -1.0, 0.0, 0.0], atol=1e-7)
        self.assertEqual(zero_meta["controlvectorsubspace.alpha"], 0.0)
        self.assertEqual(negative_meta["controlvectorsubspace.alpha"], -0.5)
        manifest = json.loads((output / "manifest.json").read_text())
        self.assertEqual(manifest["alphas_in_locked_order"], [0.0, -0.5])
        self.assertEqual(manifest["rank"], self.rank)
        self.assertEqual(oct(output.stat().st_mode & 0o777), "0o700")
        for path in output.iterdir():
            self.assertEqual(oct(path.stat().st_mode & 0o777), "0o600")

    def test_reproducible_outputs_are_byte_identical(self):
        first = self.root / "first"
        second = self.root / "second"
        self._run(first)
        self._run(second)
        self.assertEqual(
            {path.name for path in first.iterdir()},
            {path.name for path in second.iterdir()})
        for path in first.iterdir():
            self.assertEqual(path.read_bytes(), (second / path.name).read_bytes())

    def test_rejects_wrong_capture_hash_before_output(self):
        output = self.root / "wrong-hash"
        with self.assertRaisesRegex(ValueError, "activation hash mismatch"):
            self._run(output, "0" * 64)
        self.assertFalse(output.exists())

    def test_rejects_public_inputs(self):
        for index, path in enumerate(
                (self.capture, self.basis, self.basis_manifest)):
            with self.subTest(path=path.name):
                os.chmod(path, 0o644)
                with self.assertRaisesRegex(ValueError, "not private"):
                    self._run(self.root / f"public-{index}")
                os.chmod(path, 0o600)

    def test_rejects_reused_output_path(self):
        output = self.root / "existing"
        output.mkdir()
        with self.assertRaisesRegex(ValueError, "refusing to reuse"):
            self._run(output)

    def test_rejects_nonorthogonal_basis(self):
        self._write_basis(self.basis, np.asarray([
            [1.0, 0.0, 0.0, 0.0], [1.0, 0.0, 0.0, 0.0]], dtype="<f4"))
        self._write_basis_manifest()
        with self.assertRaisesRegex(ValueError, "Gram error"):
            self._run(self.root / "nonorthogonal")

    def test_rejects_nonunit_basis(self):
        self._write_basis(self.basis, np.asarray([
            [2.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0]], dtype="<f4"))
        self._write_basis_manifest()
        with self.assertRaisesRegex(ValueError, "row norm error"):
            self._run(self.root / "nonunit")

    def test_rejects_nonfinite_basis(self):
        self._write_basis(self.basis, np.asarray([
            [np.nan, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0]], dtype="<f4"))
        self._write_basis_manifest()
        with self.assertRaisesRegex(ValueError, "non-finite basis row"):
            self._run(self.root / "nonfinite")

    def test_rejects_wrong_basis_rank(self):
        self._write_basis(self.basis, np.asarray([
            [1.0, 0.0, 0.0, 0.0]], dtype="<f4"))
        self._write_basis_manifest()
        with self.assertRaisesRegex(ValueError, "basis metadata/tensor inventory"):
            self._run(self.root / "wrong-rank")

    def test_rejects_malformed_basis_manifest(self):
        self._write_basis_manifest(class_mean_retention=0.5)
        with self.assertRaisesRegex(ValueError, "basis manifest binding"):
            self._run(self.root / "bad-manifest")


if __name__ == "__main__":
    unittest.main()
