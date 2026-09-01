from __future__ import annotations

from .buffer import buffered_destination, relative_buffer_bounds
from .config import YoloConfig
from .models import Position, TargetRow, TradePlanRow


def plan_trades(
    targets: list[TargetRow],
    current_positions: dict[str, Position],
    config: YoloConfig,
) -> list[TradePlanRow]:
    rows: list[TradePlanRow] = []
    for t in targets:
        p = current_positions.get(t.ticker, Position(t.ticker, 0.0, t.price))
        current_value = p.quantity * t.price
        current_weight = current_value / config.nominal_usd if config.nominal_usd else 0.0

        destination_weight, inside_before = buffered_destination(
            current_weight=current_weight,
            target_weight=t.final_weight,
            buffer=config.trade_buffer,
            mode=config.buffer_mode,
        )
        destination_value = destination_weight * config.nominal_usd
        destination_qty = 0.0 if t.price == 0 else destination_value / t.price
        trade_qty = destination_qty - p.quantity
        trade_value = trade_qty * t.price
        post_weight = destination_weight
        lo, hi = relative_buffer_bounds(t.final_weight, config.trade_buffer)
        inside_after = lo - 1e-12 <= post_weight <= hi + 1e-12

        rows.append(
            TradePlanRow(
                ticker=t.ticker,
                price=t.price,
                target_weight=t.final_weight,
                current_weight=current_weight,
                target_quantity=t.target_quantity,
                current_quantity=p.quantity,
                trade_quantity=trade_qty,
                trade_value_usd=trade_value,
                post_trade_weight=post_weight,
                within_buffer_before=inside_before,
                within_buffer_after=inside_after,
            )
        )
    return rows
