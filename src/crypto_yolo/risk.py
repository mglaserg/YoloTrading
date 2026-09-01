from dataclasses import dataclass

from .config import YoloConfig
from .models import TradePlanRow


@dataclass(frozen=True)
class RiskSummary:
    gross_weight: float
    net_weight: float
    gross_usd: float
    net_usd: float
    estimated_margin_usd: float
    estimated_margin_utilization: float
    max_abs_asset_weight: float
    approved: bool
    reasons: tuple[str, ...]


def summarize_post_trade(plan: list[TradePlanRow], config: YoloConfig) -> RiskSummary:
    weights = [r.post_trade_weight for r in plan]
    gross_weight = sum(abs(w) for w in weights)
    net_weight = sum(weights)
    gross_usd = gross_weight * config.nominal_usd
    net_usd = net_weight * config.nominal_usd
    margin = gross_usd * config.margin_required
    margin_util = margin / config.account_collateral_usd if config.account_collateral_usd else float("inf")
    max_asset = max((abs(w) for w in weights), default=0.0)

    reasons: list[str] = []
    if gross_weight > config.max_gross_weight + 1e-9:
        reasons.append("gross weight exceeds configured maximum")
    if max_asset > config.max_asset_weight + 1e-9:
        reasons.append("single-asset weight exceeds configured maximum")
    if margin_util > config.max_margin_utilization:
        reasons.append("estimated margin utilization exceeds configured maximum")
    if not all(r.within_buffer_after for r in plan):
        reasons.append("post-trade portfolio contains buffer violations")

    return RiskSummary(
        gross_weight=gross_weight,
        net_weight=net_weight,
        gross_usd=gross_usd,
        net_usd=net_usd,
        estimated_margin_usd=margin,
        estimated_margin_utilization=margin_util,
        max_abs_asset_weight=max_asset,
        approved=not reasons,
        reasons=tuple(reasons),
    )
