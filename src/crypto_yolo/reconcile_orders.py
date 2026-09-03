from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .prelive import PreLiveLedger


@dataclass(frozen=True)
class ReconcileResult:
    checked: int
    updated: int


def _extract_status(payload: Any) -> tuple[str, int | None]:
    if not isinstance(payload, dict):
        return "unknown", None

    top_status = str(payload.get("status") or "unknown")
    wrapper = payload.get("order")
    status = top_status
    order_obj: Any = wrapper

    # Hyperliquid has returned both a direct status wrapper and an
    # orderStatus-style {status: "order", order: {status, order}} shape over
    # time. Accept either so restart reconciliation is resilient to the
    # currently documented response form.
    if top_status == "order" and isinstance(wrapper, dict):
        status = str(wrapper.get("status") or top_status)
        order_obj = wrapper.get("order", wrapper)
    elif isinstance(wrapper, dict) and wrapper.get("status") is not None:
        status = str(wrapper.get("status"))
        order_obj = wrapper.get("order", wrapper)

    oid = None
    if isinstance(order_obj, dict):
        try:
            oid = int(order_obj.get("oid")) if order_obj.get("oid") is not None else None
        except (TypeError, ValueError):
            oid = None
    return status.lower(), oid


def reconcile_transmitted_intents(*, client, ledger: PreLiveLedger) -> ReconcileResult:
    pending = ledger.pending_transmitted_intents()
    updated = 0
    for row in pending:
        payload = client.query_order_status_by_cloid(row["cloid"])
        status, oid = _extract_status(payload)
        ledger.update_intent_exchange_status(int(row["id"]), status=status, exchange_oid=oid)
        updated += 1
    return ReconcileResult(len(pending), updated)
