"""External provider interfaces and read-only HTTP clients."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import math
import re
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .models import (
    ExchangeSnapshot,
    MarketSpec,
    Position,
    RawApiResponse,
    SignalRow,
)


class RobotWealthProvider(Protocol):
    def fetch_yolo_signals(self) -> list[SignalRow]: ...


class ExchangeProvider(Protocol):
    def fetch_positions(self) -> dict[str, Position]: ...


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_float(value: Any, field: str) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid {field}: {value!r}") from exc
    if not math.isfinite(out):
        raise ValueError(f"non-finite {field}: {value!r}")
    return out


def normalize_ticker(value: Any) -> str:
    ticker = str(value).strip().upper()
    ticker = re.sub(r"[-_/](USDT|USDC|USD)$", "", ticker)
    ticker = re.sub(r"(USDT|USDC)$", "", ticker)
    # Do not strip plain USD without a delimiter because some asset symbols can end in USD-like text.
    ticker = re.sub(r"/USD$", "", ticker)
    return ticker


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


@dataclass
class RobotWealthClient:
    api_key: str
    base_url: str = "https://api.robotwealth.com/v1"
    timeout_seconds: float = 15.0

    def _get(self, endpoint: str) -> RawApiResponse:
        if not self.api_key:
            raise RuntimeError("RW_API_KEY is required for live Robot Wealth pulls")
        query = urlencode({"api_key": self.api_key})
        url = f"{self.base_url.rstrip('/')}/{endpoint}?{query}"
        request = Request(url, headers={"Accept": "application/json", "User-Agent": "crypto-yolo/0.3"})
        pulled_at = _utcnow()
        status = 0
        raw = ""
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                status = int(response.status)
                raw = response.read().decode("utf-8")
        except HTTPError as exc:
            status = int(exc.code)
            raw = exc.read().decode("utf-8", errors="replace")
        except URLError as exc:
            raise RuntimeError(f"Robot Wealth transport error for {endpoint}: {exc}") from exc

        try:
            payload = json.loads(raw) if raw else None
        except json.JSONDecodeError:
            payload = None

        # Never expose the key through the archived/displayed URL.
        safe_url = f"{self.base_url.rstrip('/')}/{endpoint}"
        return RawApiResponse(endpoint, safe_url, pulled_at, status, raw, payload)

    def fetch_weights(self) -> RawApiResponse:
        return self._get("yolo/weights")

    def fetch_volatilities(self) -> RawApiResponse:
        return self._get("yolo/volatilities")

    @staticmethod
    def parse_signals(weights_payload: Any, vol_payload: Any) -> list[SignalRow]:
        weights = _extract_data_rows(weights_payload, "yolo/weights")
        vols = _extract_data_rows(vol_payload, "yolo/volatilities")

        vol_by_ticker: dict[str, dict[str, Any]] = {}
        for row in vols:
            ticker = normalize_ticker(row.get("ticker"))
            if not ticker:
                raise ValueError("volatility row missing ticker")
            if ticker in vol_by_ticker:
                raise ValueError(f"duplicate volatility ticker {ticker}")
            vol_by_ticker[ticker] = row

        signals: list[SignalRow] = []
        weight_tickers: set[str] = set()
        for row in weights:
            ticker = normalize_ticker(row.get("ticker"))
            if not ticker:
                raise ValueError("weight row missing ticker")
            if ticker in weight_tickers:
                raise ValueError(f"duplicate weight ticker {ticker}")
            weight_tickers.add(ticker)
            vol_row = vol_by_ticker.get(ticker)
            if vol_row is None:
                raise ValueError(f"missing yolo/volatilities row for {ticker}")

            weight_date = row.get("date")
            vol_date = vol_row.get("date")
            if vol_date not in (None, "") and weight_date not in (None, ""):
                if str(vol_date)[:10] != str(weight_date)[:10]:
                    raise ValueError(
                        f"weights/volatility date mismatch for {ticker}: {weight_date} vs {vol_date}"
                    )

            signals.append(
                SignalRow(
                    ticker=ticker,
                    price=_as_float(row.get("arrival_price"), "arrival_price"),
                    momentum=_as_float(row.get("momentum_megafactor"), "momentum_megafactor"),
                    trend=_as_float(row.get("trend_megafactor"), "trend_megafactor"),
                    carry=_as_float(row.get("carry_megafactor"), "carry_megafactor"),
                    ewvol=_as_float(vol_row.get("ewvol"), "ewvol"),
                    date=str(weight_date) if weight_date is not None else None,
                    combo_weight=(
                        _as_float(row.get("combo_weight"), "combo_weight")
                        if row.get("combo_weight") not in (None, "")
                        else None
                    ),
                )
            )

        extra_vols = set(vol_by_ticker) - weight_tickers
        if extra_vols:
            raise ValueError(
                "volatility payload contains tickers absent from weights: " + ", ".join(sorted(extra_vols))
            )
        return signals


@dataclass
class HyperliquidReadOnlyClient:
    user_address: str
    api_url: str = "https://api.hyperliquid.xyz"
    timeout_seconds: float = 15.0

    def _post_info(self, body: dict[str, Any]) -> Any:
        if not self.user_address:
            raise RuntimeError("HL_ACCOUNT_ADDRESS or HL_YOLO_SUBACCOUNT_ADDRESS is required")
        raw_body = json.dumps(body).encode("utf-8")
        request = Request(
            f"{self.api_url.rstrip('/')}/info",
            data=raw_body,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                raw = response.read().decode("utf-8")
        except (HTTPError, URLError) as exc:
            raise RuntimeError(f"Hyperliquid info request failed: {exc}") from exc
        return json.loads(raw)

    def fetch_account_snapshot(self) -> ExchangeSnapshot:
        pulled_at = _utcnow()
        state = self._post_info({"type": "clearinghouseState", "user": self.user_address})
        meta_ctx = self._post_info({"type": "metaAndAssetCtxs"})
        markets = self._parse_markets(meta_ctx)
        positions = self._parse_positions(state, markets)
        summary = state.get("marginSummary", {}) if isinstance(state, dict) else {}
        return ExchangeSnapshot(
            pulled_at_utc=pulled_at,
            account_value_usd=_as_float(summary.get("accountValue", 0), "accountValue"),
            total_notional_usd=_as_float(summary.get("totalNtlPos", 0), "totalNtlPos"),
            total_margin_used_usd=_as_float(summary.get("totalMarginUsed", 0), "totalMarginUsed"),
            withdrawable_usd=_as_float(state.get("withdrawable", 0), "withdrawable"),
            positions=positions,
            markets=markets,
        )

    @staticmethod
    def _parse_markets(payload: Any) -> dict[str, MarketSpec]:
        if not isinstance(payload, list) or len(payload) < 2:
            raise ValueError("unexpected Hyperliquid metaAndAssetCtxs response")
        meta, contexts = payload[0], payload[1]
        universe = meta.get("universe", []) if isinstance(meta, dict) else []
        if not isinstance(universe, list) or not isinstance(contexts, list):
            raise ValueError("invalid Hyperliquid market metadata")
        markets: dict[str, MarketSpec] = {}
        for instrument, ctx in zip(universe, contexts):
            ticker = normalize_ticker(instrument.get("name"))
            if not ticker:
                continue
            mark = _as_float(ctx.get("markPx"), f"{ticker}.markPx")
            markets[ticker] = MarketSpec(
                ticker=ticker,
                mark_price=mark,
                size_decimals=int(instrument.get("szDecimals", 0)),
            )
        return markets

    @staticmethod
    def _parse_positions(state: Any, markets: dict[str, MarketSpec]) -> dict[str, Position]:
        if not isinstance(state, dict):
            raise ValueError("unexpected Hyperliquid clearinghouseState response")
        out: dict[str, Position] = {}
        for asset_position in state.get("assetPositions", []):
            position = asset_position.get("position", {})
            ticker = normalize_ticker(position.get("coin"))
            if not ticker:
                continue
            quantity = _as_float(position.get("szi", 0), f"{ticker}.szi")
            if abs(quantity) < 1e-18:
                continue
            market = markets.get(ticker)
            if market is not None:
                mark = market.mark_price
            else:
                value = abs(_as_float(position.get("positionValue", 0), f"{ticker}.positionValue"))
                mark = value / abs(quantity) if quantity else 0.0
            out[ticker] = Position(ticker=ticker, quantity=quantity, price=mark)
        return out


def _extract_data_rows(payload: Any, endpoint: str) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        raise ValueError(f"{endpoint} response is not a JSON object")
    rows = payload.get("data")
    if not isinstance(rows, list):
        raise ValueError(f"{endpoint} response missing data list")
    if not all(isinstance(row, dict) for row in rows):
        raise ValueError(f"{endpoint} data contains non-object rows")
    return rows
