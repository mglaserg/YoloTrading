from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class SignalRow:
    ticker: str
    price: float
    momentum: float
    trend: float
    carry: float
    ewvol: float
    date: str | None = None
    combo_weight: float | None = None

    @property
    def arrival_price(self) -> float:
        """Robot Wealth's point-in-time arrival price for the signal."""
        return self.price


@dataclass(frozen=True)
class Position:
    ticker: str
    quantity: float
    price: float

    @property
    def value_usd(self) -> float:
        return self.quantity * self.price


@dataclass(frozen=True)
class MarketSpec:
    ticker: str
    mark_price: float
    size_decimals: int


@dataclass(frozen=True)
class ExchangeSnapshot:
    pulled_at_utc: datetime
    account_value_usd: float
    total_notional_usd: float
    total_margin_used_usd: float
    withdrawable_usd: float
    positions: dict[str, Position]
    markets: dict[str, MarketSpec]

    @property
    def mark_prices(self) -> dict[str, float]:
        return {ticker: spec.mark_price for ticker, spec in self.markets.items()}


@dataclass(frozen=True)
class RawApiResponse:
    endpoint: str
    url: str
    pulled_at_utc: datetime
    status_code: int
    raw_text: str
    payload: Any | None


@dataclass(frozen=True)
class SignalSnapshot:
    pulled_at_utc: datetime
    signal_date: str
    signals: list[SignalRow]
    weights_pull_id: int | None = None
    volatilities_pull_id: int | None = None


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
    arrival_price: float | None = None
    is_universe_exit: bool = False
