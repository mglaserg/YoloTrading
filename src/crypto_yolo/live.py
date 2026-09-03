from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timezone
from typing import Any

from .archive import SignalArchive
from .config import YoloConfig
from .models import ExchangeSnapshot, SignalRow
from .providers import HyperliquidReadOnlyClient, RobotWealthClient
from .validation import SignalValidationError, validate_signals


@dataclass(frozen=True)
class LiveInputs:
    signals: list[SignalRow]
    signal_date: date
    signal_snapshot_id: int
    exchange: ExchangeSnapshot


def _payload_date(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    rows = payload.get("data")
    if not isinstance(rows, list):
        return None
    dates = {str(row.get("date")) for row in rows if isinstance(row, dict) and row.get("date")}
    return next(iter(dates)) if len(dates) == 1 else None


def fetch_live_inputs(
    config: YoloConfig,
    *,
    expected_date: date | None = None,
) -> LiveInputs:
    """Pull, archive, validate, then return live RW + Hyperliquid inputs.

    Raw RW responses are written to SQLite before any parsing or staleness checks.
    Any validation failure blocks the rebalance but leaves a forensic record behind.
    """
    archive = SignalArchive(config.sqlite_path)
    rw = RobotWealthClient(
        api_key=config.rw_api_key,
        base_url=config.rw_base_url,
        timeout_seconds=config.request_timeout_seconds,
    )

    weights_response = rw.fetch_weights()
    weights_pull_id = archive.record_pull(weights_response, _payload_date(weights_response.payload))
    if weights_response.status_code != 200:
        message = f"yolo/weights HTTP {weights_response.status_code}"
        archive.mark_pull_validation(weights_pull_id, "rejected", message)
        raise SignalValidationError(message)

    vols_response = rw.fetch_volatilities()
    vols_pull_id = archive.record_pull(vols_response, _payload_date(vols_response.payload))
    if vols_response.status_code != 200:
        message = f"yolo/volatilities HTTP {vols_response.status_code}"
        archive.mark_pull_validation(vols_pull_id, "rejected", message)
        raise SignalValidationError(message)

    pulled_at = max(weights_response.pulled_at_utc, vols_response.pulled_at_utc)
    signals: list[SignalRow] = []
    signal_date = expected_date or pulled_at.astimezone(timezone.utc).date()
    try:
        signals = rw.parse_signals(weights_response.payload, vols_response.payload)
        signal_date = validate_signals(
            signals,
            expected_date=expected_date or pulled_at.astimezone(timezone.utc).date(),
            expected_universe_size=config.expected_universe_size,
        )
    except (ValueError, SignalValidationError) as exc:
        message = str(exc)
        archive.mark_pull_validation(weights_pull_id, "rejected", message)
        archive.mark_pull_validation(vols_pull_id, "rejected", message)
        archive.record_signal_snapshot(
            pulled_at_utc=pulled_at.isoformat(),
            signal_date=signal_date.isoformat(),
            signals=signals,
            weights_pull_id=weights_pull_id,
            volatilities_pull_id=vols_pull_id,
            validation_status="rejected",
            validation_message=message,
        )
        raise SignalValidationError(message) from exc

    archive.mark_pull_validation(weights_pull_id, "accepted")
    archive.mark_pull_validation(vols_pull_id, "accepted")
    snapshot_id = archive.record_signal_snapshot(
        pulled_at_utc=pulled_at.isoformat(),
        signal_date=signal_date.isoformat(),
        signals=signals,
        weights_pull_id=weights_pull_id,
        volatilities_pull_id=vols_pull_id,
        validation_status="accepted",
    )

    hl = HyperliquidReadOnlyClient(
        user_address=config.hyperliquid_user_address,
        api_url=config.hyperliquid_api_url,
        timeout_seconds=config.request_timeout_seconds,
    )
    exchange = hl.fetch_account_snapshot()
    return LiveInputs(signals, signal_date, snapshot_id, exchange)
