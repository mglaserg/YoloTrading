from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import date
import json
from pathlib import Path

from .archive import SignalArchive
from .config import YoloConfig
from .env import load_env_file
from .live import fetch_live_inputs
from .models import Position, SignalRow
from .planner import plan_trades
from .portfolio import build_targets
from .providers import HyperliquidReadOnlyClient
from .risk import summarize_post_trade
from .sizing import SizingDecision, SizingError, SizingLedger
from .validation import SignalValidationError


def _load_fixture(path: Path) -> tuple[list[SignalRow], dict[str, Position]]:
    data = json.loads(path.read_text())
    signals = [SignalRow(**row) for row in data["signals"]]
    positions = {row["ticker"]: Position(**row) for row in data.get("positions", [])}
    return signals, positions


def _fetch_exchange_only(config: YoloConfig):
    return HyperliquidReadOnlyClient(
        user_address=config.hyperliquid_user_address,
        api_url=config.hyperliquid_api_url,
        timeout_seconds=config.request_timeout_seconds,
    ).fetch_account_snapshot()


def _print_sizing(sizing: SizingDecision) -> None:
    print("\nSIZING")
    print(f"mode:             {sizing.mode.upper()}")
    print(f"base nominal:     ${sizing.base_nominal_usd:,.2f}")
    if sizing.mode == "compound":
        print(f"account value:    ${sizing.account_value_usd:,.2f}")
        print(f"starting equity:  ${sizing.initial_account_value_usd:,.2f}")
        print(f"NAV / unit:       {sizing.nav_per_unit:.6f}")
        print(f"raw multiplier:   {sizing.raw_multiplier:.4f}x")
        if sizing.clipped:
            print(f"applied mult.:    {sizing.applied_multiplier:.4f}x  (CLIPPED)")
        else:
            print(f"applied mult.:    {sizing.applied_multiplier:.4f}x")
        if sizing.initialized_now:
            print("baseline:          INITIALIZED TODAY")
    print(f"effective nominal:${sizing.effective_nominal_usd:,.2f}")


def _print_plan(
    targets,
    plan,
    risk,
    *,
    signal_date: str | None = None,
    exchange=None,
    sizing: SizingDecision | None = None,
) -> None:
    if signal_date:
        print(f"RW signal date: {signal_date}")
        print("Signal status: CURRENT")
    if exchange is not None:
        print(f"Hyperliquid account value: ${exchange.account_value_usd:,.2f}")
        print(f"Hyperliquid margin used:  ${exchange.total_margin_used_usd:,.2f}")
    if sizing is not None:
        _print_sizing(sizing)
    print()
    print(
        "ticker  mom    trend  carry   ewvol  target_w  current_w  trade_qty    trade_usd  note"
    )
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
    if risk.reasons:
        for reason in risk.reasons:
            print(f"- {reason}")
    print("* trade dollars divided by post-trade gross dollars")


def _require_compound_subaccount(config: YoloConfig) -> None:
    if config.require_dedicated_subaccount_for_compound and not config.hl_subaccount_address:
        raise SizingError(
            "compounding is guarded to a dedicated YOLO subaccount; set HL_YOLO_SUBACCOUNT_ADDRESS "
            "or explicitly set YOLO_REQUIRE_DEDICATED_SUBACCOUNT_FOR_COMPOUND=false"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Preview a Crypto YOLO rebalance")
    parser.add_argument("--fixture", type=Path, default=Path("examples/sample_snapshot.json"))
    parser.add_argument("--live-data", action="store_true", help="Pull real RW + Hyperliquid read-only data")
    parser.add_argument("--expected-date", type=date.fromisoformat, default=None)
    parser.add_argument("--buffer-mode", choices=["edge", "target"], default=None)
    parser.add_argument("--archive-status", action="store_true", help="Show the most recent archived RW pulls")
    parser.add_argument(
        "--sizing-status",
        action="store_true",
        help="Show the current unitized compounding state using live Hyperliquid equity",
    )
    parser.add_argument(
        "--record-flow",
        type=float,
        default=None,
        metavar="USD",
        help="Record an external YOLO subaccount cash flow after it posts (+deposit, -withdrawal)",
    )
    parser.add_argument(
        "--rebase-compounding",
        action="store_true",
        help="Reset the compounding baseline to current YOLO subaccount equity and YOLO_NOMINAL_USD",
    )
    args = parser.parse_args()

    load_env_file()
    config = YoloConfig.from_env()
    if args.buffer_mode:
        config = replace(config, buffer_mode=args.buffer_mode)

    if args.archive_status:
        archive = SignalArchive(config.sqlite_path)
        for row in archive.recent_pulls():
            print(
                row["id"], row["pulled_at_utc"], row["endpoint"], row["status_code"],
                row["validation_status"], row["payload_date"] or "-"
            )
        return

    ledger = SizingLedger(config.sqlite_path)

    if args.rebase_compounding:
        try:
            _require_compound_subaccount(config)
            exchange = _fetch_exchange_only(config)
            ledger.rebase(
                account_value_usd=exchange.account_value_usd,
                base_nominal_usd=config.nominal_usd,
            )
        except (RuntimeError, ValueError, SizingError) as exc:
            print(f"COMPOUNDING: BLOCKED — {exc}")
            raise SystemExit(2) from exc
        print("COMPOUNDING BASELINE REBASED")
        print(f"starting equity: ${exchange.account_value_usd:,.2f}")
        print(f"base nominal:    ${config.nominal_usd:,.2f}")
        return

    if args.record_flow is not None:
        try:
            _require_compound_subaccount(config)
            exchange = _fetch_exchange_only(config)
            ledger.record_external_flow(
                amount_usd=args.record_flow,
                current_account_value_usd=exchange.account_value_usd,
            )
        except (RuntimeError, ValueError, SizingError) as exc:
            print(f"CASH FLOW: BLOCKED — {exc}")
            raise SystemExit(2) from exc
        kind = "DEPOSIT" if args.record_flow > 0 else "WITHDRAWAL"
        print(f"{kind} RECORDED: ${abs(args.record_flow):,.2f}")
        print(f"current YOLO equity: ${exchange.account_value_usd:,.2f}")
        print("This changes strategy units, not performance.")
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

    if args.live_data:
        try:
            live = fetch_live_inputs(config, expected_date=args.expected_date)
            sizing = ledger.decision(
                account_value_usd=live.exchange.account_value_usd,
                config=config,
            )
        except (SignalValidationError, SizingError) as exc:
            print(f"RISK CHECK: BLOCKED — {exc}")
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
        risk = summarize_post_trade(
            plan,
            effective_config,
            account_collateral_usd=live.exchange.account_value_usd,
        )
        SignalArchive(config.sqlite_path).mark_snapshot_planned(live.signal_snapshot_id)
        _print_plan(
            targets,
            plan,
            risk,
            signal_date=live.signal_date.isoformat(),
            exchange=live.exchange,
            sizing=sizing,
        )
        print("\nEXECUTION: DRY RUN — no orders can be submitted in this version")
        return

    signals, positions = _load_fixture(args.fixture)
    targets = build_targets(signals, config)
    plan = plan_trades(targets, positions, config)
    risk = summarize_post_trade(plan, config)
    _print_plan(targets, plan, risk)


if __name__ == "__main__":
    main()
