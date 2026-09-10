from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

from crypto_yolo.config import YoloConfig
from crypto_yolo.execution import (
    LiveExecutionError,
    execute_run,
    parse_order_response,
    validate_api_wallet,
)
from crypto_yolo.models import BboQuote, ExchangeSnapshot, MarketSpec, Position, TradePlanRow
from crypto_yolo.prelive import PreLiveLedger, build_alo_intents


NOW = datetime(2026, 9, 10, 13, 1, tzinfo=timezone.utc)


def plan_row():
    return TradePlanRow(
        ticker="BTC",
        price=10_000.0,
        target_weight=0.1,
        current_weight=0.0,
        target_quantity=0.1,
        current_quantity=0.0,
        trade_quantity=0.1,
        trade_value_usd=1000.0,
        post_trade_weight=0.1,
        within_buffer_before=False,
        within_buffer_after=True,
        arrival_price=9900.0,
    )


def snapshot(qty=0.0):
    positions = {} if qty == 0 else {"BTC": Position("BTC", qty, 10_000.0)}
    return ExchangeSnapshot(
        pulled_at_utc=NOW,
        account_value_usd=2000.0,
        total_notional_usd=abs(qty) * 10_000,
        total_margin_used_usd=0.0,
        withdrawable_usd=2000.0,
        positions=positions,
        markets={"BTC": MarketSpec("BTC", 10_000.0, 3)},
        account_mode="unifiedAccount",
        account_value_source="spot",
    )


class FakeArchive:
    def __init__(self):
        self.rebalanced = []

    def mark_snapshot_rebalanced(self, snapshot_id):
        self.rebalanced.append(snapshot_id)


class FakeRead:
    def __init__(self, final_qty=0.1):
        self.position_qty = 0.0
        self.snapshot_calls = 0
        self.status_by_cloid = {}
        self.fills = []
        self.bbo_calls = 0

    def fetch_account_snapshot(self):
        self.snapshot_calls += 1
        return snapshot(self.position_qty)

    def fetch_bbo(self, ticker):
        self.bbo_calls += 1
        return BboQuote(ticker, 9995.0, 10005.0, NOW)

    def query_order_status_by_cloid(self, cloid):
        return self.status_by_cloid[cloid]

    def fetch_user_fills_by_time(self, start_time_ms, end_time_ms=None):
        return list(self.fills)

    def fetch_user_role(self, address):
        return {"role": "agent", "data": {"user": "0xmaster"}}


class FakeExec:
    def __init__(self, read, responses):
        self.read = read
        self.responses = list(responses)
        self.deadman = []
        self.placed = []
        self.canceled = []
        self.signer_address = "0xagent"
        self.next_oid = 100

    def schedule_cancel(self, time_ms=None):
        self.deadman.append(time_ms)
        return {"status": "ok", "response": {"type": "default"}}

    def place_alo(self, **kwargs):
        self.placed.append(kwargs)
        response = self.responses.pop(0)
        outcome = parse_order_response(response)
        if outcome.oid is not None:
            self.read.status_by_cloid[kwargs["cloid"]] = {
                "status": "order",
                "order": {
                    "status": "filled",
                    "order": {
                        "oid": outcome.oid,
                        "origSz": str(kwargs["quantity"]),
                        "sz": "0",
                    },
                },
            }
            if outcome.status in {"open", "filled"}:
                signed_qty = kwargs["quantity"] if kwargs["side"] == "BUY" else -kwargs["quantity"]
                self.read.position_qty += signed_qty
                self.read.fills.append(
                    {
                        "coin": kwargs["ticker"],
                        "side": "B" if kwargs["side"] == "BUY" else "A",
                        "px": str(kwargs["limit_price"]),
                        "sz": str(kwargs["quantity"]),
                        "fee": "0.20",
                        "feeToken": "USDC",
                        "closedPnl": "0",
                        "crossed": False,
                        "time": 1789045260000,
                        "tid": 12345 + outcome.oid,
                        "oid": outcome.oid,
                    }
                )
        return response

    def cancel_by_cloid(self, **kwargs):
        self.canceled.append(kwargs)
        return {"status": "ok", "response": {"type": "cancel", "data": {"statuses": ["success"]}}}


class FakeExecFillLag(FakeExec):
    def place_alo(self, **kwargs):
        self.placed.append(kwargs)
        response = self.responses.pop(0)
        outcome = parse_order_response(response)
        if outcome.oid is not None:
            self.read.status_by_cloid[kwargs["cloid"]] = {
                "status": "order",
                "order": {
                    "status": "filled",
                    "order": {
                        "oid": outcome.oid,
                        "origSz": str(kwargs["quantity"]),
                        "sz": "0",
                    },
                },
            }
        return response


def resting(oid):
    return {"status": "ok", "response": {"type": "order", "data": {"statuses": [{"resting": {"oid": oid}}]}}}


def filled(oid, qty="0.1", px="10000"):
    return {"status": "ok", "response": {"type": "order", "data": {"statuses": [{"filled": {"oid": oid, "totalSz": qty, "avgPx": px}}]}}}


def rejected(message):
    return {"status": "ok", "response": {"type": "order", "data": {"statuses": [{"error": message}]}}}


class ExecutionTests(unittest.TestCase):
    def _persist(self, db_path):
        ledger = PreLiveLedger(db_path)
        row = plan_row()
        intents = build_alo_intents(
            plan=[row],
            quotes={"BTC": BboQuote("BTC", 9990.0, 10010.0, NOW)},
            run_key="runkey",
            account_address="0xmaster",
            min_order_usd=10,
        )
        persisted = ledger.persist_run(
            run_key="runkey",
            signal_date="2026-09-10",
            signal_snapshot_id=7,
            signal_fingerprint="fp",
            network="mainnet",
            execution_mode="execute",
            account_address="0xmaster",
            effective_nominal_usd=10_000,
            risk_approved=True,
            risk_reasons=(),
            account_value_usd=2000,
            total_margin_used_usd=0,
            health_status="PRE-LIVE READY",
            intents=intents,
            direction_mode="long_short",
            plan=[row],
        )
        return ledger, persisted.run_id

    def test_parse_order_response(self):
        self.assertEqual(parse_order_response(resting(1)).status, "open")
        self.assertEqual(parse_order_response(filled(2)).status, "filled")
        self.assertIn("nope", parse_order_response(rejected("nope")).error)

    def test_complete_live_run_persists_fill_tca_and_deadman(self):
        with tempfile.TemporaryDirectory() as td:
            ledger, run_id = self._persist(Path(td) / "yolo.sqlite")
            read = FakeRead(final_qty=0.1)
            exe = FakeExec(read, [resting(100)])
            archive = FakeArchive()
            cfg = YoloConfig(
                nominal_usd=10_000,
                network="mainnet",
                execution_mode="execute",
                direction_mode="long_short",
                hl_account_address="0xmaster",
                min_order_usd=10,
                execution_reprice_seconds=0,
                execution_max_reprices=0,
                execution_deadman_seconds=60,
            )
            result = execute_run(
                run_id=run_id,
                config=cfg,
                read_client=read,
                execution_client=exe,
                ledger=ledger,
                archive=archive,
                sleep_fn=lambda _: None,
                time_ms_fn=lambda: 1789045260000,
            )
            self.assertEqual(result.status, "complete")
            self.assertTrue(result.postcheck_ok)
            self.assertEqual(exe.placed[0]["limit_price"], 9995.0)
            self.assertEqual(archive.rebalanced, [7])
            self.assertEqual(len(ledger.fills_for_intent(ledger.intents_for_run(run_id)[0]["id"])), 1)
            self.assertIsNotNone(result.intents[0].realized_tca_bps)
            self.assertIsNotNone(exe.deadman[0])
            self.assertIsNone(exe.deadman[-1])
            self.assertEqual(ledger.execution_session(run_id)["status"], "complete")

    def test_bad_alo_reprices_with_new_cloid(self):
        with tempfile.TemporaryDirectory() as td:
            ledger, run_id = self._persist(Path(td) / "yolo.sqlite")
            read = FakeRead(final_qty=0.1)
            exe = FakeExec(read, [rejected("Bad ALO price"), filled(201)])
            cfg = YoloConfig(
                nominal_usd=10_000,
                network="mainnet",
                execution_mode="execute",
                direction_mode="long_short",
                hl_account_address="0xmaster",
                min_order_usd=10,
                execution_reprice_seconds=0,
                execution_max_reprices=1,
            )
            result = execute_run(
                run_id=run_id,
                config=cfg,
                read_client=read,
                execution_client=exe,
                ledger=ledger,
                archive=FakeArchive(),
                sleep_fn=lambda _: None,
                time_ms_fn=lambda: 1789045260000,
            )
            self.assertEqual(result.status, "complete")
            self.assertEqual(len(exe.placed), 2)
            self.assertNotEqual(exe.placed[0]["cloid"], exe.placed[1]["cloid"])
            # Attempt zero and the reprice each take a fresh BBO; a persisted
            # preview price is never reused as a live quote.
            self.assertEqual(read.bbo_calls, 2)

    def test_permanent_rejection_fails_closed_and_clears_deadman(self):
        with tempfile.TemporaryDirectory() as td:
            ledger, run_id = self._persist(Path(td) / "yolo.sqlite")
            read = FakeRead(final_qty=0.0)
            exe = FakeExec(read, [rejected("Insufficient margin")])
            cfg = YoloConfig(
                nominal_usd=10_000,
                network="mainnet",
                execution_mode="execute",
                direction_mode="long_short",
                hl_account_address="0xmaster",
                execution_reprice_seconds=0,
            )
            with self.assertRaises(LiveExecutionError):
                execute_run(
                    run_id=run_id,
                    config=cfg,
                    read_client=read,
                    execution_client=exe,
                    ledger=ledger,
                    archive=FakeArchive(),
                    sleep_fn=lambda _: None,
                    time_ms_fn=lambda: 1789045260000,
                )
            self.assertIsNone(exe.deadman[-1])
            self.assertEqual(ledger.execution_session(run_id)["status"], "attention")

    def test_fill_feed_lag_fails_closed_without_reissue(self):
        with tempfile.TemporaryDirectory() as td:
            ledger, run_id = self._persist(Path(td) / "yolo.sqlite")
            read = FakeRead(final_qty=0.1)
            exe = FakeExecFillLag(read, [filled(301)])
            cfg = YoloConfig(
                nominal_usd=10_000,
                network="mainnet",
                execution_mode="execute",
                direction_mode="long_short",
                hl_account_address="0xmaster",
                min_order_usd=10,
                execution_reprice_seconds=0,
                execution_max_reprices=2,
            )
            with self.assertRaisesRegex(LiveExecutionError, "no order was reissued"):
                execute_run(
                    run_id=run_id,
                    config=cfg,
                    read_client=read,
                    execution_client=exe,
                    ledger=ledger,
                    archive=FakeArchive(),
                    sleep_fn=lambda _: None,
                    time_ms_fn=lambda: 1789045260000,
                )
            self.assertEqual(len(exe.placed), 1)
            self.assertEqual(ledger.execution_session(run_id)["status"], "attention")

    def test_restart_reuses_prepared_but_unsent_attempt(self):
        with tempfile.TemporaryDirectory() as td:
            ledger, run_id = self._persist(Path(td) / "yolo.sqlite")
            intent = ledger.intents_for_run(run_id)[0]
            prepared = ledger.prepare_attempt(
                intent_id=int(intent["id"]),
                attempt_no=0,
                cloid=str(intent["cloid"]),
                quantity=float(intent["quantity"]),
                limit_price=float(intent["limit_price"]),
            )
            read = FakeRead(final_qty=0.1)
            exe = FakeExec(read, [filled(401)])
            cfg = YoloConfig(
                nominal_usd=10_000,
                network="mainnet",
                execution_mode="execute",
                direction_mode="long_short",
                hl_account_address="0xmaster",
                min_order_usd=10,
                execution_reprice_seconds=0,
                execution_max_reprices=1,
            )
            result = execute_run(
                run_id=run_id,
                config=cfg,
                read_client=read,
                execution_client=exe,
                ledger=ledger,
                archive=FakeArchive(),
                sleep_fn=lambda _: None,
                time_ms_fn=lambda: 1789045260000,
            )
            self.assertEqual(result.status, "complete")
            self.assertEqual(len(exe.placed), 1)
            self.assertEqual(exe.placed[0]["cloid"], prepared["cloid"])
            self.assertEqual(len(ledger.attempts_for_intent(int(intent["id"]))), 1)

    def test_stale_run_cannot_be_executed_or_resumed(self):
        with tempfile.TemporaryDirectory() as td:
            ledger, run_id = self._persist(Path(td) / "yolo.sqlite")
            read = FakeRead()
            exe = FakeExec(read, [filled(501)])
            cfg = YoloConfig(
                nominal_usd=10_000, network="mainnet", execution_mode="execute",
                direction_mode="long_short", hl_account_address="0xmaster", min_order_usd=10,
            )
            stale_ms = int(datetime(2026, 9, 11, 13, 1, tzinfo=timezone.utc).timestamp() * 1000)
            with self.assertRaisesRegex(LiveExecutionError, "refusing stale execution"):
                execute_run(
                    run_id=run_id, config=cfg, read_client=read, execution_client=exe,
                    ledger=ledger, archive=FakeArchive(), sleep_fn=lambda _: None,
                    time_ms_fn=lambda: stale_ms,
                )
            self.assertEqual(exe.placed, [])
            self.assertEqual(exe.deadman, [])

    def test_position_drift_before_send_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            ledger, run_id = self._persist(Path(td) / "yolo.sqlite")
            read = FakeRead()
            read.position_qty = 0.02
            exe = FakeExec(read, [filled(601)])
            cfg = YoloConfig(
                nominal_usd=10_000, network="mainnet", execution_mode="execute",
                direction_mode="long_short", hl_account_address="0xmaster", min_order_usd=10,
                execution_deadman_seconds=60,
            )
            with self.assertRaisesRegex(LiveExecutionError, "live position drifted"):
                execute_run(
                    run_id=run_id, config=cfg, read_client=read, execution_client=exe,
                    ledger=ledger, archive=FakeArchive(), sleep_fn=lambda _: None,
                    time_ms_fn=lambda: 1789045260000,
                )
            self.assertEqual(exe.placed, [])
            self.assertIsNone(exe.deadman[-1])


    def test_resume_requires_execution_session(self):
        with tempfile.TemporaryDirectory() as td:
            ledger, run_id = self._persist(Path(td) / "yolo.sqlite")
            read = FakeRead()
            exe = FakeExec(read, [filled(701)])
            cfg = YoloConfig(
                nominal_usd=10_000, network="mainnet", execution_mode="execute",
                direction_mode="long_short", hl_account_address="0xmaster", min_order_usd=10,
            )
            with self.assertRaisesRegex(LiveExecutionError, "never entered live execution"):
                execute_run(
                    run_id=run_id, config=cfg, read_client=read, execution_client=exe,
                    ledger=ledger, archive=FakeArchive(), sleep_fn=lambda _: None,
                    time_ms_fn=lambda: 1789045260000, require_existing_session=True,
                )
            self.assertEqual(exe.placed, [])
            self.assertIsNone(ledger.get_execution_lock(
                signal_date="2026-09-10", network="mainnet", account_address="0xmaster"
            ))

    def test_api_wallet_must_be_agent_for_master(self):
        read = FakeRead()
        exe = type("E", (), {"signer_address": "0xagent"})()
        validate_api_wallet(read_client=read, execution_client=exe, master_account_address="0xmaster")
        read.fetch_user_role = lambda _: {"role": "user"}
        with self.assertRaises(LiveExecutionError):
            validate_api_wallet(read_client=read, execution_client=exe, master_account_address="0xmaster")


if __name__ == "__main__":
    unittest.main()
