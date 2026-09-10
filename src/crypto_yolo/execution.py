from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import math
import time
from typing import Any, Callable

from .archive import SignalArchive
from .config import YoloConfig
from .prelive import PreLiveLedger


class LiveExecutionError(RuntimeError):
    pass


class LiveDependencyError(LiveExecutionError):
    pass


@dataclass(frozen=True)
class PlacementOutcome:
    status: str
    oid: int | None = None
    filled_quantity: float = 0.0
    average_price: float | None = None
    error: str | None = None


@dataclass(frozen=True)
class OrderStatusOutcome:
    status: str
    oid: int | None = None
    original_quantity: float | None = None
    remaining_quantity: float | None = None

    @property
    def confirmed_filled_quantity(self) -> float | None:
        if self.original_quantity is None or self.remaining_quantity is None:
            return None
        return max(self.original_quantity - self.remaining_quantity, 0.0)


@dataclass(frozen=True)
class IntentExecutionResult:
    intent_id: int
    ticker: str
    status: str
    requested_quantity: float
    filled_quantity: float
    average_fill_price: float | None
    fee_usd: float
    realized_tca_bps: float | None
    attempts: int


@dataclass(frozen=True)
class LiveRunResult:
    run_id: int
    status: str
    intents: tuple[IntentExecutionResult, ...]
    postcheck_ok: bool
    postcheck: tuple[dict, ...]


def parse_order_response(payload: Any) -> PlacementOutcome:
    if not isinstance(payload, dict):
        return PlacementOutcome("rejected", error="non-object Hyperliquid order response")
    if str(payload.get("status", "")).lower() != "ok":
        return PlacementOutcome("rejected", error=str(payload.get("response") or payload))
    response = payload.get("response")
    data = response.get("data") if isinstance(response, dict) else None
    statuses = data.get("statuses") if isinstance(data, dict) else None
    if not isinstance(statuses, list) or len(statuses) != 1:
        return PlacementOutcome("rejected", error="Hyperliquid order response missing one status")
    item = statuses[0]
    if not isinstance(item, dict):
        return PlacementOutcome("rejected", error=str(item))
    if "error" in item:
        return PlacementOutcome("rejected", error=str(item["error"]))
    if "resting" in item and isinstance(item["resting"], dict):
        oid = item["resting"].get("oid")
        return PlacementOutcome("open", oid=int(oid) if oid is not None else None)
    if "filled" in item and isinstance(item["filled"], dict):
        filled = item["filled"]
        oid = filled.get("oid")
        return PlacementOutcome(
            "filled",
            oid=int(oid) if oid is not None else None,
            filled_quantity=float(filled.get("totalSz", 0.0)),
            average_price=float(filled["avgPx"]) if filled.get("avgPx") is not None else None,
        )
    return PlacementOutcome("rejected", error=f"unrecognized Hyperliquid order status: {item!r}")


def _order_status(payload: Any) -> OrderStatusOutcome:
    if not isinstance(payload, dict):
        return OrderStatusOutcome("unknown")
    top = str(payload.get("status") or "unknown")
    wrapper = payload.get("order")
    if top == "order" and isinstance(wrapper, dict):
        status = str(wrapper.get("status") or "unknown").lower()
        order = wrapper.get("order")
    elif isinstance(wrapper, dict):
        status = str(wrapper.get("status") or top).lower()
        order = wrapper.get("order", wrapper)
    else:
        return OrderStatusOutcome(top.lower())

    oid = None
    original_quantity = None
    remaining_quantity = None
    if isinstance(order, dict):
        try:
            if order.get("oid") is not None:
                oid = int(order["oid"])
        except (TypeError, ValueError):
            oid = None
        try:
            if order.get("origSz") is not None:
                original_quantity = float(order["origSz"])
            if order.get("sz") is not None:
                remaining_quantity = float(order["sz"])
        except (TypeError, ValueError):
            original_quantity = None
            remaining_quantity = None
    return OrderStatusOutcome(status, oid, original_quantity, remaining_quantity)


def _is_missing_order_status(status: str) -> bool:
    s = status.lower()
    return s in {"unknown", "unknownoid", "missing", "notfound", "not_found"}


def _is_bad_alo_error(error: str | None) -> bool:
    if not error:
        return False
    text = error.lower()
    return "alo" in text or "post only" in text or "post-only" in text


class HyperliquidSdkExecutionClient:
    """Thin signed-order adapter around Hyperliquid's official Python SDK.

    The SDK is loaded lazily so read-only/plan mode remains stdlib-only. On the
    Lubuntu deployment, ``deploy/install-live-deps.sh`` installs the pinned SDK
    into ``.vendor`` without creating a project virtual environment.
    """

    def __init__(
        self,
        *,
        private_key: str,
        account_address: str,
        subaccount_address: str,
        api_url: str,
        timeout_seconds: float,
    ):
        if not private_key:
            raise LiveExecutionError("HL_API_WALLET_PRIVATE_KEY is required for live execution")
        if not account_address:
            raise LiveExecutionError("HL_ACCOUNT_ADDRESS is required for live execution")
        try:
            from eth_account import Account
            from hyperliquid.exchange import Exchange
            from hyperliquid.utils.types import Cloid
        except ModuleNotFoundError as exc:
            raise LiveDependencyError(
                "Hyperliquid live dependencies are missing; run bash deploy/install-live-deps.sh"
            ) from exc

        try:
            wallet = Account.from_key(private_key)
        except Exception as exc:  # SDK/eth-account provides the detailed key error.
            raise LiveExecutionError(f"invalid Hyperliquid API-wallet private key: {exc}") from exc

        kwargs: dict[str, Any] = {
            "account_address": account_address,
            "timeout": timeout_seconds,
        }
        if subaccount_address:
            kwargs["vault_address"] = subaccount_address
        self._exchange = Exchange(wallet, api_url, **kwargs)
        self._Cloid = Cloid
        self.signer_address = wallet.address

    def place_alo(
        self,
        *,
        ticker: str,
        side: str,
        quantity: float,
        limit_price: float,
        reduce_only: bool,
        cloid: str,
    ) -> Any:
        return self._exchange.order(
            ticker,
            side.upper() == "BUY",
            float(quantity),
            float(limit_price),
            {"limit": {"tif": "Alo"}},
            reduce_only=bool(reduce_only),
            cloid=self._Cloid.from_str(cloid),
        )

    def cancel_by_cloid(self, *, ticker: str, cloid: str) -> Any:
        return self._exchange.cancel_by_cloid(ticker, self._Cloid.from_str(cloid))

    def schedule_cancel(self, time_ms: int | None = None) -> Any:
        return self._exchange.schedule_cancel(time_ms)


def validate_api_wallet(*, read_client: Any, execution_client: Any, master_account_address: str) -> dict:
    role = read_client.fetch_user_role(execution_client.signer_address)
    if not isinstance(role, dict) or str(role.get("role", "")).lower() != "agent":
        raise LiveExecutionError(
            "signing key is not an authorized Hyperliquid API wallet/agent; use an API wallet, not the master key"
        )
    data = role.get("data")
    owner = str(data.get("user", "")).lower() if isinstance(data, dict) else ""
    if owner != master_account_address.lower():
        raise LiveExecutionError(
            f"API wallet is authorized for {owner or 'an unknown account'}, not HL_ACCOUNT_ADDRESS"
        )
    return role


def _fills_for_intent(
    *,
    ledger: PreLiveLedger,
    read_client: Any,
    intent: dict,
    start_time_ms: int,
) -> list[dict]:
    attempts = ledger.attempts_for_intent(int(intent["id"]))
    attempt_by_oid = {
        int(a["exchange_oid"]): int(a["id"])
        for a in attempts
        if a.get("exchange_oid") is not None
    }
    if not attempt_by_oid:
        return ledger.fills_for_intent(int(intent["id"]))
    payload = read_client.fetch_user_fills_by_time(start_time_ms)
    if not isinstance(payload, list):
        raise LiveExecutionError("Hyperliquid userFillsByTime returned a non-list response")
    for fill in payload:
        if not isinstance(fill, dict):
            continue
        try:
            oid = int(fill.get("oid"))
        except (TypeError, ValueError):
            continue
        attempt_id = attempt_by_oid.get(oid)
        if attempt_id is None:
            continue
        ledger.record_fill(intent_id=int(intent["id"]), attempt_id=attempt_id, fill=fill)
    return ledger.fills_for_intent(int(intent["id"]))


def _fill_stats(intent: dict, fills: list[dict]) -> tuple[float, float | None, float, float | None]:
    total_qty = sum(float(f["quantity"]) for f in fills)
    average = None
    if total_qty > 0:
        average = sum(float(f["quantity"]) * float(f["price"]) for f in fills) / total_qty
    fee = sum(float(f.get("fee_usd") or 0.0) for f in fills)
    tca = None
    arrival = intent.get("arrival_price")
    if average is not None and arrival not in (None, 0):
        signed = 1.0 if str(intent["side"]).upper() == "BUY" else -1.0
        tca = signed * (average / float(arrival) - 1.0) * 10_000.0
    return total_qty, average, fee, tca


def _round_remaining(remaining: float, size_decimals: int | None) -> float:
    value = max(float(remaining), 0.0)
    if size_decimals is None:
        return value
    return round(value, int(size_decimals))


def _quantity_tolerance(size_decimals: int | None) -> float:
    if size_decimals is None:
        return 1e-12
    return max(1e-12, 10 ** (-int(size_decimals)) * 0.51)


def _terminal_confirmed_fill(state: OrderStatusOutcome) -> float:
    status = state.status.lower()
    if status == "open":
        raise LiveExecutionError("cannot reconcile fills while order is still open")
    confirmed = state.confirmed_filled_quantity
    if confirmed is not None:
        return confirmed
    if "rejected" in status:
        return 0.0
    raise LiveExecutionError(
        f"Hyperliquid order status {state.status!r} did not include origSz/sz; refusing to reissue without a confirmed fill quantity"
    )


def _sync_fills_to_confirmed(
    *,
    ledger: PreLiveLedger,
    read_client: Any,
    intent: dict,
    start_time_ms: int,
    confirmed_quantity: float,
    size_decimals: int | None,
    sleep_fn: Callable[[float], None],
) -> tuple[list[dict], float, float | None, float, float | None]:
    tolerance = _quantity_tolerance(size_decimals)
    last = ([], 0.0, None, 0.0, None)
    for poll in range(4):
        fills = _fills_for_intent(
            ledger=ledger, read_client=read_client, intent=intent, start_time_ms=start_time_ms
        )
        filled_qty, avg_fill, fee, tca = _fill_stats(intent, fills)
        last = (fills, filled_qty, avg_fill, fee, tca)
        if filled_qty + tolerance >= confirmed_quantity:
            if filled_qty > confirmed_quantity + tolerance:
                raise LiveExecutionError(
                    f"{intent['ticker']} fill ledger exceeds exchange-confirmed quantity; refusing further orders"
                )
            return last
        if poll < 3:
            sleep_fn(0.25)
    raise LiveExecutionError(
        f"{intent['ticker']} exchange confirms {confirmed_quantity:g} filled but userFillsByTime only reconciles {last[1]:g}; "
        "no order was reissued. Resume the run after the fill feed catches up."
    )


def _expected_position_quantity(*, ledger: PreLiveLedger, run_id: int, ticker: str) -> float:
    ticker_intents = [row for row in ledger.intents_for_run(run_id) if row["ticker"] == ticker]
    if not ticker_intents:
        return 0.0
    first = min(ticker_intents, key=lambda row: int(row["id"]))
    expected = float(first["current_quantity"] or 0.0)
    for row in ticker_intents:
        signed = 1.0 if str(row["side"]).upper() == "BUY" else -1.0
        expected += signed * sum(
            float(fill["quantity"]) for fill in ledger.fills_for_intent(int(row["id"]))
        )
    return expected


def _assert_position_matches_ledger(
    *, ledger: PreLiveLedger, run_id: int, intent: dict, snapshot: Any, min_order_usd: float
) -> None:
    ticker = str(intent["ticker"])
    expected = _expected_position_quantity(ledger=ledger, run_id=run_id, ticker=ticker)
    position = snapshot.positions.get(ticker)
    actual = 0.0 if position is None else float(position.quantity)
    market = snapshot.markets.get(ticker)
    price = market.mark_price if market is not None else (position.price if position is not None else 0.0)
    size_decimals = None if market is None else market.size_decimals
    tolerance = _quantity_tolerance(size_decimals)
    if price > 0:
        tolerance = max(tolerance, float(min_order_usd) / price)
    if abs(actual - expected) > tolerance + 1e-12:
        raise LiveExecutionError(
            f"{ticker} live position drifted from YOLO ledger: actual {actual:g}, expected {expected:g}; "
            "refusing stale-size execution. Rebuild the plan from current account state."
        )


def _postcheck(*, ledger: PreLiveLedger, run_id: int, snapshot: Any, min_order_usd: float) -> tuple[bool, tuple[dict, ...]]:
    rows: list[dict] = []
    all_ok = True
    for target in ledger.targets_for_run(run_id):
        ticker = target["ticker"]
        position = snapshot.positions.get(ticker)
        actual_qty = 0.0 if position is None else float(position.quantity)
        wanted_qty = float(target["destination_quantity"])
        market = snapshot.markets.get(ticker)
        price = market.mark_price if market is not None else (position.price if position is not None else 0.0)
        lot_qty = 10 ** (-market.size_decimals) if market is not None else 0.0
        tolerance_usd = max(float(min_order_usd), lot_qty * price * 1.01)
        error_usd = abs(actual_qty - wanted_qty) * price if price > 0 else math.inf
        ok = error_usd <= tolerance_usd + 1e-9
        all_ok = all_ok and ok
        rows.append(
            {
                "ticker": ticker,
                "actual_quantity": actual_qty,
                "destination_quantity": wanted_qty,
                "error_usd": error_usd,
                "tolerance_usd": tolerance_usd,
                "ok": ok,
            }
        )
    return all_ok, tuple(rows)


def execute_run(
    *,
    run_id: int,
    config: YoloConfig,
    read_client: Any,
    execution_client: Any,
    ledger: PreLiveLedger,
    archive: SignalArchive,
    sleep_fn: Callable[[float], None] = time.sleep,
    time_ms_fn: Callable[[], int] = lambda: int(time.time() * 1000),
    require_existing_session: bool = False,
) -> LiveRunResult:
    run = ledger.run_by_id(run_id)
    if run is None:
        raise LiveExecutionError(f"unknown rebalance run {run_id}")
    if not bool(run["risk_approved"]):
        raise LiveExecutionError("refusing to execute a run that did not pass the risk gate")
    if str(run["network"]) != config.normalized_network:
        raise LiveExecutionError("run network does not match current YOLO_NETWORK")
    if str(run["account_address"]).lower() != config.hyperliquid_user_address.lower():
        raise LiveExecutionError("run account does not match the current Hyperliquid trading account")
    if str(run.get("direction_mode") or "long_short") != config.normalized_direction_mode:
        raise LiveExecutionError("run direction mode does not match current YOLO_DIRECTION_MODE")

    now_ms = time_ms_fn()
    current_utc_date = datetime.fromtimestamp(now_ms / 1000.0, tz=timezone.utc).date().isoformat()
    if str(run["signal_date"]) != current_utc_date:
        raise LiveExecutionError(
            f"run signal date {run['signal_date']} is not current UTC date {current_utc_date}; refusing stale execution"
        )

    session = ledger.execution_session(run_id)
    if require_existing_session and session is None:
        raise LiveExecutionError(
            "refusing --resume-execution for a run that never entered live execution; "
            "run a fresh --live-data instead"
        )
    if session is not None and str(session.get("status") or "").lower() == "complete":
        raise LiveExecutionError(f"run {run_id} execution is already complete")

    ledger.reserve_execution(
        signal_date=str(run["signal_date"]),
        network=config.normalized_network,
        account_address=config.hyperliquid_user_address,
        run_id=run_id,
    )

    start_ms = int(session["started_at_ms"]) if session is not None else now_ms
    intents = ledger.intents_for_run(run_id)
    market_snapshot = read_client.fetch_account_snapshot()
    deadman_time = None
    deadman_armed = False
    if intents:
        deadman_time = now_ms + max(5, int(config.execution_deadman_seconds)) * 1000
        response = execution_client.schedule_cancel(deadman_time)
        if not isinstance(response, dict) or str(response.get("status", "")).lower() != "ok":
            raise LiveExecutionError(f"failed to arm Hyperliquid dead-man switch: {response!r}")
        deadman_armed = True
    ledger.start_execution_session(run_id=run_id, started_at_ms=start_ms, deadman_time_ms=deadman_time)

    results: list[IntentExecutionResult] = []
    execution_error: Exception | None = None
    try:
        for intent in intents:
            intent_id = int(intent["id"])
            total_requested = float(intent["quantity"])
            market = market_snapshot.markets.get(intent["ticker"])
            size_decimals = None if market is None else market.size_decimals
            qty_tolerance = _quantity_tolerance(size_decimals)
            existing_attempts = ledger.attempts_for_intent(intent_id)
            existing_by_no = {int(a["attempt_no"]): a for a in existing_attempts}

            # Reconcile every previously transmitted attempt before considering a
            # new order.  The exchange's origSz - sz is authoritative for how much
            # could have filled; the fill feed must catch up to that number before
            # another CLOID can be transmitted.
            confirmed_filled = 0.0
            for existing in existing_attempts:
                if not existing.get("transmitted_at_utc"):
                    continue
                db_status = str(existing.get("status") or "").lower()
                if "rejected" in db_status and existing.get("exchange_oid") is None:
                    continue

                status_payload = read_client.query_order_status_by_cloid(str(existing["cloid"]))
                state = _order_status(status_payload)
                if _is_missing_order_status(state.status):
                    raise LiveExecutionError(
                        f"cannot safely resume {intent['ticker']}: transmitted CLOID {existing['cloid']} has no resolvable order status"
                    )
                if state.status == "open":
                    cancel_response = execution_client.cancel_by_cloid(
                        ticker=str(intent["ticker"]), cloid=str(existing["cloid"])
                    )
                    if not isinstance(cancel_response, dict) or str(cancel_response.get("status", "")).lower() != "ok":
                        raise LiveExecutionError(
                            f"failed to cancel open {intent['ticker']} CLOID {existing['cloid']}: {cancel_response!r}"
                        )
                    sleep_fn(0.25)
                    status_payload = read_client.query_order_status_by_cloid(str(existing["cloid"]))
                    state = _order_status(status_payload)
                    if _is_missing_order_status(state.status) or state.status == "open":
                        raise LiveExecutionError(
                            f"{intent['ticker']} CLOID {existing['cloid']} is not confirmed canceled; dead-man remains the backstop"
                        )

                ledger.update_attempt(
                    int(existing["id"]),
                    status=state.status,
                    exchange_oid=state.oid,
                    response=status_payload,
                    completed=True,
                )
                confirmed_filled += _terminal_confirmed_fill(state)

            _, filled_qty, avg_fill, fee, tca = _sync_fills_to_confirmed(
                ledger=ledger,
                read_client=read_client,
                intent=intent,
                start_time_ms=start_ms,
                confirmed_quantity=confirmed_filled,
                size_decimals=size_decimals,
                sleep_fn=sleep_fn,
            )

            final_status = str(intent.get("status") or "planned")
            max_attempts = 1 + max(0, int(config.execution_max_reprices))
            for attempt_no in range(max_attempts):
                existing = existing_by_no.get(attempt_no)
                if existing is not None and existing.get("transmitted_at_utc"):
                    # Already reconciled above. Continue only if there is a safe,
                    # material remainder and another attempt number is available.
                    continue

                remaining = _round_remaining(total_requested - filled_qty, size_decimals)
                if remaining <= qty_tolerance:
                    final_status = "filled"
                    break

                if existing is not None:
                    # Crash between durable prepare and network send: reuse exactly
                    # the same CLOID/price/quantity. If earlier fills changed the
                    # required remainder, abandon this never-transmitted attempt
                    # instead of sending stale size.
                    prepared_qty = float(existing["quantity"])
                    if abs(prepared_qty - remaining) > qty_tolerance:
                        ledger.update_attempt(
                            int(existing["id"]),
                            status="abandoned",
                            error=f"prepared quantity {prepared_qty:g} no longer matches safe remainder {remaining:g}",
                            completed=True,
                        )
                        continue
                    attempt = existing
                    cloid = str(existing["cloid"])
                    limit_price = float(existing["limit_price"])
                else:
                    # Every newly prepared live attempt, including attempt zero,
                    # uses a fresh BBO. The persisted intent price is a preview/audit
                    # value only and is never allowed to become an hours-old live
                    # quote merely because the same plan was inspected earlier.
                    quote = read_client.fetch_bbo(str(intent["ticker"]))
                    limit_price = (
                        quote.bid_price
                        if str(intent["side"]).upper() == "BUY"
                        else quote.ask_price
                    )
                    cloid = (
                        str(intent["cloid"])
                        if attempt_no == 0
                        else ledger.make_reprice_cloid(
                            base_cloid=str(intent["cloid"]), attempt=attempt_no
                        )
                    )
                    attempt = ledger.prepare_attempt(
                        intent_id=intent_id,
                        attempt_no=attempt_no,
                        cloid=cloid,
                        quantity=remaining,
                        limit_price=limit_price,
                    )

                if remaining * float(limit_price) < config.min_order_usd:
                    if existing is not None:
                        ledger.update_attempt(
                            int(existing["id"]), status="dust", completed=True
                        )
                    final_status = "dust"
                    break

                pre_send_snapshot = read_client.fetch_account_snapshot()
                _assert_position_matches_ledger(
                    ledger=ledger,
                    run_id=run_id,
                    intent=intent,
                    snapshot=pre_send_snapshot,
                    min_order_usd=config.min_order_usd,
                )

                response = execution_client.place_alo(
                    ticker=str(intent["ticker"]),
                    side=str(intent["side"]),
                    quantity=remaining,
                    limit_price=limit_price,
                    reduce_only=bool(intent["reduce_only"]),
                    cloid=cloid,
                )
                ledger.mark_run_transmitted(run_id)
                ledger.mark_intent_transmitted(intent_id)
                outcome = parse_order_response(response)
                ledger.update_attempt(
                    int(attempt["id"]),
                    status=outcome.status,
                    exchange_oid=outcome.oid,
                    error=outcome.error,
                    response=response,
                    transmitted=True,
                    completed=outcome.status in {"filled", "rejected"},
                )

                if outcome.status == "rejected":
                    if _is_bad_alo_error(outcome.error) and attempt_no + 1 < max_attempts:
                        continue
                    ledger.update_intent_exchange_status(intent_id, status="rejected", exchange_oid=outcome.oid)
                    raise LiveExecutionError(
                        f"{intent['ticker']} ALO order rejected: {outcome.error or 'unknown error'}"
                    )

                if outcome.status == "filled":
                    if outcome.filled_quantity <= 0:
                        raise LiveExecutionError(
                            f"{intent['ticker']} placement reports filled without totalSz; refusing any reissue"
                        )
                    confirmed_filled += float(outcome.filled_quantity)
                else:
                    sleep_fn(max(0.0, float(config.execution_reprice_seconds)))
                    status_payload = read_client.query_order_status_by_cloid(cloid)
                    state = _order_status(status_payload)
                    if _is_missing_order_status(state.status):
                        raise LiveExecutionError(
                            f"{intent['ticker']} CLOID {cloid} has no resolvable status after placement; refusing any reissue"
                        )
                    if state.status == "open":
                        cancel_response = execution_client.cancel_by_cloid(
                            ticker=str(intent["ticker"]), cloid=cloid
                        )
                        if not isinstance(cancel_response, dict) or str(cancel_response.get("status", "")).lower() != "ok":
                            raise LiveExecutionError(
                                f"failed to cancel open {intent['ticker']} CLOID {cloid}: {cancel_response!r}"
                            )
                        sleep_fn(0.25)
                        status_payload = read_client.query_order_status_by_cloid(cloid)
                        state = _order_status(status_payload)
                        if _is_missing_order_status(state.status) or state.status == "open":
                            raise LiveExecutionError(
                                f"{intent['ticker']} CLOID {cloid} is not confirmed canceled; refusing any reissue"
                            )
                    ledger.update_attempt(
                        int(attempt["id"]),
                        status=state.status,
                        exchange_oid=state.oid if state.oid is not None else outcome.oid,
                        response=status_payload,
                        completed=True,
                    )
                    confirmed_filled += _terminal_confirmed_fill(state)

                _, filled_qty, avg_fill, fee, tca = _sync_fills_to_confirmed(
                    ledger=ledger,
                    read_client=read_client,
                    intent=intent,
                    start_time_ms=start_ms,
                    confirmed_quantity=confirmed_filled,
                    size_decimals=size_decimals,
                    sleep_fn=sleep_fn,
                )
                if filled_qty + qty_tolerance >= total_requested:
                    final_status = "filled"
                    break

            if final_status not in {"filled", "dust"}:
                remaining = max(total_requested - filled_qty, 0.0)
                reference_price = float(intent["limit_price"])
                if remaining <= qty_tolerance:
                    final_status = "filled"
                elif remaining * reference_price < config.min_order_usd:
                    final_status = "dust"
                else:
                    final_status = "unfilled"
            ledger.update_intent_exchange_status(intent_id, status=final_status)
            results.append(
                IntentExecutionResult(
                    intent_id=intent_id,
                    ticker=str(intent["ticker"]),
                    status=final_status,
                    requested_quantity=total_requested,
                    filled_quantity=filled_qty,
                    average_fill_price=avg_fill,
                    fee_usd=fee,
                    realized_tca_bps=tca,
                    attempts=len(ledger.attempts_for_intent(intent_id)),
                )
            )
            if final_status == "unfilled":
                raise LiveExecutionError(
                    f"{intent['ticker']} remained materially unfilled after {max_attempts} ALO attempt(s)"
                )

        final_snapshot = read_client.fetch_account_snapshot()
        postcheck_ok, postcheck = _postcheck(
            ledger=ledger,
            run_id=run_id,
            snapshot=final_snapshot,
            min_order_usd=config.min_order_usd,
        )
        intents_ok = all(r.status in {"filled", "dust"} for r in results)
        if not postcheck_ok or not intents_ok:
            raise LiveExecutionError("post-trade position verification failed")

        ledger.finish_execution_session(
            run_id=run_id,
            status="complete",
            verification={"postcheck": list(postcheck)},
        )
        archive.mark_snapshot_rebalanced(int(run["signal_snapshot_id"]))
        return LiveRunResult(run_id, "complete", tuple(results), True, postcheck)
    except Exception as exc:
        execution_error = exc
        ledger.finish_execution_session(run_id=run_id, status="attention", verification={"error": str(exc)})
        raise
    finally:
        if deadman_armed:
            try:
                execution_client.schedule_cancel(None)
            except Exception as exc:
                if execution_error is None:
                    ledger.finish_execution_session(
                        run_id=run_id,
                        status="attention",
                        verification={"error": f"failed to clear dead-man switch: {exc}"},
                    )
                    raise LiveExecutionError(f"failed to clear Hyperliquid dead-man switch: {exc}") from exc
