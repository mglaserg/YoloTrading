from dataclasses import dataclass


@dataclass(frozen=True)
class SignalRow:
    ticker: str
    price: float
    momentum: float
    trend: float
    carry: float
    ewvol: float


@dataclass(frozen=True)
class Position:
    ticker: str
    quantity: float
    price: float

    @property
    def value_usd(self) -> float:
        return self.quantity * self.price


@dataclass(frozen=True)
class TargetRow:
    ticker: str
    price: float
    momentum: float
    trend: float
    carry: float
    ewvol: float
    raw_weight: float
    vol_scaled_weight: float
    final_weight: float
    target_value_usd: float
    target_quantity: float


@dataclass(frozen=True)
class TradePlanRow:
    ticker: str
    price: float
    target_weight: float
    current_weight: float
    target_quantity: float
    current_quantity: float
    trade_quantity: float
    trade_value_usd: float
    post_trade_weight: float
    within_buffer_before: bool
    within_buffer_after: bool
