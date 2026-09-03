from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any

from .sizing import SizingLedger


SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS cashflow_sync_state (
    id INTEGER PRIMARY KEY CHECK (id=1),
    initialized_at_utc TEXT NOT NULL,
    last_time_ms INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS cashflow_events (
    event_key TEXT PRIMARY KEY,
    time_ms INTEGER NOT NULL,
    tx_hash TEXT,
    event_type TEXT NOT NULL,
    classification TEXT NOT NULL,
    flow_usd REAL NOT NULL,
    applied INTEGER NOT NULL DEFAULT 0,
    raw_json TEXT NOT NULL,
    recorded_at_utc TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cashflow_events_time ON cashflow_events(time_ms);
"""


class CashFlowSyncError(RuntimeError):
    pass


@dataclass(frozen=True)
class CashFlowSyncResult:
    initialized_cursor: bool
    events_seen: int
    new_events: int
    applied_events: int
    net_external_flow_usd: float
    manual_review_events: int
    from_time_ms: int
    through_time_ms: int


class CashFlowLedger:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as conn:
            conn.executescript(SCHEMA)

    def _connect(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    @contextmanager
    def _db(self):
        conn = self._connect()
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    def cursor(self) -> int | None:
        with self._db() as conn:
            row = conn.execute("SELECT last_time_ms FROM cashflow_sync_state WHERE id=1").fetchone()
        return None if row is None else int(row["last_time_ms"])

    def unresolved_manual_count(self) -> int:
        with self._db() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM cashflow_events WHERE classification='manual_review' AND applied=0"
            ).fetchone()
        return int(row["n"])

    def acknowledge_manual_events(self) -> int:
        with self._db() as conn:
            cur = conn.execute(
                "UPDATE cashflow_events SET applied=-1 WHERE classification='manual_review' AND applied=0"
            )
        return int(cur.rowcount)

    def initialize_cursor(self, time_ms: int) -> None:
        with self._db() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO cashflow_sync_state(id, initialized_at_utc, last_time_ms) VALUES(1, ?, ?)",
                (self._now_iso(), int(time_ms)),
            )

    def advance_cursor(self, time_ms: int) -> None:
        with self._db() as conn:
            conn.execute("UPDATE cashflow_sync_state SET last_time_ms=? WHERE id=1", (int(time_ms),))

    @staticmethod
    def _event_key(event: dict[str, Any]) -> str:
        raw = json.dumps(event, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def classify(event: dict[str, Any], user_address: str) -> tuple[str, float]:
        delta = event.get("delta") if isinstance(event, dict) else None
        if not isinstance(delta, dict):
            return "manual_review", 0.0
        kind = str(delta.get("type") or "unknown")
        user = str(user_address).lower()

        def amount(field: str = "usdc") -> float:
            try:
                return float(delta.get(field, 0) or 0)
            except (TypeError, ValueError):
                return 0.0

        if kind == "deposit":
            return "external_flow", amount()
        if kind == "withdraw":
            return "external_flow", -amount()
        if kind == "accountClassTransfer":
            usd = amount()
            return "external_flow", usd if bool(delta.get("toPerp")) else -usd
        if kind in {"subAccountTransfer", "internalTransfer"}:
            usd = amount()
            source = str(delta.get("user") or "").lower()
            destination = str(delta.get("destination") or "").lower()
            if destination == user and source != user:
                return "external_flow", usd
            if source == user and destination != user:
                return "external_flow", -usd
            return "manual_review", 0.0
        if kind in {"liquidation", "rewardsClaim", "vaultDistribution"}:
            return "strategy_pnl_or_system", 0.0
        if kind in {
            "spotTransfer", "vaultCreate", "vaultDeposit", "vaultWithdraw",
            "perpDexTransfer", "perpDexClassTransfer",
        }:
            return "manual_review", 0.0
        return "manual_review", 0.0

    @staticmethod
    def _account_value_points(payload: Any) -> list[tuple[int, float]]:
        points: dict[int, float] = {}
        if not isinstance(payload, list):
            return []
        # Prefer perp-specific histories because YOLO compounds the perpetual
        # clearinghouse equity, not unrelated spot balances. Merge multiple
        # windows so a transfer older than one day can still be located.
        for item in payload:
            if not isinstance(item, list) or len(item) != 2:
                continue
            label, history = item
            if not str(label).startswith("perp") or not isinstance(history, dict):
                continue
            for point in history.get("accountValueHistory", []):
                if not isinstance(point, list) or len(point) < 2:
                    continue
                try:
                    ts = int(point[0])
                    value = float(point[1])
                except (TypeError, ValueError):
                    continue
                points[ts] = value
        return sorted(points.items())

    @staticmethod
    def _equity_before(points: list[tuple[int, float]], time_ms: int) -> float | None:
        candidates = [value for ts, value in points if ts < time_ms]
        return candidates[-1] if candidates else None

    def sync(
        self,
        *,
        client,
        sizing_ledger: SizingLedger,
        user_address: str,
        current_account_value_usd: float,
        through_time_ms: int,
        lookback_days: int = 7,
    ) -> CashFlowSyncResult:
        unresolved = self.unresolved_manual_count()
        if unresolved:
            raise CashFlowSyncError(
                f"{unresolved} previously detected Hyperliquid ledger event(s) still need manual review; inspect --cashflow-status and rebase if appropriate"
            )
        cursor = self.cursor()
        if cursor is None:
            # The existing account value is already embedded in the compounding baseline.
            # Starting the cursor "now" avoids double-counting historical deposits.
            self.initialize_cursor(through_time_ms)
            return CashFlowSyncResult(True, 0, 0, 0, 0.0, 0, through_time_ms, through_time_ms)

        start_ms = cursor + 1
        max_gap_ms = max(1, lookback_days) * 86_400_000
        if through_time_ms - start_ms > max_gap_ms:
            raise CashFlowSyncError(
                f"cash-flow sync gap exceeds {max(1, lookback_days)} day(s); refusing to skip ledger history. Inspect account activity and rebase if appropriate"
            )
        events = client.fetch_non_funding_ledger_updates(start_ms, through_time_ms)
        if not isinstance(events, list):
            raise CashFlowSyncError("Hyperliquid non-funding ledger response is not a list")

        new_rows: list[tuple[str, dict[str, Any], str, float]] = []
        manual = 0
        with self._db() as conn:
            for event in sorted(events, key=lambda x: int(x.get("time", 0)) if isinstance(x, dict) else 0):
                if not isinstance(event, dict):
                    manual += 1
                    continue
                key = self._event_key(event)
                exists = conn.execute("SELECT 1 FROM cashflow_events WHERE event_key=?", (key,)).fetchone()
                if exists:
                    continue
                classification, flow = self.classify(event, user_address)
                if classification == "manual_review":
                    manual += 1
                new_rows.append((key, event, classification, flow))

        if manual:
            # Persist all new events first, but do not advance/apply anything until the
            # ambiguous event is reviewed. Fail closed so compounding cannot be distorted.
            self._persist_new(new_rows, applied_keys=set())
            raise CashFlowSyncError(
                f"{manual} new Hyperliquid ledger event(s) need manual review before compound sizing can continue"
            )

        external_rows = [(key, event, flow) for key, event, cls, flow in new_rows if cls == "external_flow"]
        external_keys = {key for key, _, _ in external_rows}
        net_flow = sum(flow for _, _, flow in external_rows)
        if external_rows:
            points = self._account_value_points(client.fetch_portfolio_history())
            if not points:
                self._persist_new(new_rows, applied_keys=set())
                raise CashFlowSyncError(
                    "recognized external flow detected, but Hyperliquid portfolio history had no perp account-value points; refusing to approximate compounding"
                )
            for _, event, flow in external_rows:
                event_time = int(event.get("time", 0))
                pre_flow_equity = self._equity_before(points, event_time)
                if pre_flow_equity is None or pre_flow_equity <= 0:
                    self._persist_new(new_rows, applied_keys=set())
                    raise CashFlowSyncError(
                        f"could not establish positive pre-flow YOLO equity for ledger event at {event_time}; refusing to approximate compounding"
                    )
                sizing_ledger.record_external_flow(
                    amount_usd=flow,
                    current_account_value_usd=pre_flow_equity + flow,
                )
        self._persist_new(new_rows, applied_keys=external_keys)
        self.advance_cursor(through_time_ms)
        return CashFlowSyncResult(
            False,
            len(events),
            len(new_rows),
            len(external_keys),
            net_flow,
            0,
            start_ms,
            through_time_ms,
        )

    def _persist_new(self, rows, *, applied_keys: set[str]) -> None:
        with self._db() as conn:
            for key, event, classification, flow in rows:
                delta = event.get("delta", {}) if isinstance(event, dict) else {}
                conn.execute(
                    """
                    INSERT OR IGNORE INTO cashflow_events(
                        event_key, time_ms, tx_hash, event_type, classification,
                        flow_usd, applied, raw_json, recorded_at_utc
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        key,
                        int(event.get("time", 0)),
                        event.get("hash"),
                        str(delta.get("type") or "unknown"),
                        classification,
                        flow,
                        1 if key in applied_keys else 0,
                        json.dumps(event, sort_keys=True),
                        self._now_iso(),
                    ),
                )

    def recent_events(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._db() as conn:
            rows = conn.execute(
                "SELECT * FROM cashflow_events ORDER BY time_ms DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(row) for row in rows]
