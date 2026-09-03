import unittest

from crypto_yolo.providers import HyperliquidReadOnlyClient, RobotWealthClient


class ProviderParsingTests(unittest.TestCase):
    def test_rw_weights_and_vols_join(self):
        weights = {
            "data": [
                {
                    "ticker": "BTCUSDT",
                    "arrival_price": "100000",
                    "carry_megafactor": "0.3",
                    "combo_weight": "0.2",
                    "date": "2026-09-03",
                    "momentum_megafactor": "0.1",
                    "trend_megafactor": "0.2",
                }
            ]
        }
        vols = {"data": [{"ticker": "BTCUSDT", "ewvol": "0.55"}]}
        signal = RobotWealthClient.parse_signals(weights, vols)[0]
        self.assertEqual(signal.ticker, "BTC")
        self.assertEqual(signal.date, "2026-09-03")
        self.assertAlmostEqual(signal.ewvol, 0.55)
        self.assertAlmostEqual(signal.carry, 0.3)

    def test_hyperliquid_read_only_payload_parsing(self):
        meta = [
            {"universe": [{"name": "BTC", "szDecimals": 5}]},
            [{"markPx": "101000"}],
        ]
        markets = HyperliquidReadOnlyClient._parse_markets(meta)
        state = {
            "assetPositions": [
                {"position": {"coin": "BTC", "szi": "0.02", "positionValue": "2020"}}
            ]
        }
        positions = HyperliquidReadOnlyClient._parse_positions(state, markets)
        self.assertAlmostEqual(markets["BTC"].mark_price, 101000)
        self.assertAlmostEqual(positions["BTC"].quantity, 0.02)
        self.assertAlmostEqual(positions["BTC"].price, 101000)


if __name__ == "__main__":
    unittest.main()
