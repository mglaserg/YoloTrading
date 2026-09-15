from unittest.mock import patch
import unittest

from crypto_yolo.config import YoloConfig


class ConfigModeTests(unittest.TestCase):
    def test_explicit_network_and_mode_take_precedence_over_legacy_flags(self):
        env = {
            "YOLO_NETWORK": "mainnet",
            "YOLO_EXECUTION_MODE": "plan",
            "HYPERLIQUID_TESTNET": "true",
            "DRY_RUN": "false",
        }
        with patch.dict("os.environ", env, clear=True):
            cfg = YoloConfig.from_env()
        self.assertEqual(cfg.normalized_network, "mainnet")
        self.assertEqual(cfg.normalized_execution_mode, "plan")
        self.assertIn("api.hyperliquid.xyz", cfg.hyperliquid_api_url)

    def test_deadman_required_defaults_true_and_can_be_disabled(self):
        with patch.dict("os.environ", {}, clear=True):
            self.assertTrue(YoloConfig.from_env().deadman_required)
        with patch.dict("os.environ", {"YOLO_DEADMAN_REQUIRED": "false"}, clear=True):
            self.assertFalse(YoloConfig.from_env().deadman_required)

    def test_direction_mode_normalization(self):
        self.assertEqual(YoloConfig(direction_mode="LONG_ONLY").normalized_direction_mode, "long_only")
        with self.assertRaises(ValueError):
            _ = YoloConfig(direction_mode="sometimes_short").normalized_direction_mode

    def test_buffer_basis_defaults_to_rw_absolute_weight_and_supports_legacy(self):
        with patch.dict("os.environ", {}, clear=True):
            cfg = YoloConfig.from_env()
        self.assertEqual(cfg.normalized_buffer_basis, "absolute_weight")
        self.assertEqual(
            YoloConfig(buffer_basis="RELATIVE_TARGET").normalized_buffer_basis,
            "relative_target",
        )
        with self.assertRaises(ValueError):
            _ = YoloConfig(buffer_basis="mystery").normalized_buffer_basis


if __name__ == "__main__":
    unittest.main()
