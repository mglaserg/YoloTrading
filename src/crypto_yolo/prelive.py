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


RUN_KEY_VERSION = "v0.6-live"


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
    direction_mode TEXT NOT NULL DEFAULT 'long_short',
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
    current_quantity REAL NOT NULL DEFAULT 0,
    destination_quantity REAL NOT NULL DEFAULT 0,
    proposed_tca_bps REAL,
    status TEXT NOT NULL,
    transmitted INTEGER NOT NULL DEFAULT 0,
    exchange_oid INTEGER,
    last_exchange_status TEXT,
    FOREIGN KEY(run_id) REFERENCES rebalance_runs(id)
);
CREATE INDEX IF NOT EXISTS idx_trade_intents_run ON trade_intents(run_id);
CREATE TABLE IF NOT EXISTS run_targets (
    run_id INTEGER NOT NULL,
    ticker TEXT NOT NULL,
    target_weight REAL NOT NULL,
    destination_weight REAL NOT NULL,
    destination_quantity REAL NOT NULL,
    arrival_price REAL,
    is_universe_exit INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(run_id, ticker),
    FOREIGN KEY(run_id) REFERENCES rebalance_runs(id)
);
CREATE TABLE IF NOT EXISTS execution_locks (
    signal_date TEXT NOT NULL,
    network TEXT NOT NULL,
    account_address TEXT NOT NULL,
    locked_at_utc TEXT NOT NULL,
    run_id INTEGER NOT NULL,
    PRIMARY KEY(signal_date, network, account_address)
);
CREATE TABLE IF NOT EXISTS execution_sessions (
    run_id INTEGER PRIMARY KEY,
    started_at_utc TEXT NOT NULL,
    started_at_ms INTEGER NOT NULL,
    deadman_time_ms INTEGER,
    completed_at_utc TEXT,
    status TEXT NOT NULL,
    verification_json TEXT,
    FOREIGN KEY(run_id) REFERENCES rebalance_runs(id)
);
CREATE TABLE IF NOT EXISTS execution_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    intent_id INTEGER NOT NULL,
    attempt_no INTEGER NOT NULL,
    cloid TEXT NOT NULL UNIQUE,
    quantity REAL NOT NULL,
    limit_price REAL NOT NULL,
    created_at_utc TEXT NOT NULL,
    transmitted_at_utc TEXT,
    completed_at_utc TEXT,
    status TEXT NOT NULL,
    exchange_oid INTEGER,
    error TEXT,
    response_json TEXT,
    UNIQUE(intent_id, attempt_no),
    FOREIGN KEY(intent_id) REFERENCES trade_intents(id)
);
CREATE INDEX IF NOT EXISTS idx_execution_attempts_intent ON execution_attempts(intent_id);
CREATE TABLE IF NOT EXISTS execution_fills (
    fill_key TEXT PRIMARY KEY,
    intent_id INTEGER NOT NULL,
    attempt_id INTEGER,
    exchange_oid INTEGER,
    ticker TEXT NOT NULL,
    side TEXT NOT NULL,
    price REAL NOT NULL,
    quantity REAL NOT NULL,
    fee_usd REAL NOT NULL DEFAULT 0,
    fee_token TEXT,
    closed_pnl_usd REAL,
    crossed INTEGER,
    time_ms INTEGER NOT NULL,
    tid TEXT,
    raw_json TEXT NOT NULL,
    FOREIGN KEY(intent_id) REFERENCES trade_intents(id),
    FOREIGN KEY(attempt_id) REFERENCES execution_attempts(id)
);
CREATE INDEX IF NOT EXISTS idx_execution_fills_intent ON execution_fills(intent_id);
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
            self._ensure_columns(conn)

    @staticmethod
    def _ensure_columns(conn: sqlite3.Connection) -> None:
        run_columns = {row[1] for row in conn.execute("PRAGMA table_info(rebalance_runs)")}
        if "direction_mode" not in run_columns:
            conn.execute(
                "ALTER TABLE rebalance_runs ADD COLUMN direction_mode TEXT NOT NULL DEFAULT 'long_short'"
            )
        columns = {row[1] for row in conn.execute("PRAGMA table_info(trade_intents)")}
        if "current_quantity" not in columns:
            conn.execute("ALTER TABLE trade_intents ADD COLUMN current_quantity REAL NOT NULL DEFAULT 0")
        if "destination_quantity" not in columns:
            conn.execute("ALTER TABLE trade_intents ADD COLUMN destination_quantity REAL NOT NULL DEFAULT 0")

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
            [RUN_KEY_VERSION, signal_date, signal_fingerprint, account.lower(), network, state],
            separators=(",", ":"),
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def make_cloid(
        *, run_key: str, account: str, ticker: str, side: str, attempt: int = 0, intent_key: str = "main"
    ) -> str:
        raw = f"YOLO|{run_key}|{account.lower()}|{ticker}|{side}|{intent_key}|{attempt}"
        return "0x" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]

    @staticmethod
    def make_reprice_cloid(*, base_cloid: str, attempt: int) -> str:
        raw = f"YOLO|REPRICE|{base_cloid.lower()}|{int(attempt)}"
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
        direction_mode: str = "long_short",
        plan: list[TradePlanRow] | None = None,
    ) -> PersistedRun:
        with self._db() as conn:
            existing = conn.execute("SELECT * FROM rebalance_runs WHERE run_key=?", (run_key,)).fetchone()
            if existing:
                run_id = int(existing["id"])
                session = conn.execute(
                    "SELECT status FROM execution_sessions WHERE run_id=?", (run_id,)
                ).fetchone()
                attempt_count = int(
                    conn.execute(
                        """
                        SELECT COUNT(*) AS n
                        FROM execution_attempts a
                        JOIN trade_intents i ON i.id=a.intent_id
                        WHERE i.run_id=?
                        """,
                        (run_id,),
                    ).fetchone()["n"]
                )
                transmitted_count = int(
                    conn.execute(
                        "SELECT COUNT(*) AS n FROM trade_intents WHERE run_id=? AND transmitted=1",
                        (run_id,),
                    ).fetchone()["n"]
                )
                refreshable = (
                    not bool(existing["transmitted"])
                    and transmitted_count == 0
                    and attempt_count == 0
                    and session is None
                )
                if refreshable:
                    # A same-day plan may be inspected and then rebuilt later with a
                    # fresher BBO. Until execution actually starts, replace the
                    # preview intents/targets in place rather than retaining stale
                    # quote prices. CLOIDs remain deterministic because run_key is
                    # unchanged.
                    conn.execute("DELETE FROM run_targets WHERE run_id=?", (run_id,))
                    conn.execute("DELETE FROM trade_intents WHERE run_id=?", (run_id,))
                    conn.execute(
                        """
                        UPDATE rebalance_runs SET
                            signal_snapshot_id=?, signal_fingerprint=?, execution_mode=?,
                            direction_mode=?, effective_nominal_usd=?, risk_approved=?,
                            risk_reasons_json=?, account_value_usd=?, total_margin_used_usd=?,
                            health_status=?
                        WHERE id=?
                        """,
                        (
                            signal_snapshot_id, signal_fingerprint, execution_mode, direction_mode,
                            effective_nominal_usd, int(risk_approved), json.dumps(list(risk_reasons)),
                            account_value_usd, total_margin_used_usd, health_status, run_id,
                        ),
                    )
                    for row in plan or []:
                        conn.execute(
                            """
                            INSERT INTO run_targets(
                                run_id, ticker, target_weight, destination_weight, destination_quantity,
                                arrival_price, is_universe_exit
                            ) VALUES (?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                run_id, row.ticker, row.target_weight, row.post_trade_weight,
                                row.current_quantity + row.trade_quantity, row.arrival_price,
                                int(row.is_universe_exit),
                            ),
                        )
                    for intent in intents:
                        conn.execute(
                            """
                            INSERT INTO trade_intents(
                                run_id, ticker, side, quantity, limit_price, trade_value_usd, tif,
                                reduce_only, cloid, arrival_price, target_weight, current_weight,
                                destination_weight, current_quantity, destination_quantity,
                                proposed_tca_bps, status
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                run_id, intent.ticker, intent.side, intent.quantity, intent.limit_price,
                                intent.trade_value_usd, intent.tif, int(intent.reduce_only), intent.cloid,
                                intent.arrival_price, intent.target_weight, intent.current_weight,
                                intent.destination_weight, intent.current_quantity,
                                intent.destination_quantity, intent.proposed_tca_bps, intent.status,
                            ),
                        )
                    return PersistedRun(run_id, run_key, len(intents))

                conn.execute(
                    "UPDATE rebalance_runs SET execution_mode=?, direction_mode=?, health_status=? WHERE id=?",
                    (execution_mode, direction_mode, health_status, run_id),
                )
                count = conn.execute("SELECT COUNT(*) AS n FROM trade_intents WHERE run_id=?", (run_id,)).fetchone()["n"]
                return PersistedRun(run_id, run_key, int(count))
            cur = conn.execute(
                """
                INSERT INTO rebalance_runs(
                    run_key, created_at_utc, signal_date, signal_snapshot_id, signal_fingerprint,
                    network, execution_mode, direction_mode, account_address, effective_nominal_usd,
                    risk_approved, risk_reasons_json, account_value_usd, total_margin_used_usd,
                    health_status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_key, self._now_iso(), signal_date, signal_snapshot_id, signal_fingerprint,
                    network, execution_mode, direction_mode, account_address, effective_nominal_usd,
                    int(risk_approved), json.dumps(list(risk_reasons)), account_value_usd,
                    total_margin_used_usd, health_status,
                ),
            )
            run_id = int(cur.lastrowid)
            for row in plan or []:
                conn.execute(
                    """
                    INSERT INTO run_targets(
                        run_id, ticker, target_weight, destination_weight, destination_quantity,
                        arrival_price, is_universe_exit
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        row.ticker,
                        row.target_weight,
                        row.post_trade_weight,
                        row.current_quantity + row.trade_quantity,
                        row.arrival_price,
                        int(row.is_universe_exit),
                    ),
                )
            for intent in intents:
                conn.execute(
                    """
                    INSERT INTO trade_intents(
                        run_id, ticker, side, quantity, limit_price, trade_value_usd, tif,
                        reduce_only, cloid, arrival_price, target_weight, current_weight,
                        destination_weight, current_quantity, destination_quantity,
                        proposed_tca_bps, status
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id, intent.ticker, intent.side, intent.quantity, intent.limit_price,
                        intent.trade_value_usd, intent.tif, int(intent.reduce_only), intent.cloid,
                        intent.arrival_price, intent.target_weight, intent.current_weight,
                        intent.destination_weight, intent.current_quantity,
                        intent.destination_quantity, intent.proposed_tca_bps, intent.status,
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
            existing = conn.execute(
                "SELECT run_id FROM execution_locks WHERE signal_date=? AND network=? AND account_address=?",
                (signal_date, network, account_address.lower()),
            ).fetchone()
            if existing is not None:
                if int(existing["run_id"]) == int(run_id):
                    return
                raise RuntimeError(
                    f"execution already reserved for {signal_date} on {network} by run {existing['run_id']}"
                )
            conn.execute(
                "INSERT INTO execution_locks(signal_date, network, account_address, locked_at_utc, run_id) VALUES (?, ?, ?, ?, ?)",
                (signal_date, network, account_address.lower(), self._now_iso(), run_id),
            )

    def get_execution_lock(self, *, signal_date: str, network: str, account_address: str) -> dict | None:
        with self._db() as conn:
            row = conn.execute(
                "SELECT * FROM execution_locks WHERE signal_date=? AND network=? AND account_address=?",
                (signal_date, network, account_address.lower()),
            ).fetchone()
        return None if row is None else dict(row)

    def latest_run(self) -> dict | None:
        with self._db() as conn:
            row = conn.execute("SELECT * FROM rebalance_runs ORDER BY id DESC LIMIT 1").fetchone()
            if row is None:
                return None
            out = dict(row)
            out["intent_count"] = int(conn.execute("SELECT COUNT(*) AS n FROM trade_intents WHERE run_id=?", (row["id"],)).fetchone()["n"])
            return out

    def run_by_id(self, run_id: int) -> dict | None:
        with self._db() as conn:
            row = conn.execute("SELECT * FROM rebalance_runs WHERE id=?", (int(run_id),)).fetchone()
        return None if row is None else dict(row)

    def intents_for_run(self, run_id: int) -> list[dict]:
        with self._db() as conn:
            rows = conn.execute(
                "SELECT * FROM trade_intents WHERE run_id=? ORDER BY reduce_only DESC, trade_value_usd DESC, id",
                (int(run_id),),
            ).fetchall()
        return [dict(row) for row in rows]

    def targets_for_run(self, run_id: int) -> list[dict]:
        with self._db() as conn:
            rows = conn.execute(
                "SELECT * FROM run_targets WHERE run_id=? ORDER BY ticker",
                (int(run_id),),
            ).fetchall()
        return [dict(row) for row in rows]

    def pending_transmitted_intents(self) -> list[dict]:
        with self._db() as conn:
            rows = conn.execute(
                "SELECT * FROM trade_intents WHERE transmitted=1 AND status NOT IN ('filled','canceled','cancelled','rejected','dust') ORDER BY id"
            ).fetchall()
        return [dict(row) for row in rows]

    def update_intent_exchange_status(self, intent_id: int, *, status: str, exchange_oid: int | None = None) -> None:
        with self._db() as conn:
            conn.execute(
                "UPDATE trade_intents SET status=?, last_exchange_status=?, exchange_oid=COALESCE(?, exchange_oid) WHERE id=?",
                (status, status, exchange_oid, intent_id),
            )

    def mark_run_transmitted(self, run_id: int) -> None:
        with self._db() as conn:
            conn.execute("UPDATE rebalance_runs SET transmitted=1 WHERE id=?", (int(run_id),))

    def mark_intent_transmitted(self, intent_id: int, *, status: str = "submitted") -> None:
        with self._db() as conn:
            conn.execute(
                "UPDATE trade_intents SET transmitted=1, status=? WHERE id=?",
                (status, int(intent_id)),
            )

    def start_execution_session(self, *, run_id: int, started_at_ms: int, deadman_time_ms: int | None) -> None:
        with self._db() as conn:
            conn.execute(
                """
                INSERT INTO execution_sessions(run_id, started_at_utc, started_at_ms, deadman_time_ms, status)
                VALUES (?, ?, ?, ?, 'running')
                ON CONFLICT(run_id) DO UPDATE SET
                    deadman_time_ms=excluded.deadman_time_ms,
                    status=CASE WHEN execution_sessions.status='complete' THEN execution_sessions.status ELSE 'running' END
                """,
                (int(run_id), self._now_iso(), int(started_at_ms), deadman_time_ms),
            )

    def finish_execution_session(self, *, run_id: int, status: str, verification: dict | None = None) -> None:
        with self._db() as conn:
            conn.execute(
                "UPDATE execution_sessions SET completed_at_utc=?, status=?, verification_json=? WHERE run_id=?",
                (self._now_iso(), status, json.dumps(verification) if verification is not None else None, int(run_id)),
            )

    def execution_session(self, run_id: int) -> dict | None:
        with self._db() as conn:
            row = conn.execute("SELECT * FROM execution_sessions WHERE run_id=?", (int(run_id),)).fetchone()
        return None if row is None else dict(row)

    def prepare_attempt(
        self,
        *,
        intent_id: int,
        attempt_no: int,
        cloid: str,
        quantity: float,
        limit_price: float,
    ) -> dict:
        with self._db() as conn:
            existing = conn.execute(
                "SELECT * FROM execution_attempts WHERE intent_id=? AND attempt_no=?",
                (int(intent_id), int(attempt_no)),
            ).fetchone()
            if existing is not None:
                return dict(existing)
            cur = conn.execute(
                """
                INSERT INTO execution_attempts(
                    intent_id, attempt_no, cloid, quantity, limit_price, created_at_utc, status
                ) VALUES (?, ?, ?, ?, ?, ?, 'prepared')
                """,
                (int(intent_id), int(attempt_no), cloid, float(quantity), float(limit_price), self._now_iso()),
            )
            row = conn.execute("SELECT * FROM execution_attempts WHERE id=?", (int(cur.lastrowid),)).fetchone()
        return dict(row)

    def update_attempt(
        self,
        attempt_id: int,
        *,
        status: str,
        exchange_oid: int | None = None,
        error: str | None = None,
        response: object | None = None,
        transmitted: bool = False,
        completed: bool = False,
    ) -> None:
        transmitted_at = self._now_iso() if transmitted else None
        completed_at = self._now_iso() if completed else None
        with self._db() as conn:
            conn.execute(
                """
                UPDATE execution_attempts SET
                    status=?,
                    exchange_oid=COALESCE(?, exchange_oid),
                    error=?,
                    response_json=COALESCE(?, response_json),
                    transmitted_at_utc=COALESCE(?, transmitted_at_utc),
                    completed_at_utc=COALESCE(?, completed_at_utc)
                WHERE id=?
                """,
                (
                    status,
                    exchange_oid,
                    error,
                    json.dumps(response, sort_keys=True, default=str) if response is not None else None,
                    transmitted_at,
                    completed_at,
                    int(attempt_id),
                ),
            )

    def attempts_for_intent(self, intent_id: int) -> list[dict]:
        with self._db() as conn:
            rows = conn.execute(
                "SELECT * FROM execution_attempts WHERE intent_id=? ORDER BY attempt_no",
                (int(intent_id),),
            ).fetchall()
        return [dict(row) for row in rows]

    def record_fill(self, *, intent_id: int, attempt_id: int | None, fill: dict) -> bool:
        raw = json.dumps(fill, sort_keys=True, separators=(",", ":"), default=str)
        tid = fill.get("tid")
        fill_key = str(tid) if tid not in (None, "") else hashlib.sha256(raw.encode("utf-8")).hexdigest()
        side_raw = str(fill.get("side", "")).upper()
        side = "BUY" if side_raw == "B" else "SELL" if side_raw == "A" else side_raw
        try:
            oid = int(fill.get("oid")) if fill.get("oid") is not None else None
        except (TypeError, ValueError):
            oid = None
        with self._db() as conn:
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO execution_fills(
                    fill_key, intent_id, attempt_id, exchange_oid, ticker, side, price,
                    quantity, fee_usd, fee_token, closed_pnl_usd, crossed, time_ms, tid, raw_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    fill_key, int(intent_id), attempt_id, oid, str(fill.get("coin", "")), side,
                    float(fill.get("px", 0)), float(fill.get("sz", 0)), float(fill.get("fee", 0)),
                    fill.get("feeToken"),
                    float(fill.get("closedPnl", 0)) if fill.get("closedPnl") not in (None, "") else None,
                    int(bool(fill.get("crossed"))) if fill.get("crossed") is not None else None,
                    int(fill.get("time", 0)), str(tid) if tid not in (None, "") else None, raw,
                ),
            )
            return cur.rowcount > 0

    def fills_for_intent(self, intent_id: int) -> list[dict]:
        with self._db() as conn:
            rows = conn.execute(
                "SELECT * FROM execution_fills WHERE intent_id=? ORDER BY time_ms, fill_key",
                (int(intent_id),),
            ).fetchall()
        return [dict(row) for row in rows]

    def execution_summary(self, run_id: int) -> dict:
        run = self.run_by_id(run_id)
        session = self.execution_session(run_id)
        intents = self.intents_for_run(run_id)
        return {
            "run": run,
            "session": session,
            "intents": [
                {
                    **row,
                    "attempts": self.attempts_for_intent(int(row["id"])),
                    "fills": self.fills_for_intent(int(row["id"])),
                }
                for row in intents
            ],
        }


def build_alo_intents(
    *,
    plan: list[TradePlanRow],
    quotes: dict[str, BboQuote],
    run_key: str,
    account_address: str,
    min_order_usd: float,
) -> list[OrderIntent]:
    intents: list[OrderIntent] = []

    def append_intent(
        *,
        row: TradePlanRow,
        side: str,
        quantity: float,
        reduce_only: bool,
        current_quantity: float,
        destination_quantity: float,
        current_weight: float,
        destination_weight: float,
        intent_key: str,
    ) -> None:
        if quantity <= 1e-18:
            return
        quote = quotes.get(row.ticker)
        if quote is None:
            raise ValueError(f"missing BBO quote for planned trade {row.ticker}")
        limit_px = quote.bid_price if side == "BUY" else quote.ask_price
        if limit_px <= 0:
            raise ValueError(f"invalid BBO limit price for {row.ticker}")
        if quantity * limit_px < max(0.0, min_order_usd):
            return
        cloid = PreLiveLedger.make_cloid(
            run_key=run_key,
            account=account_address,
            ticker=row.ticker,
            side=side,
            intent_key=intent_key,
        )
        intents.append(
            OrderIntent(
                ticker=row.ticker,
                side=side,
                quantity=quantity,
                limit_price=limit_px,
                trade_value_usd=quantity * limit_px,
                tif="Alo",
                reduce_only=reduce_only,
                cloid=cloid,
                arrival_price=row.arrival_price,
                target_weight=row.target_weight,
                current_weight=current_weight,
                destination_weight=destination_weight,
                current_quantity=current_quantity,
                destination_quantity=destination_quantity,
                status="WOULD_SUBMIT",
            )
        )

    for row in plan:
        if abs(row.trade_value_usd) < max(0.0, min_order_usd) or abs(row.trade_quantity) < 1e-18:
            continue
        destination_quantity = row.current_quantity + row.trade_quantity

        # Crossing zero is deliberately two-stage. The close leg is reduce-only;
        # only after it is filled/dust does the executor move to the opening leg.
        # This avoids relying on Hyperliquid accepting a single position-flip order.
        crosses_zero = (
            row.current_quantity < -1e-18 and destination_quantity > 1e-18
        ) or (
            row.current_quantity > 1e-18 and destination_quantity < -1e-18
        )
        if crosses_zero:
            side = "BUY" if row.trade_quantity > 0 else "SELL"
            append_intent(
                row=row,
                side=side,
                quantity=abs(row.current_quantity),
                reduce_only=True,
                current_quantity=row.current_quantity,
                destination_quantity=0.0,
                current_weight=row.current_weight,
                destination_weight=0.0,
                intent_key="close",
            )
            append_intent(
                row=row,
                side=side,
                quantity=abs(destination_quantity),
                reduce_only=False,
                current_quantity=0.0,
                destination_quantity=destination_quantity,
                current_weight=0.0,
                destination_weight=row.post_trade_weight,
                intent_key="open",
            )
            continue

        side = "BUY" if row.trade_quantity > 0 else "SELL"
        reducing_long = (
            row.current_quantity > 0
            and row.trade_quantity < 0
            and destination_quantity >= -1e-18
            and abs(destination_quantity) <= abs(row.current_quantity) + 1e-18
        )
        reducing_short = (
            row.current_quantity < 0
            and row.trade_quantity > 0
            and destination_quantity <= 1e-18
            and abs(destination_quantity) <= abs(row.current_quantity) + 1e-18
        )
        append_intent(
            row=row,
            side=side,
            quantity=abs(row.trade_quantity),
            reduce_only=row.is_universe_exit or reducing_long or reducing_short,
            current_quantity=row.current_quantity,
            destination_quantity=destination_quantity,
            current_weight=row.current_weight,
            destination_weight=row.post_trade_weight,
            intent_key="main",
        )
    return intents
