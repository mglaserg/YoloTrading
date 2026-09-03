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

class HyperliquidBboTests(unittest.TestCase):
    def test_bbo_parses_two_sided_book(self):
        class Client(HyperliquidReadOnlyClient):
            def _post_info(self, body):
                return {"levels": [[{"px": "99.5", "sz": "1"}], [{"px": "100.5", "sz": "1"}]]}
        quote = Client("0xuser").fetch_bbo("BTC")
        self.assertEqual(quote.bid_price, 99.5)
        self.assertEqual(quote.ask_price, 100.5)

    def test_non_funding_ledger_request_shape(self):
        calls = []
        class Client(HyperliquidReadOnlyClient):
            def _post_info(self, body):
                calls.append(body)
                return []
        c = Client("0xuser")
        c.fetch_non_funding_ledger_updates(100, 200)
        self.assertEqual(calls[0]["type"], "userNonFundingLedgerUpdates")
        self.assertEqual(calls[0]["startTime"], 100)
        self.assertEqual(calls[0]["endTime"], 200)
        c.fetch_portfolio_history()
        self.assertEqual(calls[1]["type"], "portfolio")
