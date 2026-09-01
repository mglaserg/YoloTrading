from __future__ import annotations

import argparse
import json
from pathlib import Path

from .config import YoloConfig
from .models import Position, SignalRow
from .planner import plan_trades
from .portfolio import build_targets
from .risk import summarize_post_trade


def _load_fixture(path: Path) -> tuple[list[SignalRow], dict[str, Position]]:
    data = json.loads(path.read_text())
    signals = [SignalRow(**row) for row in data["signals"]]
    positions = {
        row["ticker"]: Position(**row)
        for row in data.get("positions", [])
    }
    return signals, positions


def main() -> None:
    parser = argparse.ArgumentParser(description="Preview a Crypto YOLO rebalance")
    parser.add_argument("--fixture", type=Path, default=Path("examples/sample_snapshot.json"))
    parser.add_argument("--buffer-mode", choices=["edge", "target"], default=None)
    args = parser.parse_args()

    config = YoloConfig.from_env()
    if args.buffer_mode:
        config = YoloConfig(**{**config.__dict__, "buffer_mode": args.buffer_mode})

    signals, positions = _load_fixture(args.fixture)
    targets = build_targets(signals, config)
    plan = plan_trades(targets, positions, config)
    risk = summarize_post_trade(plan, config)

    print("ticker  raw_w    vol_w    target_w  current_w  trade_qty    trade_usd")
    for t, p in zip(targets, plan):
        print(
            f"{t.ticker:<6} {t.raw_weight:>7.3f} {t.vol_scaled_weight:>8.3f} "
            f"{t.final_weight:>9.3f} {p.current_weight:>10.3f} "
            f"{p.trade_quantity:>10.6f} {p.trade_value_usd:>11.2f}"
        )

    print("\nRISK")
    print(f"gross weight: {risk.gross_weight:.3f}")
    print(f"net weight:   {risk.net_weight:.3f}")
    print(f"gross USD:    ${risk.gross_usd:,.2f}")
    print(f"net USD:      ${risk.net_usd:,.2f}")
    print(f"margin util:  {risk.estimated_margin_utilization:.1%}")
    print(f"approved:     {risk.approved}")
    if risk.reasons:
        for reason in risk.reasons:
            print(f"- {reason}")


if __name__ == "__main__":
    main()
