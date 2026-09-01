from dataclasses import dataclass
import os


def _f(name: str, default: float) -> float:
    return float(os.getenv(name, default))


@dataclass(frozen=True)
class YoloConfig:
    nominal_usd: float = 50_000.0
    account_collateral_usd: float = 20_000.0
    margin_required: float = 0.10
    momentum_multiplier: float = 1.0
    trend_multiplier: float = 1.0
    carry_multiplier: float = 1.0
    trade_buffer: float = 0.05
    buffer_mode: str = "edge"
    max_asset_weight: float = 0.25
    max_gross_weight: float = 1.0
    max_margin_utilization: float = 0.60

    @classmethod
    def from_env(cls) -> "YoloConfig":
        return cls(
            nominal_usd=_f("YOLO_NOMINAL_USD", 50_000),
            account_collateral_usd=_f("YOLO_ACCOUNT_COLLATERAL_USD", 20_000),
            margin_required=_f("YOLO_MARGIN_REQUIRED", 0.10),
            momentum_multiplier=_f("YOLO_MOMENTUM_MULTIPLIER", 1.0),
            trend_multiplier=_f("YOLO_TREND_MULTIPLIER", 1.0),
            carry_multiplier=_f("YOLO_CARRY_MULTIPLIER", 1.0),
            trade_buffer=_f("YOLO_TRADE_BUFFER", 0.05),
            buffer_mode=os.getenv("YOLO_BUFFER_MODE", "edge"),
            max_asset_weight=_f("YOLO_MAX_ASSET_WEIGHT", 0.25),
            max_gross_weight=_f("YOLO_MAX_GROSS_WEIGHT", 1.0),
            max_margin_utilization=_f("YOLO_MAX_MARGIN_UTILIZATION", 0.60),
        )
