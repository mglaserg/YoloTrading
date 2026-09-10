from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import date
import json
from pathlib import Path

from .archive import SignalArchive
from .cashflows import CashFlowLedger, CashFlowSyncError, CashFlowSyncResult
from .config import YoloConfig
from .env import load_env_file
from .execution import (
    HyperliquidSdkExecutionClient,
    LiveDependencyError,
    LiveExecutionError,
    execute_run,
    validate_api_wallet,
)
from .health import HealthCheck, HealthSummary, print_health
from .live import fetch_live_inputs, wait_for_live_inputs
from .models import Position, SignalRow
from .planner import plan_trades
from .portfolio import build_targets
from .prelive import PreLiveLedger, build_alo_intents
from .providers import HyperliquidReadOnlyClient
from .risk import summarize_post_trade
from .sizing import SizingDecision, SizingError, SizingLedger
from .validation import SignalValidationError


def _load_fixture(path: Path) -> tuple[list[SignalRow], dict[str, Position]]:
    data = json.loads(path.read_text())
    signals = [SignalRow(**row) for row in data["signals"]]
    positions = {row["ticker"]: Position(**row) for row in data.get("positions", [])}
    return signals, positions


def _hl_client(config: YoloConfig) -> HyperliquidReadOnlyClient:
    return HyperliquidReadOnlyClient(
        user_address=config.hyperliquid_user_address,
        api_url=config.hyperliquid_api_url,
        timeout_seconds=config.request_timeout_seconds,
    )


def _fetch_exchange_only(config: YoloConfig):
    return _hl_client(config).fetch_account_snapshot()


def _execution_client(config: YoloConfig) -> HyperliquidSdkExecutionClient:
    return HyperliquidSdkExecutionClient(
        private_key=config.hl_api_wallet_private_key,
        account_address=config.hl_account_address,
        subaccount_address=config.hl_subaccount_address,
        api_url=config.hyperliquid_api_url,
        timeout_seconds=config.request_timeout_seconds,
    )


def _require_live_execution_ready(config: YoloConfig):
    if not config.live_trading_enabled:
        raise LiveExecutionError(
            "YOLO_LIVE_TRADING_ENABLED is false; live order transmission remains interlocked"
        )
    if config.execution_max_reprices < 0:
        raise LiveExecutionError("YOLO_EXECUTION_MAX_REPRICES cannot be negative")
    if config.execution_reprice_seconds < 0:
        raise LiveExecutionError("YOLO_EXECUTION_REPRICE_SECONDS cannot be negative")
    if config.execution_deadman_seconds < 5:
        raise LiveExecutionError("YOLO_EXECUTION_DEADMAN_SECONDS must be at least 5 seconds")
    read_client = _hl_client(config)
    execution_client = _execution_client(config)
    validate_api_wallet(
        read_client=read_client,
        execution_client=execution_client,
        master_account_address=config.hl_account_address,
    )
    return read_client, execution_client


def _print_sizing(sizing: SizingDecision) -> None:
    print("\nSIZING")
    print(f"mode:             {sizing.mode.upper()}")
    print(f"base nominal:     ${sizing.base_nominal_usd:,.2f}")
    if sizing.mode == "compound":
        print(f"account value:    ${sizing.account_value_usd:,.2f}")
        print(f"starting equity:  ${sizing.initial_account_value_usd:,.2f}")
        print(f"NAV / unit:       {sizing.nav_per_unit:.6f}")
        print(f"raw multiplier:   {sizing.raw_multiplier:.4f}x")
        print(
            f"applied mult.:    {sizing.applied_multiplier:.4f}x"
            + ("  (CLIPPED)" if sizing.clipped else "")
        )
        if sizing.initialized_now:
            print("baseline:          INITIALIZED TODAY")
    print(f"effective nominal:${sizing.effective_nominal_usd:,.2f}")


def _print_plan(targets, plan, risk, *, signal_date=None, exchange=None, sizing=None) -> None:
    if signal_date:
        print(f"RW signal date: {signal_date}")
        print("Signal status: CURRENT")
    if exchange is not None:
        print(f"Hyperliquid account mode:  {exchange.account_mode}")
        print(f"Hyperliquid account value: ${exchange.account_value_usd:,.2f}")
        print(f"Hyperliquid value source:  {exchange.account_value_source}")
        print(f"Hyperliquid margin used:   ${exchange.total_margin_used_usd:,.2f}")
        if exchange.current_margin_ratio is not None:
            print(f"Hyperliquid current margin ratio: {exchange.current_margin_ratio:.1%}")
    if sizing is not None:
        _print_sizing(sizing)
    print()
    print("ticker  mom    trend  carry   ewvol  target_w  current_w  trade_qty    trade_usd  note")
    target_by_ticker = {t.ticker: t for t in targets}
    for p in plan:
        t = target_by_ticker.get(p.ticker)
        mom = f"{t.momentum:>5.2f}" if t else "    -"
        trend = f"{t.trend:>6.2f}" if t else "     -"
        carry = f"{t.carry:>6.2f}" if t else "     -"
        ewvol = f"{t.ewvol:>6.3f}" if t else "     -"
        note = "EXIT: left RW universe" if p.is_universe_exit else ("HOLD" if p.within_buffer_before else "TRADE")
        print(
            f"{p.ticker:<6} {mom} {trend} {carry} {ewvol} "
            f"{p.target_weight:>9.3f} {p.current_weight:>10.3f} "
            f"{p.trade_quantity:>10.6f} {p.trade_value_usd:>11.2f}  {note}"
        )
    turnover = sum(abs(p.trade_value_usd) for p in plan) / max(risk.gross_usd, 1e-12)
    print("\nRISK")
    print(f"gross weight: {risk.gross_weight:.3f}")
    print(f"net weight:   {risk.net_weight:.3f}")
    print(f"gross USD:    ${risk.gross_usd:,.2f}")
    print(f"net USD:      ${risk.net_usd:,.2f}")
    print(f"margin util:  {risk.estimated_margin_utilization:.1%}")
    print(f"turnover*:    {turnover:.1%}")
    print(f"approved:     {risk.approved}")
    for reason in risk.reasons:
        print(f"- {reason}")
    print("* trade dollars divided by post-trade gross dollars")


def _print_intents(intents) -> None:
    print("\nALO ORDER PREVIEW")
    if not intents:
        print("No orders above minimum notional are required.")
        return
    print("ticker side       qty        limit_px   notional    TIF  reduce  proposed_TCA  cloid")
    for i in intents:
        tca = "-" if i.proposed_tca_bps is None else f"{i.proposed_tca_bps:+.1f}bp"
        print(
            f"{i.ticker:<6} {i.side:<4} {i.quantity:>10.6f} {i.limit_price:>12.6f} "
            f"${i.trade_value_usd:>9.2f}  {i.tif:<3}  {str(i.reduce_only):<6} {tca:>11}  {i.cloid}"
        )


def _print_execution_result(result) -> None:
    print("\nLIVE EXECUTION")
    print(f"run id:          {result.run_id}")
    print(f"status:          {result.status.upper()}")
    for item in result.intents:
        avg = "-" if item.average_fill_price is None else f"{item.average_fill_price:.8g}"
        tca = "-" if item.realized_tca_bps is None else f"{item.realized_tca_bps:+.2f}bp"
        print(
            f"{item.ticker:<8} {item.status:<8} filled {item.filled_quantity:.8f}/"
            f"{item.requested_quantity:.8f} avg {avg} fee ${item.fee_usd:.4f} "
            f"TCA {tca} attempts {item.attempts}"
        )
    print(f"post-trade check: {'PASS' if result.postcheck_ok else 'FAIL'}")
    for row in result.postcheck:
        if not row["ok"]:
            print(
                f"- {row['ticker']}: ${row['error_usd']:.2f} position error "
                f"(tolerance ${row['tolerance_usd']:.2f})"
            )


def _require_compound_subaccount(config: YoloConfig) -> None:
    if config.require_dedicated_subaccount_for_compound and not config.hl_subaccount_address:
        raise SizingError(
            "compounding is guarded to a dedicated YOLO subaccount; set HL_YOLO_SUBACCOUNT_ADDRESS "
            "or explicitly set YOLO_REQUIRE_DEDICATED_SUBACCOUNT_FOR_COMPOUND=false"
        )


def _cashflow_sync(config, ledger, exchange, client) -> CashFlowSyncResult | None:
    if config.sizing_mode.strip().lower() != "compound" or not config.auto_sync_cash_flows:
        return None
    _require_compound_subaccount(config)
    cf = CashFlowLedger(config.sqlite_path)
    through_ms = int(exchange.pulled_at_utc.timestamp() * 1000)
    return cf.sync(
        client=client,
        sizing_ledger=ledger,
        user_address=config.hyperliquid_user_address,
        current_account_value_usd=exchange.account_value_usd,
        through_time_ms=through_ms,
        lookback_days=config.cashflow_lookback_days,
    )


def _persist_and_print_prelive(*, config, live, sizing, targets, plan, risk, cashflow_result):
    archive = SignalArchive(config.sqlite_path)
    fingerprint = archive.snapshot_fingerprint(live.signal_snapshot_id)
    prelive = PreLiveLedger(config.sqlite_path)
    client = _hl_client(config)

    run_key = prelive.make_run_key(
        signal_date=live.signal_date.isoformat(),
        signal_fingerprint=fingerprint,
        account=config.hyperliquid_user_address,
        network=config.normalized_network,
        plan=plan,
    )
    trade_rows = [
        p for p in plan
        if abs(p.trade_value_usd) >= config.min_order_usd and abs(p.trade_quantity) > 1e-18
    ]
    quotes = {}
    if risk.approved:
        for row in trade_rows:
            quotes[row.ticker] = client.fetch_bbo(row.ticker)
        intents = build_alo_intents(
            plan=plan,
            quotes=quotes,
            run_key=run_key,
            account_address=config.hyperliquid_user_address,
            min_order_usd=config.min_order_usd,
        )
    else:
        intents = []

    execution_locked = prelive.execution_lock_exists(
        signal_date=live.signal_date.isoformat(),
        network=config.normalized_network,
        account_address=config.hyperliquid_user_address,
    )
    cashflow_detail = "not required in fixed mode"
    cashflow_ok = True
    if cashflow_result is not None:
        if cashflow_result.initialized_cursor:
            cashflow_detail = "ledger cursor initialized; baseline already includes prior cash"
        elif cashflow_result.applied_events:
            cashflow_detail = f"applied {cashflow_result.applied_events} event(s), net {cashflow_result.net_external_flow_usd:+,.2f} USD"
        else:
            cashflow_detail = "no new external cash flows"

    execute_selected = config.normalized_execution_mode == "execute"
    idempotency_ok = (not execution_locked) or not execute_selected
    execution_interlock_ok = (
        True if not execute_selected else (config.live_trading_enabled and bool(config.hl_api_wallet_private_key))
    )
    execution_detail = (
        "PLAN — signed orders will not be sent"
        if not execute_selected
        else (
            "LIVE enabled; API-wallet credential present"
            if execution_interlock_ok
            else "execute selected but live enable/key is missing"
        )
    )

    checks = (
        HealthCheck("RW signals", True, f"current for {live.signal_date.isoformat()}"),
        HealthCheck("Signal archive", True, f"snapshot {live.signal_snapshot_id} persisted"),
        HealthCheck(
            "Hyperliquid state",
            True,
            f"{config.normalized_network}; {live.exchange.account_mode}; "
            f"equity ${live.exchange.account_value_usd:,.2f}",
        ),
        HealthCheck("Cash-flow ledger", cashflow_ok, cashflow_detail),
        HealthCheck("Sizing", True, f"{sizing.mode}; effective nominal ${sizing.effective_nominal_usd:,.2f}"),
        HealthCheck("Direction mode", True, config.normalized_direction_mode),
        HealthCheck("Risk gate", risk.approved, "approved" if risk.approved else "; ".join(risk.reasons)),
        HealthCheck("ALO construction", risk.approved, f"{len(intents)} would-submit intent(s)" if risk.approved else "not built because risk gate failed"),
        HealthCheck("Idempotency", idempotency_ok, "no execution lock for this signal date" if not execution_locked else "signal date already execution-locked"),
        HealthCheck("Execution interlock", execution_interlock_ok, execution_detail),
    )
    health = HealthSummary(checks)
    persisted = prelive.persist_run(
        run_key=run_key,
        signal_date=live.signal_date.isoformat(),
        signal_snapshot_id=live.signal_snapshot_id,
        signal_fingerprint=fingerprint,
        network=config.normalized_network,
        execution_mode=config.normalized_execution_mode,
        account_address=config.hyperliquid_user_address,
        effective_nominal_usd=sizing.effective_nominal_usd,
        risk_approved=risk.approved,
        risk_reasons=risk.reasons,
        account_value_usd=live.exchange.account_value_usd,
        total_margin_used_usd=live.exchange.total_margin_used_usd,
        health_status=health.status,
        intents=intents,
        direction_mode=config.normalized_direction_mode,
        plan=plan,
    )
    archive.mark_snapshot_planned(live.signal_snapshot_id)
    _print_plan(targets, plan, risk, signal_date=live.signal_date.isoformat(), exchange=live.exchange, sizing=sizing)
    _print_intents(intents)
    print_health(health)
    print(f"\nYOLO RUN: {persisted.run_id}  {persisted.run_key[:12]}...")
    print(f"NETWORK: {config.normalized_network.upper()}")
    print(f"DIRECTION: {config.normalized_direction_mode.upper()}")
    print(
        "EXECUTION: PLAN ONLY"
        if not execute_selected
        else "EXECUTION: LIVE TRANSMISSION SELECTED"
    )
    if not health.ok:
        raise SystemExit(2)
    return persisted, intents, health


def main() -> None:
    parser = argparse.ArgumentParser(description="Run, inspect, or execute a Crypto YOLO rebalance")
    parser.add_argument("--fixture", type=Path, default=Path("examples/sample_snapshot.json"))
    parser.add_argument("--live-data", action="store_true", help="Pull real RW + Hyperliquid data, build ALO intents, and execute only when explicitly enabled")
    parser.add_argument("--wait-for-signal", action="store_true", help="Linux/daemon mode: poll RW until today's signal is current, then run one configured rebalance")
    parser.add_argument("--expected-date", type=date.fromisoformat, default=None)
    parser.add_argument("--buffer-mode", choices=["edge", "target"], default=None)
    parser.add_argument("--archive-status", action="store_true", help="Show the most recent archived RW pulls")
    parser.add_argument("--health-status", action="store_true", help="Show the latest persisted health/run summary")
    parser.add_argument("--cashflow-status", action="store_true", help="Show recent detected Hyperliquid cash-flow ledger events")
    parser.add_argument("--sizing-status", action="store_true", help="Show current unitized compounding state using live Hyperliquid equity")
    parser.add_argument("--account-status", action="store_true", help="Read Hyperliquid account mode, equity source, margin, and open positions only")
    parser.add_argument("--execution-status", action="store_true", help="Check live-execution configuration, SDK, API-wallet authorization, and latest execution session without sending orders")
    parser.add_argument("--resume-execution", type=int, default=None, metavar="RUN_ID", help="Resume/reconcile a previously execution-locked run by run id")
    parser.add_argument("--record-flow", type=float, default=None, metavar="USD", help="Manual emergency/admin cash-flow entry (+deposit, -withdrawal)")
    parser.add_argument("--rebase-compounding", action="store_true", help="Reset compounding baseline to current YOLO subaccount equity")
    args = parser.parse_args()

    load_env_file()
    config = YoloConfig.from_env()
    if args.buffer_mode:
        config = replace(config, buffer_mode=args.buffer_mode)

    try:
        _ = config.normalized_network
        mode = config.normalized_execution_mode
        _ = config.normalized_direction_mode
    except ValueError as exc:
        print(f"CONFIG: BLOCKED — {exc}")
        raise SystemExit(2) from exc

    if args.execution_status:
        print("YOLO EXECUTION STATUS")
        print(f"network:           {config.normalized_network}")
        print(f"execution mode:    {config.normalized_execution_mode}")
        print(f"direction mode:    {config.normalized_direction_mode}")
        print(f"live enabled:      {config.live_trading_enabled}")
        print(f"API key present:   {bool(config.hl_api_wallet_private_key)}")
        print(f"max reprices:      {config.execution_max_reprices}")
        print(f"reprice wait:      {config.execution_reprice_seconds:g}s")
        print(f"dead-man timeout:  {config.execution_deadman_seconds}s")
        if config.hl_api_wallet_private_key:
            try:
                read_client = _hl_client(config)
                execution_client = _execution_client(config)
                role = validate_api_wallet(
                    read_client=read_client,
                    execution_client=execution_client,
                    master_account_address=config.hl_account_address,
                )
                print(f"API wallet:        {execution_client.signer_address}")
                print(f"wallet role:       {role.get('role')} — AUTHORIZED")
            except (LiveExecutionError, LiveDependencyError, RuntimeError, ValueError) as exc:
                print(f"wallet readiness:  BLOCKED — {exc}")
        else:
            print("wallet readiness:  NOT CONFIGURED")
        latest = PreLiveLedger(config.sqlite_path).latest_run()
        if latest is not None:
            session = PreLiveLedger(config.sqlite_path).execution_session(int(latest["id"]))
            print(f"latest run:        {latest['id']} ({latest['signal_date']})")
            print(f"latest execution:  {session['status'] if session else 'never transmitted'}")
        return

    if args.archive_status:
        for row in SignalArchive(config.sqlite_path).recent_pulls():
            print(row["id"], row["pulled_at_utc"], row["endpoint"], row["status_code"], row["validation_status"], row["payload_date"] or "-")
        return

    if args.health_status:
        row = PreLiveLedger(config.sqlite_path).latest_run()
        if row is None:
            print("YOLO HEALTH: no persisted runs yet")
            return
        print(f"YOLO HEALTH: {row['health_status']}")
        print(f"run id:            {row['id']}")
        print(f"created:           {row['created_at_utc']}")
        print(f"signal date:       {row['signal_date']}")
        print(f"network:           {row['network']}")
        print(f"execution mode:    {row['execution_mode']}")
        print(f"direction mode:    {row.get('direction_mode', 'long_short')}")
        print(f"effective nominal: ${row['effective_nominal_usd']:,.2f}")
        print(f"account value:     ${row['account_value_usd']:,.2f}")
        print(f"risk approved:     {bool(row['risk_approved'])}")
        print(f"order intents:     {row['intent_count']}")
        print(f"transmitted:       {bool(row['transmitted'])}")
        session = PreLiveLedger(config.sqlite_path).execution_session(int(row["id"]))
        if session is not None:
            print(f"execution status:  {session['status']}")
        return

    if args.cashflow_status:
        events = CashFlowLedger(config.sqlite_path).recent_events()
        if not events:
            print("No detected cash-flow ledger events stored yet.")
        for row in events:
            print(row["time_ms"], row["event_type"], row["classification"], f"{row['flow_usd']:+.2f}", "APPLIED" if row["applied"] else "NOT_APPLIED")
        return

    if args.account_status:
        try:
            exchange = _fetch_exchange_only(config)
        except (RuntimeError, ValueError) as exc:
            print(f"ACCOUNT STATUS: BLOCKED — {exc}")
            raise SystemExit(2) from exc
        print("HYPERLIQUID ACCOUNT")
        print(f"network:          {config.normalized_network}")
        print(f"mode:             {exchange.account_mode}")
        print(f"account value:    ${exchange.account_value_usd:,.2f}")
        print(f"value source:     {exchange.account_value_source}")
        print(f"gross notional:   ${exchange.total_notional_usd:,.2f}")
        print(f"position margin:  ${exchange.total_margin_used_usd:,.2f}")
        if exchange.current_margin_ratio is not None:
            print(f"current margin ratio: {exchange.current_margin_ratio:.2%}")
        print(f"open positions:   {len(exchange.positions)}")
        for ticker, position in sorted(exchange.positions.items()):
            print(f"  {ticker:<8} qty {position.quantity:>14.8f}  value ${position.value_usd:>12,.2f}")
        return

    if args.resume_execution is not None:
        if mode != "execute":
            print("EXECUTION RESUME: BLOCKED — set YOLO_EXECUTION_MODE=execute first")
            raise SystemExit(2)
        try:
            read_client, execution_client = _require_live_execution_ready(config)
            prelive = PreLiveLedger(config.sqlite_path)
            result = execute_run(
                run_id=args.resume_execution,
                config=config,
                read_client=read_client,
                execution_client=execution_client,
                ledger=prelive,
                archive=SignalArchive(config.sqlite_path),
                require_existing_session=True,
            )
        except (LiveExecutionError, LiveDependencyError, RuntimeError, ValueError) as exc:
            print(f"EXECUTION RESUME: BLOCKED — {exc}")
            raise SystemExit(2) from exc
        _print_execution_result(result)
        return

    ledger = SizingLedger(config.sqlite_path)

    if args.rebase_compounding:
        try:
            _require_compound_subaccount(config)
            exchange = _fetch_exchange_only(config)
            ledger.rebase(account_value_usd=exchange.account_value_usd, base_nominal_usd=config.nominal_usd)
            cash_ledger = CashFlowLedger(config.sqlite_path)
            acknowledged = cash_ledger.acknowledge_manual_events()
            cash_ledger.initialize_cursor(int(exchange.pulled_at_utc.timestamp() * 1000))
        except (RuntimeError, ValueError, SizingError) as exc:
            print(f"COMPOUNDING: BLOCKED — {exc}")
            raise SystemExit(2) from exc
        print("COMPOUNDING BASELINE REBASED")
        print(f"starting equity: ${exchange.account_value_usd:,.2f}")
        print(f"base nominal:    ${config.nominal_usd:,.2f}")
        print("cash-flow cursor reset to the current account snapshot")
        if acknowledged:
            print(f"manual-review ledger events acknowledged by rebase: {acknowledged}")
        return

    if args.record_flow is not None:
        try:
            _require_compound_subaccount(config)
            exchange = _fetch_exchange_only(config)
            ledger.record_external_flow(amount_usd=args.record_flow, current_account_value_usd=exchange.account_value_usd)
        except (RuntimeError, ValueError, SizingError) as exc:
            print(f"CASH FLOW: BLOCKED — {exc}")
            raise SystemExit(2) from exc
        kind = "DEPOSIT" if args.record_flow > 0 else "WITHDRAWAL"
        print(f"{kind} RECORDED: ${abs(args.record_flow):,.2f}")
        print("Manual flow entry remains an admin fallback; normal runs auto-sync recognized Hyperliquid ledger flows.")
        return

    if args.sizing_status:
        try:
            exchange = _fetch_exchange_only(config)
        except (RuntimeError, ValueError) as exc:
            print(f"SIZING STATUS: BLOCKED — {exc}")
            raise SystemExit(2) from exc
        status = ledger.status(current_account_value_usd=exchange.account_value_usd)
        print(f"initialized:       {status.initialized}")
        print(f"current equity:    ${exchange.account_value_usd:,.2f}")
        if status.initialized:
            print(f"starting equity:   ${status.initial_account_value_usd:,.2f}")
            print(f"base nominal:      ${status.base_nominal_usd:,.2f}")
            print(f"strategy units:    {status.units:,.6f}")
            print(f"NAV / unit:        {status.nav_per_unit:.6f}")
            print(f"raw multiplier:    {status.multiplier:.4f}x")
            print(f"raw nominal:       ${status.latest_effective_nominal_usd:,.2f}")
        return

    if args.live_data or args.wait_for_signal:
        execution_client = None
        validated_read_client = None
        if mode == "execute":
            try:
                validated_read_client, execution_client = _require_live_execution_ready(config)
            except (LiveExecutionError, LiveDependencyError, RuntimeError, ValueError) as exc:
                print(f"LIVE EXECUTION: BLOCKED — {exc}")
                raise SystemExit(2) from exc
        try:
            if args.wait_for_signal:
                live = wait_for_live_inputs(config, expected_date=args.expected_date)
            else:
                live = fetch_live_inputs(config, expected_date=args.expected_date)
            client = validated_read_client or _hl_client(config)
            cashflow_result = _cashflow_sync(config, ledger, live.exchange, client)
            sizing = ledger.decision(account_value_usd=live.exchange.account_value_usd, config=config)
        except (SignalValidationError, SizingError, CashFlowSyncError, RuntimeError, ValueError) as exc:
            print(f"PRE-LIVE CHECK: BLOCKED — {exc}")
            raise SystemExit(2) from exc

        effective_config = replace(config, nominal_usd=sizing.effective_nominal_usd)
        targets = build_targets(live.signals, effective_config)
        plan = plan_trades(
            targets,
            live.exchange.positions,
            effective_config,
            mark_prices=live.exchange.mark_prices,
            size_decimals={ticker: spec.size_decimals for ticker, spec in live.exchange.markets.items()},
        )
        risk = summarize_post_trade(plan, effective_config, account_collateral_usd=live.exchange.account_value_usd)
        persisted, _, _ = _persist_and_print_prelive(
            config=effective_config,
            live=live,
            sizing=sizing,
            targets=targets,
            plan=plan,
            risk=risk,
            cashflow_result=cashflow_result,
        )
        if mode == "execute":
            try:
                result = execute_run(
                    run_id=persisted.run_id,
                    config=effective_config,
                    read_client=client,
                    execution_client=execution_client,
                    ledger=PreLiveLedger(effective_config.sqlite_path),
                    archive=SignalArchive(effective_config.sqlite_path),
                )
            except (LiveExecutionError, RuntimeError, ValueError) as exc:
                print(f"LIVE EXECUTION: ATTENTION — {exc}")
                print(f"Resume after inspection with: ./bin/yolo --resume-execution {persisted.run_id}")
                raise SystemExit(2) from exc
            _print_execution_result(result)
        return

    signals, positions = _load_fixture(args.fixture)
    targets = build_targets(signals, config)
    plan = plan_trades(targets, positions, config)
    risk = summarize_post_trade(plan, config)
    _print_plan(targets, plan, risk)


if __name__ == "__main__":
    main()
