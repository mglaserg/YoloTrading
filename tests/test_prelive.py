from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

from crypto_yolo.models import BboQuote, TradePlanRow
from crypto_yolo.prelive import PreLiveLedger, build_alo_intents


def row(ticker="BTC", trade_qty=0.1, trade_usd=1000.0):
    return TradePlanRow(
        ticker=ticker, price=10000.0, target_weight=0.2, current_weight=0.1,
        target_quantity=1.0, current_quantity=0.9, trade_quantity=trade_qty,
        trade_value_usd=trade_usd, post_trade_weight=0.196,
        within_buffer_before=False, within_buffer_after=True,
        arrival_price=9900.0, is_universe_exit=False,
    )


class PreLiveTests(unittest.TestCase):
    def test_cloid_is_deterministic_and_16_bytes_hex(self):
        a = PreLiveLedger.make_cloid(run_key="abc", account="0xA", ticker="BTC", side="BUY")
        b = PreLiveLedger.make_cloid(run_key="abc", account="0xA", ticker="BTC", side="BUY")
        self.assertEqual(a, b)
        self.assertEqual(len(a), 34)
        self.assertTrue(a.startswith("0x"))

    def test_alo_buy_uses_best_bid_sell_uses_best_ask(self):
        now = datetime.now(timezone.utc)
        quotes = {
            "BTC": BboQuote("BTC", 9990.0, 10010.0, now),
            "ETH": BboQuote("ETH", 1999.0, 2001.0, now),
        }
        buy = row("BTC", 0.1, 1000)
        sell = row("ETH", -0.5, -1000)
        intents = build_alo_intents(plan=[buy, sell], quotes=quotes, run_key="r", account_address="0xyolo", min_order_usd=10)
        self.assertEqual(intents[0].side, "BUY")
        self.assertEqual(intents[0].limit_price, 9990.0)
        self.assertEqual(intents[1].side, "SELL")
        self.assertEqual(intents[1].limit_price, 2001.0)
        self.assertEqual(intents[0].tif, "Alo")

    def test_position_reduction_is_reduce_only_even_inside_universe(self):
        now = datetime.now(timezone.utc)
        reducing = TradePlanRow(
            ticker="BTC", price=10000, target_weight=0.05, current_weight=0.1,
            target_quantity=0.5, current_quantity=1.0, trade_quantity=-0.5,
            trade_value_usd=-5000, post_trade_weight=0.05,
            within_buffer_before=False, within_buffer_after=True,
            arrival_price=9900, is_universe_exit=False,
        )
        intent = build_alo_intents(
            plan=[reducing], quotes={"BTC": BboQuote("BTC", 9990, 10010, now)},
            run_key="reduce", account_address="0xyolo", min_order_usd=10,
        )[0]
        self.assertTrue(intent.reduce_only)
        self.assertEqual(intent.destination_quantity, 0.5)

    def test_subminimum_universe_exit_is_not_silently_dropped(self):
        now = datetime.now(timezone.utc)
        exit_row = TradePlanRow(
            ticker="DOGE", price=5.0, target_weight=0.0, current_weight=0.00005,
            target_quantity=0.0, current_quantity=1.0, trade_quantity=-1.0,
            trade_value_usd=-5.0, post_trade_weight=0.0,
            within_buffer_before=False, within_buffer_after=True,
            arrival_price=None, is_universe_exit=True,
        )
        intents = build_alo_intents(
            plan=[exit_row], quotes={"DOGE": BboQuote("DOGE", 4.99, 5.01, now)},
            run_key="exit-dust", account_address="0xyolo", min_order_usd=10,
        )
        self.assertEqual(len(intents), 1)
        self.assertEqual(intents[0].side, "SELL")
        self.assertTrue(intents[0].reduce_only)
        self.assertEqual(intents[0].destination_quantity, 0.0)

    def test_sign_flip_is_split_into_reduce_only_close_then_open(self):
        now = datetime.now(timezone.utc)
        flip = TradePlanRow(
            ticker="BTC", price=10000, target_weight=0.1, current_weight=-0.05,
            target_quantity=0.1, current_quantity=-0.05, trade_quantity=0.15,
            trade_value_usd=1500, post_trade_weight=0.1,
            within_buffer_before=False, within_buffer_after=True,
            arrival_price=9900, is_universe_exit=False,
        )
        intents = build_alo_intents(
            plan=[flip], quotes={"BTC": BboQuote("BTC", 9990, 10010, now)},
            run_key="flip", account_address="0xyolo", min_order_usd=10,
        )
        self.assertEqual(len(intents), 2)
        close, open_ = intents
        self.assertEqual(close.side, "BUY")
        self.assertTrue(close.reduce_only)
        self.assertAlmostEqual(close.quantity, 0.05)
        self.assertAlmostEqual(close.destination_quantity, 0.0)
        self.assertFalse(open_.reduce_only)
        self.assertAlmostEqual(open_.quantity, 0.1)
        self.assertAlmostEqual(open_.current_quantity, 0.0)
        self.assertNotEqual(close.cloid, open_.cloid)
        self.assertNotEqual(
            PreLiveLedger.make_reprice_cloid(base_cloid=close.cloid, attempt=1),
            PreLiveLedger.make_reprice_cloid(base_cloid=open_.cloid, attempt=1),
        )

    def test_persist_run_is_idempotent_for_same_run_key(self):
        with tempfile.TemporaryDirectory() as td:
            ledger = PreLiveLedger(Path(td) / "yolo.sqlite")
            now = datetime.now(timezone.utc)
            intent = build_alo_intents(
                plan=[row()], quotes={"BTC": BboQuote("BTC", 9990, 10010, now)},
                run_key="same", account_address="0xyolo", min_order_usd=10,
            )
            kwargs = dict(
                run_key="same", signal_date="2026-09-03", signal_snapshot_id=1,
                signal_fingerprint="fp", network="testnet", execution_mode="plan",
                account_address="0xyolo", effective_nominal_usd=50_000,
                risk_approved=True, risk_reasons=(), account_value_usd=20_000,
                total_margin_used_usd=1000, health_status="PRE-LIVE READY", intents=intent,
            )
            first = ledger.persist_run(**kwargs)
            second = ledger.persist_run(**kwargs)
            self.assertEqual(first.run_id, second.run_id)
            self.assertEqual(second.intent_count, 1)


    def test_untransmitted_same_run_refreshes_preview_bbo(self):
        with tempfile.TemporaryDirectory() as td:
            ledger = PreLiveLedger(Path(td) / "yolo.sqlite")
            now = datetime.now(timezone.utc)
            plan = [row()]
            first_intents = build_alo_intents(
                plan=plan, quotes={"BTC": BboQuote("BTC", 9990, 10010, now)},
                run_key="same", account_address="0xyolo", min_order_usd=10,
            )
            kwargs = dict(
                run_key="same", signal_date="2026-09-10", signal_snapshot_id=1,
                signal_fingerprint="fp", network="mainnet", execution_mode="plan",
                account_address="0xyolo", effective_nominal_usd=50_000,
                risk_approved=True, risk_reasons=(), account_value_usd=20_000,
                total_margin_used_usd=1000, health_status="PRE-LIVE READY",
                direction_mode="long_short", plan=plan,
            )
            first = ledger.persist_run(intents=first_intents, **kwargs)
            self.assertEqual(ledger.intents_for_run(first.run_id)[0]["limit_price"], 9990.0)

            fresh_intents = build_alo_intents(
                plan=plan, quotes={"BTC": BboQuote("BTC", 9997, 10003, now)},
                run_key="same", account_address="0xyolo", min_order_usd=10,
            )
            second = ledger.persist_run(intents=fresh_intents, **kwargs)
            self.assertEqual(first.run_id, second.run_id)
            refreshed = ledger.intents_for_run(first.run_id)
            self.assertEqual(len(refreshed), 1)
            self.assertEqual(refreshed[0]["limit_price"], 9997.0)

    def test_execution_lock_is_separate_from_plan(self):
        with tempfile.TemporaryDirectory() as td:
            ledger = PreLiveLedger(Path(td) / "yolo.sqlite")
            self.assertFalse(ledger.execution_lock_exists(signal_date="2026-09-03", network="mainnet", account_address="0xyolo"))
            # Reserve manually to exercise the future live guard.
            ledger.reserve_execution(signal_date="2026-09-03", network="mainnet", account_address="0xyolo", run_id=1)
            self.assertTrue(ledger.execution_lock_exists(signal_date="2026-09-03", network="mainnet", account_address="0xyolo"))


if __name__ == "__main__":
    unittest.main()
