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


if __name__ == "__main__":
    unittest.main()
