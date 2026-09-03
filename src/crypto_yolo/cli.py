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
from .risk import summarize_post_trade
from .validation import SignalValidationError


def _load_fixture(path: Path) -> tuple[list[SignalRow], dict[str, Position]]:
    data = json.loads(path.read_text())
    signals = [SignalRow(**row) for row in data["signals"]]
    positions = {row["ticker"]: Position(**row) for row in data.get("positions", [])}
    return signals, positions


def _print_plan(targets, plan, risk, *, signal_date: str | None = None, exchange=None) -> None:
    if signal_date:
        print(f"RW signal date: {signal_date}")
        print("Signal status: CURRENT")
    if exchange is not None:
        print(f"Hyperliquid account value: ${exchange.account_value_usd:,.2f}")
        print(f"Hyperliquid margin used:  ${exchange.total_margin_used_usd:,.2f}")
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


def main() -> None:
    parser = argparse.ArgumentParser(description="Preview a Crypto YOLO rebalance")
    parser.add_argument("--fixture", type=Path, default=Path("examples/sample_snapshot.json"))
    parser.add_argument("--live-data", action="store_true", help="Pull real RW + Hyperliquid read-only data")
    parser.add_argument("--expected-date", type=date.fromisoformat, default=None)
    parser.add_argument("--buffer-mode", choices=["edge", "target"], default=None)
    parser.add_argument("--archive-status", action="store_true", help="Show the most recent archived RW pulls")
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

    if args.live_data:
        try:
            live = fetch_live_inputs(config, expected_date=args.expected_date)
        except SignalValidationError as exc:
            print(f"RISK CHECK: BLOCKED — {exc}")
            raise SystemExit(2) from exc
        targets = build_targets(live.signals, config)
        plan = plan_trades(
            targets,
            live.exchange.positions,
            config,
            mark_prices=live.exchange.mark_prices,
            size_decimals={ticker: spec.size_decimals for ticker, spec in live.exchange.markets.items()},
        )
        risk = summarize_post_trade(
            plan,
            config,
            account_collateral_usd=live.exchange.account_value_usd,
        )
        SignalArchive(config.sqlite_path).mark_snapshot_planned(live.signal_snapshot_id)
        _print_plan(
            targets,
            plan,
            risk,
            signal_date=live.signal_date.isoformat(),
            exchange=live.exchange,
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
