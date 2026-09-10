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

    def test_direction_mode_normalization(self):
        self.assertEqual(YoloConfig(direction_mode="LONG_ONLY").normalized_direction_mode, "long_only")
        with self.assertRaises(ValueError):
            _ = YoloConfig(direction_mode="sometimes_short").normalized_direction_mode


if __name__ == "__main__":
    unittest.main()
