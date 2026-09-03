from __future__ import annotations

from .buffer import buffered_destination, relative_buffer_bounds
from .config import YoloConfig
from .models import Position, TargetRow, TradePlanRow


def _round_quantity(quantity: float, decimals: int | None) -> float:
    return quantity if decimals is None else round(quantity, decimals)


def plan_trades(
    targets: list[TargetRow],
    current_positions: dict[str, Position],
    config: YoloConfig,
    *,
    mark_prices: dict[str, float] | None = None,
    size_decimals: dict[str, int] | None = None,
) -> list[TradePlanRow]:
    """Create a buffer-aware, exchange-precision-aware dry-run trade plan.

    RW arrival_price remains attached for TCA/audit purposes. Current Hyperliquid
    marks value positions and determine quantities when available. Optional
    size_decimals rounds the proposed delta to Hyperliquid's asset precision.
    """
    mark_prices = mark_prices or {}
    size_decimals = size_decimals or {}
    rows: list[TradePlanRow] = []
    target_by_ticker = {t.ticker: t for t in targets}

    for t in targets:
        p = current_positions.get(t.ticker, Position(t.ticker, 0.0, t.price))
        price = mark_prices.get(t.ticker, p.price if p.price > 0 else t.price)
        if price <= 0:
            price = t.price
        current_value = p.quantity * price
        current_weight = current_value / config.nominal_usd if config.nominal_usd else 0.0

        destination_weight, inside_before = buffered_destination(
            current_weight=current_weight,
            target_weight=t.final_weight,
            buffer=config.trade_buffer,
            mode=config.buffer_mode,
        )
        destination_value = destination_weight * config.nominal_usd
        desired_destination_qty = 0.0 if price == 0 else destination_value / price
        theoretical_target_qty = 0.0 if price == 0 else t.target_value_usd / price
        trade_qty = desired_destination_qty - p.quantity
        trade_qty = _round_quantity(trade_qty, size_decimals.get(t.ticker))
        post_qty = p.quantity + trade_qty
        post_weight = post_qty * price / config.nominal_usd if config.nominal_usd else 0.0
        trade_value = trade_qty * price
        lo, hi = relative_buffer_bounds(t.final_weight, config.trade_buffer)

        # Exchange rounding can leave the destination microscopically beyond an edge.
        one_lot_weight = 0.0
        decimals = size_decimals.get(t.ticker)
        if decimals is not None and config.nominal_usd:
            one_lot_weight = (10 ** (-decimals)) * price / config.nominal_usd
        tolerance = max(1e-12, one_lot_weight + 1e-12)
        inside_after = lo - tolerance <= post_weight <= hi + tolerance

        rows.append(
            TradePlanRow(
                ticker=t.ticker,
                price=price,
                target_weight=t.final_weight,
                current_weight=current_weight,
                target_quantity=theoretical_target_qty,
                current_quantity=p.quantity,
                trade_quantity=trade_qty,
                trade_value_usd=trade_value,
                post_trade_weight=post_weight,
                within_buffer_before=inside_before,
                within_buffer_after=inside_after,
                arrival_price=t.price,
                is_universe_exit=False,
            )
        )

    if config.close_non_universe_positions:
        for ticker in sorted(set(current_positions) - set(target_by_ticker)):
            p = current_positions[ticker]
            price = mark_prices.get(ticker, p.price)
            if price <= 0:
                raise ValueError(f"cannot price non-universe position {ticker} for controlled exit")
            current_weight = p.quantity * price / config.nominal_usd if config.nominal_usd else 0.0
            trade_qty = _round_quantity(-p.quantity, size_decimals.get(ticker))
            post_qty = p.quantity + trade_qty
            post_weight = post_qty * price / config.nominal_usd if config.nominal_usd else 0.0
            rows.append(
                TradePlanRow(
                    ticker=ticker,
                    price=price,
                    target_weight=0.0,
                    current_weight=current_weight,
                    target_quantity=0.0,
                    current_quantity=p.quantity,
                    trade_quantity=trade_qty,
                    trade_value_usd=trade_qty * price,
                    post_trade_weight=post_weight,
                    within_buffer_before=False,
                    within_buffer_after=abs(post_weight) < 1e-9,
                    arrival_price=None,
                    is_universe_exit=True,
                )
            )

    return rows
