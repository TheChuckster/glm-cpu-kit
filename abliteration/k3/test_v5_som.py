#!/usr/bin/env python3
"""Focused numerical tests for the K3 v5 SOM adaptation."""

import math
import unittest

import numpy as np

import generate_v5_directions as v5


class V5SomTests(unittest.TestCase):
    def test_fisher_score(self):
        harmful = np.array([[2.0, 0.0], [4.0, 0.0]], dtype=np.float32)
        harmless = np.array([[0.0, 0.0], [0.0, 2.0]], dtype=np.float32)
        result = v5.fisher_score(harmful, harmless)
        self.assertAlmostEqual(result["centroid_distance"], math.sqrt(10.0))
        self.assertAlmostEqual(result["pooled_within_rms"], 1.0)
        self.assertAlmostEqual(result["fisher_score"], math.sqrt(10.0))

    def test_supported_pivots_are_deterministic_and_full_rank(self):
        rng = np.random.default_rng(12)
        directions = rng.normal(size=(16, 32))
        directions[:, 0] += 4.0
        counts = np.array([40, 35, 30, 25, 20, 18, 16, 14,
                           12, 10, 8, 7, 6, 5, 4, 3])
        first = v5.select_supported_pivots(directions, counts)
        second = v5.select_supported_pivots(directions, counts)
        self.assertEqual(first, second)
        self.assertEqual(len(first), 7)
        self.assertEqual(len(set(first)), 7)
        geometry = v5.direction_geometry(v5.normalized_rows(directions)[first])
        self.assertGreater(geometry["minimum_singular_value"], v5.MIN_SINGULAR_VALUE)

    def test_principal_cosines_are_rotation_invariant(self):
        left = np.eye(3, 8)
        rotation = np.array([
            [0.0, 1.0, 0.0],
            [1.0, 0.0, 0.0],
            [0.0, 0.0, -1.0],
        ])
        right = rotation @ left
        self.assertTrue(np.allclose(v5.principal_cosines(left, right), 1.0))

    def test_small_som_is_reproducible(self):
        old = (v5.WIDTH, v5.SOM_ITERATIONS)
        try:
            v5.WIDTH = 12
            v5.SOM_ITERATIONS = 80
            rng = np.random.default_rng(22)
            harmful = rng.normal(loc=1.0, size=(96, 12)).astype(np.float32)
            harmless = rng.normal(loc=-1.0, size=(96, 12)).astype(np.float32)
            first_directions, first_counts = v5.train_som_directions(harmful, harmless)
            second_directions, second_counts = v5.train_som_directions(harmful, harmless)
            self.assertTrue(np.array_equal(first_counts, second_counts))
            self.assertTrue(np.array_equal(first_directions, second_directions))
            self.assertEqual(int(np.sum(first_counts)), len(harmful))
        finally:
            v5.WIDTH, v5.SOM_ITERATIONS = old


if __name__ == "__main__":
    unittest.main()
