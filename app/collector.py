"""Read-only REST collection and idempotent fill ingestion."""
from __future__ import annotations

import json
import asyncio
from datetime import datetime, timezone
from typing import Any

from app.database import connect, upsert_fill
from app.okx_client import ReadOnlyOKXClient


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def normalize_fill(fill: dict[str, Any]) -> dict[str, Any]:
    """Normalize an OKX fill while retaining exchange decimal strings verbatim."""
    keys = ("instId", "tradeId", "ordId", "instType", "side", "posSide",
            "fillPx", "fillSz", "fee", "feeCcy", "fillPnl", "fillPnlCcy", "ts")
    return {key: fill.get(key) for key in keys}


def save_funding_bill(db_path: str, bill: dict[str, Any]) -> bool:
    """Persist funding fee bills only; idempotent by billId + instrument."""
    subtype = str(bill.get("subType", ""))
    if subtype != "173" and "funding" not in str(bill.get("type", "")).lower():
        return False
    bill_id, inst_id, ts = bill.get("billId"), bill.get("instId") or "ACCOUNT", bill.get("ts")
    if not bill_id or not ts:
        return False
    with connect(db_path) as db:
        cursor = db.execute(
            """INSERT OR IGNORE INTO funding_events
               (bill_id, inst_id, ccy, amount, ts, raw_json)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (str(bill_id), str(inst_id), bill.get("ccy"), bill.get("balChg"),
             str(ts), json.dumps(bill, ensure_ascii=False)),
        )
        return cursor.rowcount == 1


async def collect_once(client: ReadOnlyOKXClient, db_path: str) -> dict[str, Any]:
    """Fetch read-only account datasets and persist fills/funding events.

    Raw account payloads are returned to the caller only and must not be logged.
    This function intentionally does not calculate consolidated PnL.
    """
    started = utc_now()
    balance, positions, orders, fills, bills = await asyncio.gather(
        client.account_balance(), client.positions(), client.pending_orders(),
        client.fills_history(), client.account_bills(),
    )
    inserted_fills = sum(1 for item in fills.get("data", [])
                         if upsert_fill(db_path, normalize_fill(item)))
    inserted_funding = sum(1 for item in bills.get("data", [])
                           if save_funding_bill(db_path, item))
    return {
        "started_at": started,
        "finished_at": utc_now(),
        "account_balance": balance.get("data", []),
        "positions": positions.get("data", []),
        "pending_orders": orders.get("data", []),
        "fills_received": len(fills.get("data", [])),
        "fills_inserted": inserted_fills,
        "funding_bills_inserted": inserted_funding,
        "pnl_status": "not_calculated_insufficient_accounting_context",
    }
