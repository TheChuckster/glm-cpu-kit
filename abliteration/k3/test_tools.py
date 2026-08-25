#!/usr/bin/env python3
"""Focused regression tests for K3 subspace and patch-log gates."""

import math
import tempfile
import unittest
from pathlib import Path

import numpy as np

from compare_subspaces import jacobi_eigen_symmetric, principal_cosines
from recover_v4_donor import top_left_direction
from verify_model import check_quant_log, expected_targets


class SubspaceTests(unittest.TestCase):
    def test_rotated_eigenpairs(self):
        values, vectors = jacobi_eigen_symmetric([[1.64, 0.48], [0.48, 1.36]])
        self.assertAlmostEqual(values[0], 2.0, places=12)
        self.assertAlmostEqual(values[1], 1.0, places=12)
        first = [vectors[row][0] for row in range(2)]
        self.assertAlmostEqual(abs(first[0]), 0.8, places=12)
        self.assertAlmostEqual(abs(first[1]), 0.6, places=12)

    def test_principal_cosines(self):
        angle = 0.3
        left = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]
        right = [
            [math.cos(angle), 0.0, math.sin(angle)],
            [0.0, 1.0, 0.0],
        ]
        cosines = principal_cosines(left, right)
        self.assertAlmostEqual(cosines[0], 1.0, places=12)
        self.assertAlmostEqual(cosines[1], math.cos(angle), places=12)


class PatchLogTests(unittest.TestCase):
    def make_log(self, omit=None, duplicate=None, scale=1.0,
                 actual_component=None):
        lines = [
            "orthogonalization preflight matched 279 tensors; selected-F32=0; "
            f"basis-rank=10; scale {scale:.4f}; quant-passes 16; patch-existing=yes; "
            "input files remain read-only",
        ]
        for name in sorted(expected_targets()):
            if name == omit:
                continue
            actual = ""
            if actual_component is not None:
                actual = f"actual-source-component={actual_component:.6f}% "
            lines.append(
                f"orthogonalize: {name} post-quant-residual=1.000000% "
                f"{actual}absolute-component=0.100000%")
            lines.append(
                f"orthogonalize: {name} patched-existing shard=0 offset=32 bytes=64")
            if name == duplicate:
                lines.append(
                    f"orthogonalize: {name} patched-existing shard=0 offset=32 bytes=64")
        return "\n".join(lines) + "\n"

    def check(self, text, expected_scale=1.0):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "quantize.log"
            path.write_text(text)
            return check_quant_log(
                path, expected_targets(), 0.02,
                expected_basis_rank=10, require_patch_existing=True,
                expected_scale=expected_scale)

    def test_complete_patch_log(self):
        observed, actual, _, worst, patched = self.check(self.make_log())
        self.assertEqual(len(observed), 279)
        self.assertEqual(actual, {})
        self.assertEqual(len(patched), 279)
        self.assertAlmostEqual(worst, 0.01)

    def test_complete_reflection_log(self):
        observed, actual, _, worst, patched = self.check(
            self.make_log(scale=2.0, actual_component=100.0), expected_scale=2.0)
        self.assertEqual(len(observed), 279)
        self.assertEqual(len(actual), 279)
        self.assertEqual(len(patched), 279)
        self.assertAlmostEqual(worst, 0.01)

    def test_mislabeled_scale_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "quantization scale mismatch"):
            self.check(self.make_log(scale=1.0), expected_scale=2.0)

    def test_missing_reflection_component_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "actual source component set mismatch"):
            self.check(self.make_log(scale=2.0), expected_scale=2.0)

    def test_wrong_reflection_magnitude_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "differs from the expected"):
            self.check(
                self.make_log(scale=2.0, actual_component=90.0),
                expected_scale=2.0,
            )

    def test_missing_patch_is_rejected(self):
        name = next(iter(expected_targets()))
        with self.assertRaisesRegex(ValueError, "patch-existing write set mismatch"):
            self.check(self.make_log(omit=name))

    def test_duplicate_patch_is_rejected(self):
        name = next(iter(expected_targets()))
        with self.assertRaisesRegex(ValueError, "duplicate patch-existing writes"):
            self.check(self.make_log(duplicate=name))


class DonorRecoveryTests(unittest.TestCase):
    def test_recovers_dominant_left_direction(self):
        left = np.array([1.0, -2.0, 0.5, 3.0], dtype=np.float32)
        left /= np.linalg.norm(left)
        right = np.array([2.0, 1.0, -1.0], dtype=np.float32)
        right /= np.linalg.norm(right)
        nuisance_left = np.array([2.0, 1.0, 0.0, 0.0], dtype=np.float32)
        nuisance_left -= np.dot(nuisance_left, left) * left
        nuisance_left /= np.linalg.norm(nuisance_left)
        nuisance_right = np.array([1.0, -2.0, 0.0], dtype=np.float32)
        nuisance_right -= np.dot(nuisance_right, right) * right
        nuisance_right /= np.linalg.norm(nuisance_right)
        delta = (4.0 * np.outer(left, right)
                 + 0.2 * np.outer(nuisance_left, nuisance_right)).astype(np.float32)
        recovered, sigma, energy, residual = top_left_direction(delta)
        self.assertGreater(abs(float(np.dot(recovered, left))), 0.99999)
        self.assertAlmostEqual(sigma, 4.0, places=5)
        self.assertAlmostEqual(energy, 16.0 / 16.04, places=6)
        self.assertAlmostEqual(residual, math.sqrt(0.04 / 16.04), places=5)


if __name__ == "__main__":
    unittest.main()
