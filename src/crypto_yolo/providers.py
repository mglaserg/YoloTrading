"""External provider interfaces.

The endpoint names below come from YOLO Trade Helper v8:
- yolo/weights
- yolo/volatilities

Actual HTTP wiring is intentionally deferred until the user's RW API credentials and
response payload are available. Keeping parsing isolated lets us lock the spreadsheet
math now without guessing at auth/transport details.
"""
from dataclasses import dataclass
from typing import Protocol

from .models import Position, SignalRow


class RobotWealthProvider(Protocol):
    def fetch_yolo_signals(self) -> list[SignalRow]: ...


class ExchangeProvider(Protocol):
    def fetch_positions(self) -> dict[str, Position]: ...


@dataclass
class FixtureRobotWealthProvider:
    rows: list[SignalRow]

    def fetch_yolo_signals(self) -> list[SignalRow]:
        return self.rows


@dataclass
class FixtureExchangeProvider:
    positions: dict[str, Position]

    def fetch_positions(self) -> dict[str, Position]:
        return self.positions
