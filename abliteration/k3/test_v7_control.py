#!/usr/bin/env python3
"""Focused fail-closed tests for K3 v7 calibration and vector receipts."""

import unittest

import prepare_v7_calibration as calibration
import set_v7_control as control


class CalibrationTests(unittest.TestCase):
    def test_selected_rows_follow_locked_id_order(self):
        rows = [
            {"id": "second", "instruction": "b", "label": "harmful"},
            {"id": "first", "instruction": "a", "label": "harmful"},
        ]
        selected = calibration.select_rows(rows, ("first", "second"))
        self.assertEqual([row["id"] for row in selected], ["first", "second"])

    def test_missing_selected_row_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "missing selected ids"):
            calibration.select_rows([], ("absent",))

    def test_jsonl_encoding_is_stable(self):
        rows = [{"label": "harmful", "instruction": "x", "id": "one"}]
        self.assertEqual(
            calibration.encode_jsonl(rows),
            b'{"id": "one", "instruction": "x", "label": "harmful"}\n',
        )


class ControlReceiptTests(unittest.TestCase):
    def valid_row(self, scale=-0.25):
        return [{
            "id": 0,
            "path": str(control.VECTOR_PATH),
            "scale": scale,
            "layer_start": control.LAYER_START,
            "layer_end": control.LAYER_END,
            "applied": scale != 0.0,
        }]

    def test_only_preregistered_scales_are_allowed(self):
        for scale in control.ALLOWED_SCALES:
            self.assertTrue(control.scale_allowed(scale))
        self.assertFalse(control.scale_allowed(-0.3))
        self.assertFalse(control.scale_allowed(1.0))

    def test_loopback_is_required(self):
        self.assertEqual(
            control.verify_loopback("http://127.0.0.1:8081/"),
            "http://127.0.0.1:8081",
        )
        with self.assertRaisesRegex(ValueError, "loopback"):
            control.verify_loopback("https://example.com")
        with self.assertRaisesRegex(ValueError, "path"):
            control.verify_loopback("http://127.0.0.1:8081/v1")

    def test_exact_single_vector_state_passes(self):
        control.verify_single_vector(self.valid_row(), -0.25)
        control.verify_single_vector(self.valid_row(0.0), 0.0)

    def test_wrong_vector_or_range_fails_closed(self):
        wrong_path = self.valid_row()
        wrong_path[0]["path"] = "/tmp/other.gguf"
        with self.assertRaisesRegex(ValueError, "path"):
            control.verify_single_vector(wrong_path, -0.25)
        wrong_range = self.valid_row()
        wrong_range[0]["layer_end"] += 1
        with self.assertRaisesRegex(ValueError, "layer_end"):
            control.verify_single_vector(wrong_range, -0.25)

    def test_wrong_applied_flag_or_scale_fails_closed(self):
        wrong_applied = self.valid_row()
        wrong_applied[0]["applied"] = False
        with self.assertRaisesRegex(ValueError, "applied"):
            control.verify_single_vector(wrong_applied, -0.25)
        with self.assertRaisesRegex(ValueError, "scale"):
            control.verify_single_vector(self.valid_row(), -0.5)

    def test_model_identity_must_be_exact(self):
        control.verify_model({"data": [{"id": "v7"}]}, "v7")
        with self.assertRaisesRegex(ValueError, "expected exactly"):
            control.verify_model({"data": [{"id": "v1"}, {"id": "v7"}]}, "v7")


if __name__ == "__main__":
    unittest.main()
