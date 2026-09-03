from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

from crypto_yolo.cashflows import CashFlowLedger, CashFlowSyncError
from crypto_yolo.config import YoloConfig
from crypto_yolo.sizing import SizingLedger


class FakeLedgerClient:
    def __init__(self, events, portfolio=None):
        self.events = events
        self.calls = []
        self.portfolio = portfolio or [["perpDay", {"accountValueHistory": [[900, "20000"]], "pnlHistory": [], "vlm": "0"}]]

    def fetch_non_funding_ledger_updates(self, start, end):
        self.calls.append((start, end))
        return self.events

    def fetch_portfolio_history(self):
        return self.portfolio


class CashFlowTests(unittest.TestCase):
    def test_classifies_subaccount_transfer_sign(self):
        user = "0xyolo"
        incoming = {"delta": {"type": "subAccountTransfer", "usdc": "100", "user": "0xmain", "destination": user}}
        outgoing = {"delta": {"type": "subAccountTransfer", "usdc": "25", "user": user, "destination": "0xmain"}}
        self.assertEqual(CashFlowLedger.classify(incoming, user), ("external_flow", 100.0))
        self.assertEqual(CashFlowLedger.classify(outgoing, user), ("external_flow", -25.0))

    def test_first_sync_initializes_cursor_without_replaying_history(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "yolo.sqlite"
            cash = CashFlowLedger(path)
            sizing = SizingLedger(path)
            cfg = replace(YoloConfig(), sizing_mode="compound", hl_subaccount_address="0xyolo")
            sizing.decision(account_value_usd=20_000, config=cfg)
            result = cash.sync(
                client=FakeLedgerClient([{"time": 10, "delta": {"type": "deposit", "usdc": "500"}}]),
                sizing_ledger=sizing,
                user_address="0xyolo",
                current_account_value_usd=20_000,
                through_time_ms=1000,
            )
            self.assertTrue(result.initialized_cursor)
            self.assertEqual(result.applied_events, 0)

    def test_recognized_deposit_is_unitized_not_performance(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "yolo.sqlite"
            cash = CashFlowLedger(path)
            sizing = SizingLedger(path)
            cfg = replace(YoloConfig(), sizing_mode="compound", hl_subaccount_address="0xyolo")
            sizing.decision(account_value_usd=20_000, config=cfg)
            cash.initialize_cursor(1000)
            # P&L first takes the account from 20k to 22k, then a 10k deposit arrives.
            event = {"time": 1100, "hash": "0xabc", "delta": {"type": "deposit", "usdc": "10000"}}
            portfolio = [["perpDay", {"accountValueHistory": [[1050, "22000"], [1150, "32000"]], "pnlHistory": [], "vlm": "0"}]]
            result = cash.sync(
                client=FakeLedgerClient([event], portfolio), sizing_ledger=sizing, user_address="0xyolo",
                current_account_value_usd=32_000, through_time_ms=1200,
            )
            self.assertEqual(result.net_external_flow_usd, 10_000)
            decision = sizing.decision(account_value_usd=32_000, config=cfg)
            self.assertAlmostEqual(decision.raw_multiplier, 1.1)

    def test_unknown_cashflow_blocks_until_rebase_acknowledges(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "yolo.sqlite"
            cash = CashFlowLedger(path)
            sizing = SizingLedger(path)
            cfg = replace(YoloConfig(), sizing_mode="compound", hl_subaccount_address="0xyolo")
            sizing.decision(account_value_usd=20_000, config=cfg)
            cash.initialize_cursor(1000)
            event = {"time": 1100, "hash": "0xdef", "delta": {"type": "spotTransfer", "token": "USDC", "usdcValue": "10"}}
            with self.assertRaises(CashFlowSyncError):
                cash.sync(client=FakeLedgerClient([event]), sizing_ledger=sizing, user_address="0xyolo", current_account_value_usd=20_000, through_time_ms=1200)
            self.assertEqual(cash.unresolved_manual_count(), 1)
            cash.acknowledge_manual_events()
            self.assertEqual(cash.unresolved_manual_count(), 0)

    def test_sync_gap_fails_closed_instead_of_skipping_history(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "yolo.sqlite"
            cash = CashFlowLedger(path)
            sizing = SizingLedger(path)
            cfg = replace(YoloConfig(), sizing_mode="compound", hl_subaccount_address="0xyolo")
            sizing.decision(account_value_usd=20_000, config=cfg)
            cash.initialize_cursor(1000)
            with self.assertRaisesRegex(CashFlowSyncError, "gap exceeds"):
                cash.sync(
                    client=FakeLedgerClient([]), sizing_ledger=sizing, user_address="0xyolo",
                    current_account_value_usd=20_000, through_time_ms=1000 + 8 * 86_400_000, lookback_days=7,
                )


if __name__ == "__main__":
    unittest.main()
