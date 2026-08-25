#!/usr/bin/env python3

import unittest

import numpy as np

import diagnose_v5_spectral as spectral
import generate_v5_spectral_directions as directions


class SpectralDiagnosticTests(unittest.TestCase):
    def test_subspace_is_orthonormal_and_retains_signal(self):
        rng = np.random.default_rng(7)
        latent = rng.normal(size=(120, 7))
        decoder, _ = np.linalg.qr(rng.normal(size=(32, 7)))
        matrix = np.asarray(latent @ decoder.T, dtype=np.float32)
        basis, geometry = spectral.spectral_subspace(matrix)
        self.assertLess(geometry["orthogonality_max_error"], 2e-5)
        projection = basis.T @ basis
        self.assertLess(np.linalg.norm(matrix - matrix @ projection), 1e-4)

    def test_variants_have_locked_shapes(self):
        harmful = np.ones((10, 8), dtype=np.float32)
        harmless = np.zeros((10, 8), dtype=np.float32)
        self.assertEqual(
            spectral.contrast_matrix(
                harmful, harmless, "harmful-residual-raw").shape, (10, 8))
        self.assertEqual(
            spectral.contrast_matrix(
                harmful, harmless, "symmetric-contrast-unit").shape, (20, 8))
        norms = np.linalg.norm(spectral.contrast_matrix(
            harmful, harmless, "harmful-residual-unit"), axis=1)
        np.testing.assert_allclose(norms, 1.0, atol=1e-6)

    def test_principal_cosines_are_sign_free(self):
        left = np.eye(7, 10)
        right = left.copy()
        right[3] *= -1
        np.testing.assert_allclose(
            spectral.principal_cosines(left, right), np.ones(7), atol=1e-12)

    def test_basis_orientation_is_deterministic(self):
        basis = np.eye(7, 10)
        basis[2] *= -1
        oriented = directions.orient_basis(basis)
        self.assertGreater(oriented[2, 2], 0)
        np.testing.assert_allclose(oriented @ oriented.T, np.eye(7), atol=1e-12)


if __name__ == "__main__":
    unittest.main()
