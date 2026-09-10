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

    def test_execution_read_request_shapes(self):
        calls = []
        class Client(HyperliquidReadOnlyClient):
            def _post_info(self, body):
                calls.append(body)
                return []
        c = Client("0xMASTER")
        c.fetch_user_fills_by_time(100, 200)
        self.assertEqual(calls[0], {
            "type": "userFillsByTime",
            "user": "0xMASTER",
            "startTime": 100,
            "aggregateByTime": False,
            "endTime": 200,
        })
        c.fetch_user_role("0xAGENT")
        self.assertEqual(calls[1], {"type": "userRole", "user": "0xAGENT"})
        c.query_order_status_by_cloid("0x" + "11" * 16)
        self.assertEqual(calls[2]["type"], "orderStatus")
        self.assertEqual(calls[2]["oid"], "0x" + "11" * 16)

class HyperliquidUnifiedAccountTests(unittest.TestCase):
    META = [
        {"universe": [{"name": "BTC", "szDecimals": 5}]},
        [{"markPx": "100000"}],
    ]

    def test_unified_account_uses_spot_usdc_as_account_value(self):
        class Client(HyperliquidReadOnlyClient):
            def _post_info(self, body):
                typ = body["type"]
                if typ == "userAbstraction":
                    return "unifiedAccount"
                if typ == "spotClearinghouseState":
                    return {"balances": [{"coin": "USDC", "token": 0, "hold": "0", "total": "975.00"}]}
                if typ == "clearinghouseState":
                    return {
                        "assetPositions": [],
                        "crossMaintenanceMarginUsed": "0",
                        "marginSummary": {"accountValue": "0", "totalNtlPos": "0", "totalMarginUsed": "0"},
                        "withdrawable": "0",
                    }
                if typ == "metaAndAssetCtxs":
                    return HyperliquidUnifiedAccountTests.META
                raise AssertionError(body)

        snap = Client("0xuser").fetch_account_snapshot()
        self.assertEqual(snap.account_mode, "unifiedAccount")
        self.assertEqual(snap.account_value_source, "spotClearinghouseState.USDC.total")
        self.assertAlmostEqual(snap.account_value_usd, 975.0)
        self.assertAlmostEqual(snap.total_notional_usd, 0.0)
        self.assertAlmostEqual(snap.total_margin_used_usd, 0.0)

    def test_unified_account_keeps_perp_positions_but_not_zero_perp_equity(self):
        class Client(HyperliquidReadOnlyClient):
            def _post_info(self, body):
                typ = body["type"]
                if typ == "userAbstraction":
                    return "unifiedAccount"
                if typ == "spotClearinghouseState":
                    return {"balances": [{"coin": "USDC", "token": 0, "hold": "10", "total": "1000"}]}
                if typ == "clearinghouseState":
                    return {
                        "assetPositions": [{
                            "position": {
                                "coin": "BTC", "szi": "0.001", "positionValue": "100",
                                "marginUsed": "10", "leverage": {"type": "cross", "value": 10},
                            }
                        }],
                        "crossMaintenanceMarginUsed": "5",
                        "marginSummary": {"accountValue": "0", "totalNtlPos": "0", "totalMarginUsed": "0"},
                        "withdrawable": "0",
                    }
                if typ == "metaAndAssetCtxs":
                    return HyperliquidUnifiedAccountTests.META
                raise AssertionError(body)

        snap = Client("0xuser").fetch_account_snapshot()
        self.assertAlmostEqual(snap.account_value_usd, 1000.0)
        self.assertAlmostEqual(snap.total_notional_usd, 100.0)
        self.assertAlmostEqual(snap.total_margin_used_usd, 10.0)
        self.assertAlmostEqual(snap.positions["BTC"].quantity, 0.001)
        self.assertAlmostEqual(snap.current_margin_ratio, 0.005)
        self.assertAlmostEqual(snap.withdrawable_usd, 980.0)

    def test_standard_account_remains_perp_summary_authoritative(self):
        calls = []
        class Client(HyperliquidReadOnlyClient):
            def _post_info(self, body):
                calls.append(body["type"])
                typ = body["type"]
                if typ == "userAbstraction":
                    return "disabled"
                if typ == "clearinghouseState":
                    return {
                        "assetPositions": [],
                        "marginSummary": {"accountValue": "1234", "totalNtlPos": "200", "totalMarginUsed": "20"},
                        "withdrawable": "1214",
                    }
                if typ == "metaAndAssetCtxs":
                    return HyperliquidUnifiedAccountTests.META
                raise AssertionError(body)

        snap = Client("0xuser").fetch_account_snapshot()
        self.assertEqual(snap.account_mode, "disabled")
        self.assertAlmostEqual(snap.account_value_usd, 1234.0)
        self.assertNotIn("spotClearinghouseState", calls)

    def test_default_zero_perp_summary_falls_back_to_spot_usdc(self):
        class Client(HyperliquidReadOnlyClient):
            def _post_info(self, body):
                typ = body["type"]
                if typ == "userAbstraction":
                    return "default"
                if typ == "spotClearinghouseState":
                    return {"balances": [{"coin": "USDC", "total": "975", "hold": "0"}]}
                if typ == "clearinghouseState":
                    return {"assetPositions": [], "marginSummary": {"accountValue": "0", "totalNtlPos": "0", "totalMarginUsed": "0"}, "withdrawable": "0"}
                if typ == "metaAndAssetCtxs":
                    return HyperliquidUnifiedAccountTests.META
                raise AssertionError(body)

        snap = Client("0xuser").fetch_account_snapshot()
        self.assertEqual(snap.account_mode, "unifiedAccount")
        self.assertAlmostEqual(snap.account_value_usd, 975.0)

    def test_portfolio_margin_fails_closed(self):
        class Client(HyperliquidReadOnlyClient):
            def _post_info(self, body):
                if body["type"] == "userAbstraction":
                    return "portfolioMargin"
                raise AssertionError("should fail before account reads")
        with self.assertRaisesRegex(ValueError, "portfolio-margin"):
            Client("0xuser").fetch_account_snapshot()
