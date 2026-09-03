from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Iterable

from .models import BboQuote, OrderIntent, TradePlanRow


SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS rebalance_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_key TEXT NOT NULL UNIQUE,
    created_at_utc TEXT NOT NULL,
    signal_date TEXT NOT NULL,
    signal_snapshot_id INTEGER NOT NULL,
    signal_fingerprint TEXT NOT NULL,
    network TEXT NOT NULL,
    execution_mode TEXT NOT NULL,
    account_address TEXT NOT NULL,
    effective_nominal_usd REAL NOT NULL,
    risk_approved INTEGER NOT NULL,
    risk_reasons_json TEXT NOT NULL,
    account_value_usd REAL NOT NULL,
    total_margin_used_usd REAL NOT NULL,
    health_status TEXT NOT NULL,
    transmitted INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS trade_intents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    ticker TEXT NOT NULL,
    side TEXT NOT NULL,
    quantity REAL NOT NULL,
    limit_price REAL NOT NULL,
    trade_value_usd REAL NOT NULL,
    tif TEXT NOT NULL,
    reduce_only INTEGER NOT NULL,
    cloid TEXT NOT NULL UNIQUE,
    arrival_price REAL,
    target_weight REAL NOT NULL,
    current_weight REAL NOT NULL,
    destination_weight REAL NOT NULL,
    proposed_tca_bps REAL,
    status TEXT NOT NULL,
    transmitted INTEGER NOT NULL DEFAULT 0,
    exchange_oid INTEGER,
    last_exchange_status TEXT,
    FOREIGN KEY(run_id) REFERENCES rebalance_runs(id)
);
CREATE INDEX IF NOT EXISTS idx_trade_intents_run ON trade_intents(run_id);
CREATE TABLE IF NOT EXISTS execution_locks (
    signal_date TEXT NOT NULL,
    network TEXT NOT NULL,
    account_address TEXT NOT NULL,
    locked_at_utc TEXT NOT NULL,
    run_id INTEGER NOT NULL,
    PRIMARY KEY(signal_date, network, account_address)
);
"""


@dataclass(frozen=True)
class PersistedRun:
    run_id: int
    run_key: str
    intent_count: int


class PreLiveLedger:
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

    @staticmethod
    def make_run_key(*, signal_date: str, signal_fingerprint: str, account: str, network: str, plan: list[TradePlanRow]) -> str:
        state = [
            [p.ticker, round(p.current_quantity, 12), round(p.trade_quantity, 12), round(p.price, 12)]
            for p in sorted(plan, key=lambda x: x.ticker)
        ]
        raw = json.dumps(
            [signal_date, signal_fingerprint, account.lower(), network, state],
            separators=(",", ":"),
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def make_cloid(*, run_key: str, account: str, ticker: str, side: str, attempt: int = 0) -> str:
        raw = f"YOLO|{run_key}|{account.lower()}|{ticker}|{side}|{attempt}"
        return "0x" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]

    def persist_run(
        self,
        *,
        run_key: str,
        signal_date: str,
        signal_snapshot_id: int,
        signal_fingerprint: str,
        network: str,
        execution_mode: str,
        account_address: str,
        effective_nominal_usd: float,
        risk_approved: bool,
        risk_reasons: Iterable[str],
        account_value_usd: float,
        total_margin_used_usd: float,
        health_status: str,
        intents: list[OrderIntent],
    ) -> PersistedRun:
        with self._db() as conn:
            existing = conn.execute("SELECT id FROM rebalance_runs WHERE run_key=?", (run_key,)).fetchone()
            if existing:
                run_id = int(existing["id"])
                count = conn.execute("SELECT COUNT(*) AS n FROM trade_intents WHERE run_id=?", (run_id,)).fetchone()["n"]
                return PersistedRun(run_id, run_key, int(count))
            cur = conn.execute(
                """
                INSERT INTO rebalance_runs(
                    run_key, created_at_utc, signal_date, signal_snapshot_id, signal_fingerprint,
                    network, execution_mode, account_address, effective_nominal_usd,
                    risk_approved, risk_reasons_json, account_value_usd, total_margin_used_usd,
                    health_status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_key, self._now_iso(), signal_date, signal_snapshot_id, signal_fingerprint,
                    network, execution_mode, account_address, effective_nominal_usd,
                    int(risk_approved), json.dumps(list(risk_reasons)), account_value_usd,
                    total_margin_used_usd, health_status,
                ),
            )
            run_id = int(cur.lastrowid)
            for intent in intents:
                conn.execute(
                    """
                    INSERT INTO trade_intents(
                        run_id, ticker, side, quantity, limit_price, trade_value_usd, tif,
                        reduce_only, cloid, arrival_price, target_weight, current_weight,
                        destination_weight, proposed_tca_bps, status
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id, intent.ticker, intent.side, intent.quantity, intent.limit_price,
                        intent.trade_value_usd, intent.tif, int(intent.reduce_only), intent.cloid,
                        intent.arrival_price, intent.target_weight, intent.current_weight,
                        intent.destination_weight, intent.proposed_tca_bps, intent.status,
                    ),
                )
            return PersistedRun(run_id, run_key, len(intents))

    def execution_lock_exists(self, *, signal_date: str, network: str, account_address: str) -> bool:
        with self._db() as conn:
            row = conn.execute(
                "SELECT 1 FROM execution_locks WHERE signal_date=? AND network=? AND account_address=?",
                (signal_date, network, account_address.lower()),
            ).fetchone()
        return row is not None

    def reserve_execution(self, *, signal_date: str, network: str, account_address: str, run_id: int) -> None:
        with self._db() as conn:
            conn.execute(
                "INSERT INTO execution_locks(signal_date, network, account_address, locked_at_utc, run_id) VALUES (?, ?, ?, ?, ?)",
                (signal_date, network, account_address.lower(), self._now_iso(), run_id),
            )

    def latest_run(self) -> dict | None:
        with self._db() as conn:
            row = conn.execute("SELECT * FROM rebalance_runs ORDER BY id DESC LIMIT 1").fetchone()
            if row is None:
                return None
            out = dict(row)
            out["intent_count"] = int(conn.execute("SELECT COUNT(*) AS n FROM trade_intents WHERE run_id=?", (row["id"],)).fetchone()["n"])
            return out

    def pending_transmitted_intents(self) -> list[dict]:
        with self._db() as conn:
            rows = conn.execute(
                "SELECT * FROM trade_intents WHERE transmitted=1 AND status NOT IN ('filled','cancelled','rejected') ORDER BY id"
            ).fetchall()
        return [dict(row) for row in rows]

    def update_intent_exchange_status(self, intent_id: int, *, status: str, exchange_oid: int | None = None) -> None:
        with self._db() as conn:
            conn.execute(
                "UPDATE trade_intents SET status=?, last_exchange_status=?, exchange_oid=COALESCE(?, exchange_oid) WHERE id=?",
                (status, status, exchange_oid, intent_id),
            )


def build_alo_intents(
    *,
    plan: list[TradePlanRow],
    quotes: dict[str, BboQuote],
    run_key: str,
    account_address: str,
    min_order_usd: float,
) -> list[OrderIntent]:
    intents: list[OrderIntent] = []
    for row in plan:
        if abs(row.trade_value_usd) < max(0.0, min_order_usd) or abs(row.trade_quantity) < 1e-18:
            continue
        quote = quotes.get(row.ticker)
        if quote is None:
            raise ValueError(f"missing BBO quote for planned trade {row.ticker}")
        side = "BUY" if row.trade_quantity > 0 else "SELL"
        limit_px = quote.bid_price if side == "BUY" else quote.ask_price
        if limit_px <= 0:
            raise ValueError(f"invalid BBO limit price for {row.ticker}")
        cloid = PreLiveLedger.make_cloid(
            run_key=run_key,
            account=account_address,
            ticker=row.ticker,
            side=side,
        )
        intents.append(
            OrderIntent(
                ticker=row.ticker,
                side=side,
                quantity=abs(row.trade_quantity),
                limit_price=limit_px,
                trade_value_usd=abs(row.trade_quantity) * limit_px,
                tif="Alo",
                reduce_only=row.is_universe_exit,
                cloid=cloid,
                arrival_price=row.arrival_price,
                target_weight=row.target_weight,
                current_weight=row.current_weight,
                destination_weight=row.post_trade_weight,
                status="WOULD_SUBMIT",
            )
        )
    return intents
