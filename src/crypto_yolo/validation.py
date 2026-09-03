from __future__ import annotations

from datetime import date, datetime, timezone
import math
from typing import Any

from .models import SignalRow


class SignalValidationError(RuntimeError):
    pass


def normalize_signal_date(value: Any) -> date:
    if value is None:
        raise SignalValidationError("RW signal payload is missing date")
    text = str(value).strip()
    if not text:
        raise SignalValidationError("RW signal payload has an empty date")

    # Handle YYYY-MM-DD, ISO timestamps, and common trailing Z timestamps.
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        pass
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed.date()
    except ValueError as exc:
        raise SignalValidationError(f"unparseable RW signal date: {text!r}") from exc


def validate_signals(
    signals: list[SignalRow],
    *,
    expected_date: date | None = None,
    expected_universe_size: int | None = 10,
) -> date:
    """Fail closed unless a complete, current, numerically sane YOLO snapshot is present."""
    if not signals:
        raise SignalValidationError("RW returned no YOLO signals")

    if expected_universe_size is not None and len(signals) != expected_universe_size:
        raise SignalValidationError(
            f"expected {expected_universe_size} YOLO assets, received {len(signals)}"
        )

    tickers = [s.ticker for s in signals]
    if len(set(tickers)) != len(tickers):
        raise SignalValidationError("RW signal payload contains duplicate tickers")

    dates = {normalize_signal_date(s.date) for s in signals}
    if len(dates) != 1:
        rendered = ", ".join(sorted(d.isoformat() for d in dates))
        raise SignalValidationError(f"RW signal rows contain multiple dates: {rendered}")
    signal_date = next(iter(dates))

    expected_date = expected_date or datetime.now(timezone.utc).date()
    if signal_date != expected_date:
        raise SignalValidationError(
            f"stale RW signal date {signal_date.isoformat()}; expected {expected_date.isoformat()}"
        )

    for s in signals:
        numeric = {
            "arrival_price": s.arrival_price,
            "momentum": s.momentum,
            "trend": s.trend,
            "carry": s.carry,
            "ewvol": s.ewvol,
        }
        for name, value in numeric.items():
            if not math.isfinite(value):
                raise SignalValidationError(f"{s.ticker} has non-finite {name}")
        if s.arrival_price <= 0:
            raise SignalValidationError(f"{s.ticker} has non-positive arrival_price")
        if s.ewvol <= 0:
            raise SignalValidationError(f"{s.ticker} has non-positive ewvol")

    return signal_date
