from __future__ import annotations

from dataclasses import dataclass
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import sqlite3

from .config import YoloConfig


SIZING_SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS sizing_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    initialized_at_utc TEXT NOT NULL,
    base_nominal_usd REAL NOT NULL,
    initial_account_value_usd REAL NOT NULL,
    units REAL NOT NULL,
    initial_nav_per_unit REAL NOT NULL,
    last_recorded_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sizing_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    recorded_at_utc TEXT NOT NULL,
    event_type TEXT NOT NULL,
    account_value_usd REAL NOT NULL,
    external_flow_usd REAL NOT NULL DEFAULT 0,
    units_before REAL,
    units_after REAL,
    nav_per_unit REAL,
    raw_multiplier REAL,
    applied_multiplier REAL,
    effective_nominal_usd REAL,
    note TEXT
);

CREATE INDEX IF NOT EXISTS idx_sizing_events_time
ON sizing_events(recorded_at_utc);
"""


class SizingError(RuntimeError):
    pass


@dataclass(frozen=True)
class SizingDecision:
    mode: str
    base_nominal_usd: float
    account_value_usd: float
    initial_account_value_usd: float | None
    units: float | None
    nav_per_unit: float | None
    raw_multiplier: float
    applied_multiplier: float
    effective_nominal_usd: float
    clipped: bool
    initialized_now: bool = False


@dataclass(frozen=True)
class SizingStatus:
    initialized: bool
    base_nominal_usd: float | None
    initial_account_value_usd: float | None
    units: float | None
    nav_per_unit: float | None
    multiplier: float | None
    latest_account_value_usd: float | None
    latest_effective_nominal_usd: float | None


class SizingLedger:
    """Persist unitized YOLO NAV so external cash flows do not masquerade as P&L.

    The strategy starts with NAV-per-unit = 1.0. Profit/loss changes NAV-per-unit.
    Deposits and withdrawals change the number of units at the pre-flow NAV-per-unit,
    leaving the performance index unchanged.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as conn:
            conn.executescript(SIZING_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
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

    def _state(self) -> sqlite3.Row | None:
        with self._db() as conn:
            return conn.execute("SELECT * FROM sizing_state WHERE id=1").fetchone()

    def _record_event(
        self,
        *,
        event_type: str,
        account_value_usd: float,
        external_flow_usd: float = 0.0,
        units_before: float | None = None,
        units_after: float | None = None,
        nav_per_unit: float | None = None,
        raw_multiplier: float | None = None,
        applied_multiplier: float | None = None,
        effective_nominal_usd: float | None = None,
        note: str | None = None,
    ) -> None:
        with self._db() as conn:
            conn.execute(
                """
                INSERT INTO sizing_events (
                    recorded_at_utc, event_type, account_value_usd, external_flow_usd,
                    units_before, units_after, nav_per_unit, raw_multiplier,
                    applied_multiplier, effective_nominal_usd, note
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    self._now_iso(),
                    event_type,
                    account_value_usd,
                    external_flow_usd,
                    units_before,
                    units_after,
                    nav_per_unit,
                    raw_multiplier,
                    applied_multiplier,
                    effective_nominal_usd,
                    note,
                ),
            )

    def rebase(self, *, account_value_usd: float, base_nominal_usd: float) -> None:
        if account_value_usd <= 0:
            raise SizingError("cannot initialize compounding with non-positive account value")
        if base_nominal_usd <= 0:
            raise SizingError("base nominal must be positive")
        now = self._now_iso()
        units = account_value_usd
        with self._db() as conn:
            conn.execute("DELETE FROM sizing_state")
            conn.execute(
                """
                INSERT INTO sizing_state (
                    id, initialized_at_utc, base_nominal_usd, initial_account_value_usd,
                    units, initial_nav_per_unit, last_recorded_at_utc
                ) VALUES (1, ?, ?, ?, ?, 1.0, ?)
                """,
                (now, base_nominal_usd, account_value_usd, units, now),
            )
        self._record_event(
            event_type="rebase",
            account_value_usd=account_value_usd,
            units_before=None,
            units_after=units,
            nav_per_unit=1.0,
            raw_multiplier=1.0,
            applied_multiplier=1.0,
            effective_nominal_usd=base_nominal_usd,
            note="compounding baseline initialized/rebased",
        )

    def decision(self, *, account_value_usd: float, config: YoloConfig) -> SizingDecision:
        mode = config.sizing_mode.strip().lower()
        if mode not in {"fixed", "compound"}:
            raise SizingError(f"unknown YOLO_SIZING_MODE={config.sizing_mode!r}; use fixed or compound")
        if config.nominal_usd <= 0:
            raise SizingError("YOLO_NOMINAL_USD must be positive")
        if config.min_nominal_multiplier <= 0:
            raise SizingError("YOLO_MIN_NOMINAL_MULTIPLIER must be positive")
        if config.max_nominal_multiplier < config.min_nominal_multiplier:
            raise SizingError("YOLO_MAX_NOMINAL_MULTIPLIER must be >= YOLO_MIN_NOMINAL_MULTIPLIER")

        if mode == "fixed":
            decision = SizingDecision(
                mode="fixed",
                base_nominal_usd=config.nominal_usd,
                account_value_usd=account_value_usd,
                initial_account_value_usd=None,
                units=None,
                nav_per_unit=None,
                raw_multiplier=1.0,
                applied_multiplier=1.0,
                effective_nominal_usd=config.nominal_usd,
                clipped=False,
            )
            self._record_event(
                event_type="fixed_observation",
                account_value_usd=account_value_usd,
                raw_multiplier=1.0,
                applied_multiplier=1.0,
                effective_nominal_usd=config.nominal_usd,
            )
            return decision

        if config.require_dedicated_subaccount_for_compound and not config.hl_subaccount_address:
            raise SizingError(
                "compound sizing requires HL_YOLO_SUBACCOUNT_ADDRESS by default so other strategies' P&L "
                "cannot change YOLO size; set a YOLO subaccount or explicitly disable the guard"
            )
        if account_value_usd <= 0:
            raise SizingError("cannot compound from a non-positive Hyperliquid account value")

        state = self._state()
        initialized_now = False
        if state is None:
            self.rebase(account_value_usd=account_value_usd, base_nominal_usd=config.nominal_usd)
            state = self._state()
            initialized_now = True
        assert state is not None

        base_nominal = float(state["base_nominal_usd"])
        if abs(base_nominal - config.nominal_usd) > max(0.01, abs(base_nominal) * 1e-9):
            raise SizingError(
                f"configured YOLO_NOMINAL_USD ${config.nominal_usd:,.2f} differs from persisted "
                f"compounding base ${base_nominal:,.2f}; use --rebase-compounding to intentionally reset the baseline"
            )

        units = float(state["units"])
        initial_nav_per_unit = float(state["initial_nav_per_unit"])
        if units <= 0 or initial_nav_per_unit <= 0:
            raise SizingError("invalid persisted compounding state")

        nav_per_unit = account_value_usd / units
        raw_multiplier = nav_per_unit / initial_nav_per_unit
        applied_multiplier = min(
            config.max_nominal_multiplier,
            max(config.min_nominal_multiplier, raw_multiplier),
        )
        effective_nominal = base_nominal * applied_multiplier
        clipped = abs(applied_multiplier - raw_multiplier) > 1e-12

        with self._db() as conn:
            conn.execute(
                "UPDATE sizing_state SET last_recorded_at_utc=? WHERE id=1",
                (self._now_iso(),),
            )
        self._record_event(
            event_type="compound_observation",
            account_value_usd=account_value_usd,
            units_before=units,
            units_after=units,
            nav_per_unit=nav_per_unit,
            raw_multiplier=raw_multiplier,
            applied_multiplier=applied_multiplier,
            effective_nominal_usd=effective_nominal,
            note="multiplier clipped to configured bounds" if clipped else None,
        )

        return SizingDecision(
            mode="compound",
            base_nominal_usd=base_nominal,
            account_value_usd=account_value_usd,
            initial_account_value_usd=float(state["initial_account_value_usd"]),
            units=units,
            nav_per_unit=nav_per_unit,
            raw_multiplier=raw_multiplier,
            applied_multiplier=applied_multiplier,
            effective_nominal_usd=effective_nominal,
            clipped=clipped,
            initialized_now=initialized_now,
        )

    def record_external_flow(self, *, amount_usd: float, current_account_value_usd: float) -> None:
        if amount_usd == 0:
            raise SizingError("external flow amount cannot be zero")
        state = self._state()
        if state is None:
            raise SizingError(
                "compounding ledger is not initialized; run a compound live-data preview or --rebase-compounding first"
            )

        units_before = float(state["units"])
        pre_flow_equity = current_account_value_usd - amount_usd
        if pre_flow_equity <= 0:
            raise SizingError("recorded flow implies non-positive pre-flow equity")
        nav_per_unit_before = pre_flow_equity / units_before
        if nav_per_unit_before <= 0:
            raise SizingError("invalid pre-flow NAV per unit")

        unit_delta = amount_usd / nav_per_unit_before
        units_after = units_before + unit_delta
        if units_after <= 0:
            raise SizingError("withdrawal would leave non-positive strategy units")
        nav_per_unit_after = current_account_value_usd / units_after

        with self._db() as conn:
            conn.execute(
                "UPDATE sizing_state SET units=?, last_recorded_at_utc=? WHERE id=1",
                (units_after, self._now_iso()),
            )
        self._record_event(
            event_type="external_flow",
            account_value_usd=current_account_value_usd,
            external_flow_usd=amount_usd,
            units_before=units_before,
            units_after=units_after,
            nav_per_unit=nav_per_unit_after,
            note=(
                "deposit; units issued at pre-flow NAV per unit"
                if amount_usd > 0
                else "withdrawal; units redeemed at pre-flow NAV per unit"
            ),
        )

    def status(self, *, current_account_value_usd: float | None = None) -> SizingStatus:
        state = self._state()
        if state is None:
            return SizingStatus(False, None, None, None, None, None, current_account_value_usd, None)
        units = float(state["units"])
        latest = current_account_value_usd
        nav_per_unit = None if latest is None else latest / units
        multiplier = None if nav_per_unit is None else nav_per_unit / float(state["initial_nav_per_unit"])
        effective = None if multiplier is None else float(state["base_nominal_usd"]) * multiplier
        return SizingStatus(
            initialized=True,
            base_nominal_usd=float(state["base_nominal_usd"]),
            initial_account_value_usd=float(state["initial_account_value_usd"]),
            units=units,
            nav_per_unit=nav_per_unit,
            multiplier=multiplier,
            latest_account_value_usd=latest,
            latest_effective_nominal_usd=effective,
        )
