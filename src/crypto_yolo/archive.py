from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Iterable

from .models import RawApiResponse, SignalRow


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS api_pulls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pulled_at_utc TEXT NOT NULL,
    endpoint TEXT NOT NULL,
    url TEXT NOT NULL,
    status_code INTEGER NOT NULL,
    payload_date TEXT,
    response_sha256 TEXT NOT NULL,
    raw_text TEXT NOT NULL,
    validation_status TEXT NOT NULL DEFAULT 'unvalidated',
    validation_message TEXT,
    used_for_trade_plan INTEGER NOT NULL DEFAULT 0,
    used_for_rebalance INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_api_pulls_endpoint_time
ON api_pulls(endpoint, pulled_at_utc);

CREATE TABLE IF NOT EXISTS signal_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pulled_at_utc TEXT NOT NULL,
    signal_date TEXT NOT NULL,
    weights_pull_id INTEGER,
    volatilities_pull_id INTEGER,
    validation_status TEXT NOT NULL,
    validation_message TEXT,
    used_for_trade_plan INTEGER NOT NULL DEFAULT 0,
    used_for_rebalance INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY(weights_pull_id) REFERENCES api_pulls(id),
    FOREIGN KEY(volatilities_pull_id) REFERENCES api_pulls(id)
);

CREATE INDEX IF NOT EXISTS idx_signal_snapshots_date
ON signal_snapshots(signal_date, pulled_at_utc);

CREATE TABLE IF NOT EXISTS signal_rows (
    snapshot_id INTEGER NOT NULL,
    ticker TEXT NOT NULL,
    arrival_price REAL NOT NULL,
    momentum REAL NOT NULL,
    trend REAL NOT NULL,
    carry REAL NOT NULL,
    combo_weight REAL,
    ewvol REAL NOT NULL,
    PRIMARY KEY(snapshot_id, ticker),
    FOREIGN KEY(snapshot_id) REFERENCES signal_snapshots(id)
);
"""


class SignalArchive:
    """SQLite point-in-time archive for all RW pulls and validated signal snapshots."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(SCHEMA)
            self._ensure_columns(conn)

    @staticmethod
    def _ensure_columns(conn: sqlite3.Connection) -> None:
        for table in ("api_pulls", "signal_snapshots"):
            columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
            if "used_for_trade_plan" not in columns:
                conn.execute(
                    f"ALTER TABLE {table} ADD COLUMN used_for_trade_plan INTEGER NOT NULL DEFAULT 0"
                )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def record_pull(self, response: RawApiResponse, payload_date: str | None = None) -> int:
        digest = hashlib.sha256(response.raw_text.encode("utf-8")).hexdigest()
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO api_pulls (
                    pulled_at_utc, endpoint, url, status_code, payload_date,
                    response_sha256, raw_text
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    response.pulled_at_utc.isoformat(),
                    response.endpoint,
                    response.url,
                    response.status_code,
                    payload_date,
                    digest,
                    response.raw_text,
                ),
            )
            return int(cur.lastrowid)

    def mark_pull_validation(self, pull_id: int, status: str, message: str | None = None) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE api_pulls SET validation_status=?, validation_message=? WHERE id=?",
                (status, message, pull_id),
            )

    def record_signal_snapshot(
        self,
        *,
        pulled_at_utc: str,
        signal_date: str,
        signals: Iterable[SignalRow],
        weights_pull_id: int | None,
        volatilities_pull_id: int | None,
        validation_status: str,
        validation_message: str | None = None,
    ) -> int:
        rows = list(signals)
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO signal_snapshots (
                    pulled_at_utc, signal_date, weights_pull_id, volatilities_pull_id,
                    validation_status, validation_message
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    pulled_at_utc,
                    signal_date,
                    weights_pull_id,
                    volatilities_pull_id,
                    validation_status,
                    validation_message,
                ),
            )
            snapshot_id = int(cur.lastrowid)
            if rows:
                conn.executemany(
                    """
                    INSERT INTO signal_rows (
                        snapshot_id, ticker, arrival_price, momentum, trend,
                        carry, combo_weight, ewvol
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            snapshot_id,
                            r.ticker,
                            r.arrival_price,
                            r.momentum,
                            r.trend,
                            r.carry,
                            r.combo_weight,
                            r.ewvol,
                        )
                        for r in rows
                    ],
                )
            return snapshot_id

    def mark_snapshot_planned(self, snapshot_id: int) -> None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT weights_pull_id, volatilities_pull_id FROM signal_snapshots WHERE id=?",
                (snapshot_id,),
            ).fetchone()
            conn.execute(
                "UPDATE signal_snapshots SET used_for_trade_plan=1 WHERE id=?",
                (snapshot_id,),
            )
            if row:
                pull_ids = [row["weights_pull_id"], row["volatilities_pull_id"]]
                conn.executemany(
                    "UPDATE api_pulls SET used_for_trade_plan=1 WHERE id=?",
                    [(pid,) for pid in pull_ids if pid is not None],
                )

    def mark_snapshot_rebalanced(self, snapshot_id: int) -> None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT weights_pull_id, volatilities_pull_id FROM signal_snapshots WHERE id=?",
                (snapshot_id,),
            ).fetchone()
            conn.execute(
                "UPDATE signal_snapshots SET used_for_rebalance=1 WHERE id=?",
                (snapshot_id,),
            )
            if row:
                pull_ids = [row["weights_pull_id"], row["volatilities_pull_id"]]
                conn.executemany(
                    "UPDATE api_pulls SET used_for_rebalance=1 WHERE id=?",
                    [(pid,) for pid in pull_ids if pid is not None],
                )

    def recent_pulls(self, limit: int = 20) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM api_pulls ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(row) for row in rows]
