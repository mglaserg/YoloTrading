from __future__ import annotations

from dataclasses import dataclass

from .buffer import buffer_bounds
from .models import Position, TargetRow


@dataclass(frozen=True)
class VerificationRow:
    ticker: str
    actual_weight: float
    target_weight: float
    within_buffer: bool


def verify_post_trade_state(
    *,
    targets: list[TargetRow],
    positions: dict[str, Position],
    mark_prices: dict[str, float],
    nominal_usd: float,
    buffer: float,
    buffer_basis: str = "absolute_weight",
) -> list[VerificationRow]:
    out: list[VerificationRow] = []
    for target in targets:
        position = positions.get(target.ticker)
        qty = 0.0 if position is None else position.quantity
        px = mark_prices.get(target.ticker, target.price)
        actual = qty * px / nominal_usd if nominal_usd else 0.0
        lo, hi = buffer_bounds(target.final_weight, buffer, buffer_basis)
        out.append(VerificationRow(target.ticker, actual, target.final_weight, lo - 1e-9 <= actual <= hi + 1e-9))
    return out
