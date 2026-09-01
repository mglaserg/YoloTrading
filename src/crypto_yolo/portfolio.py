from __future__ import annotations

from .config import YoloConfig
from .models import SignalRow, TargetRow


def _clip(x: float, lo: float, hi: float) -> float:
    return max(lo, min(x, hi))


def build_targets(signals: list[SignalRow], config: YoloConfig) -> list[TargetRow]:
    """Reproduce the YOLO Trade Helper v8 portfolio construction.

    1. Raw ensemble = (M*m_mult + T*t_mult + C*c_mult) / 3
    2. Inverse-vol scale = raw / ewvol
    3. Clip each asset to +/- max_asset_weight (spreadsheet default 25%)
    4. If gross > max_gross_weight, scale all weights proportionally down
    5. Convert final weights to nominal-dollar and quantity targets
    """
    intermediate: list[tuple[SignalRow, float, float]] = []

    for s in signals:
        raw = (
            s.momentum * config.momentum_multiplier
            + s.trend * config.trend_multiplier
            + s.carry * config.carry_multiplier
        ) / 3.0

        vol_scaled = 0.0 if s.ewvol == 0 else raw / s.ewvol
        vol_scaled = _clip(vol_scaled, -config.max_asset_weight, config.max_asset_weight)
        intermediate.append((s, raw, vol_scaled))

    gross = sum(abs(v) for _, _, v in intermediate)
    scale = 1.0
    if gross > config.max_gross_weight and gross > 0:
        scale = config.max_gross_weight / gross

    out: list[TargetRow] = []
    for s, raw, vol_scaled in intermediate:
        final_weight = vol_scaled * scale
        target_value = final_weight * config.nominal_usd
        target_qty = 0.0 if s.price == 0 else target_value / s.price
        out.append(
            TargetRow(
                ticker=s.ticker,
                price=s.price,
                momentum=s.momentum,
                trend=s.trend,
                carry=s.carry,
                ewvol=s.ewvol,
                raw_weight=raw,
                vol_scaled_weight=vol_scaled,
                final_weight=final_weight,
                target_value_usd=target_value,
                target_quantity=target_qty,
            )
        )
    return out
