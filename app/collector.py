"""Read-only REST collection and idempotent fill ingestion."""
from __future__ import annotations

import json
import asyncio
from datetime import datetime, timezone
from typing import Any

from app.database import (
    connect,
    finish_ingestion_run,
    get_sync_state,
    save_sync_state,
    start_ingestion_run,
    upsert_fill,
)
from app.okx_client import OKXError, ReadOnlyOKXClient


DEFAULT_FILL_INST_TYPES = ("SPOT", "MARGIN", "SWAP", "FUTURES", "OPTION", "EVENTS")


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
    if subtype not in {"173", "174"} and "funding" not in str(bill.get("type", "")).lower():
        return False
    bill_id, inst_id, ts = bill.get("billId"), bill.get("instId") or "ACCOUNT", bill.get("ts")
    if not bill_id or not ts:
        return False
    with connect(db_path) as db:
        cursor = db.execute(
            """INSERT OR IGNORE INTO funding_events
               (bill_id, inst_id, ccy, amount, ts, raw_json)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (str(bill_id), str(inst_id), bill.get("ccy"), bill.get("pnl", bill.get("balChg")),
             str(ts), json.dumps(bill, ensure_ascii=False)),
        )
        return cursor.rowcount == 1


async def _sync_fills_page(
    client: ReadOnlyOKXClient,
    db_path: str,
    *,
    inst_type: str,
    page_limit: int,
    max_pages: int,
) -> dict[str, Any]:
    source = f"fills_history:{inst_type}"
    state = get_sync_state(db_path, source)
    cursor = state["cursor"] if state and state.get("last_success_at") is None else None
    started = utc_now()
    run_id = start_ingestion_run(db_path, source, started)
    records_in = 0
    records_upserted = 0
    pages = 0

    try:
        while pages < max_pages:
            payload = await client.fills_history(inst_type, after=cursor, limit=page_limit)
            rows = payload.get("data", [])
            if not isinstance(rows, list):
                raise OKXError("OKX fills response data is not a list")
            pages += 1
            records_in += len(rows)

            for item in rows:
                if not isinstance(item, dict):
                    raise OKXError("OKX fills response contains a non-object record")
                if upsert_fill(db_path, normalize_fill(item)):
                    records_upserted += 1

            if len(rows) < page_limit:
                finished = utc_now()
                save_sync_state(
                    db_path, source, cursor=None, last_success_at=finished,
                    last_error=None, updated_at=finished,
                )
                finish_ingestion_run(
                    db_path, run_id, finished_at=finished, status="success",
                    records_in=records_in, records_upserted=records_upserted,
                )
                return {
                    "source": source, "status": "success", "pages": pages,
                    "records_in": records_in, "records_upserted": records_upserted,
                }

            next_cursor = rows[-1].get("billId") or rows[-1].get("tradeId")
            if not next_cursor or str(next_cursor) == str(cursor or ""):
                raise OKXError("Pagination cursor did not advance")
            cursor = str(next_cursor)
            now = utc_now()
            save_sync_state(
                db_path, source, cursor=cursor, last_success_at=None,
                last_error=None, updated_at=now,
            )

        now = utc_now()
        save_sync_state(
            db_path, source, cursor=cursor, last_success_at=None,
            last_error=None, updated_at=now,
        )
        finish_ingestion_run(
            db_path, run_id, finished_at=now, status="partial",
            records_in=records_in, records_upserted=records_upserted,
        )
        return {
            "source": source, "status": "partial", "pages": pages,
            "records_in": records_in, "records_upserted": records_upserted,
            "cursor": cursor,
        }
    except Exception as exc:
        code = "okx_error" if isinstance(exc, OKXError) else type(exc).__name__
        message = str(exc) if isinstance(exc, OKXError) else type(exc).__name__
        now = utc_now()
        save_sync_state(
            db_path, source, cursor=cursor, last_success_at=None,
            last_error=code, updated_at=now,
        )
        finish_ingestion_run(
            db_path, run_id, finished_at=now, status="error",
            records_in=records_in, records_upserted=records_upserted,
            error_code=code, error_message=message,
        )
        raise


async def _sync_funding_bills(
    client: ReadOnlyOKXClient,
    db_path: str,
    *,
    page_limit: int,
    max_pages: int,
    archive: bool = True,
) -> dict[str, Any]:
    source = "funding_bills_archive" if archive else "funding_bills"
    state = get_sync_state(db_path, source)
    cursor = state["cursor"] if state and state.get("last_success_at") is None else None
    started = utc_now()
    run_id = start_ingestion_run(db_path, source, started)
    records_in = 0
    records_upserted = 0
    pages = 0
    fetch_page = client.account_bills_archive if archive else client.account_bills

    try:
        while pages < max_pages:
            payload = await fetch_page(after=cursor, limit=page_limit)
            rows = payload.get("data", [])
            if not isinstance(rows, list):
                raise OKXError("OKX bills response data is not a list")
            pages += 1
            records_in += len(rows)

            for item in rows:
                if not isinstance(item, dict):
                    raise OKXError("OKX bills response contains a non-object record")
                if save_funding_bill(db_path, item):
                    records_upserted += 1

            if len(rows) < page_limit:
                finished = utc_now()
                save_sync_state(
                    db_path, source, cursor=None, last_success_at=finished,
                    last_error=None, updated_at=finished,
                )
                finish_ingestion_run(
                    db_path, run_id, finished_at=finished, status="success",
                    records_in=records_in, records_upserted=records_upserted,
                )
                return {
                    "source": source, "status": "success", "pages": pages,
                    "records_in": records_in, "records_upserted": records_upserted,
                }

            next_cursor = rows[-1].get("billId")
            if not next_cursor or str(next_cursor) == str(cursor or ""):
                raise OKXError("Pagination cursor did not advance")
            cursor = str(next_cursor)
            now = utc_now()
            save_sync_state(
                db_path, source, cursor=cursor, last_success_at=None,
                last_error=None, updated_at=now,
            )

        now = utc_now()
        save_sync_state(
            db_path, source, cursor=cursor, last_success_at=None,
            last_error=None, updated_at=now,
        )
        finish_ingestion_run(
            db_path, run_id, finished_at=now, status="partial",
            records_in=records_in, records_upserted=records_upserted,
        )
        return {
            "source": source, "status": "partial", "pages": pages,
            "records_in": records_in, "records_upserted": records_upserted,
            "cursor": cursor,
        }
    except Exception as exc:
        code = "okx_error" if isinstance(exc, OKXError) else type(exc).__name__
        message = str(exc) if isinstance(exc, OKXError) else type(exc).__name__
        now = utc_now()
        save_sync_state(
            db_path, source, cursor=cursor, last_success_at=None,
            last_error=code, updated_at=now,
        )
        finish_ingestion_run(
            db_path, run_id, finished_at=now, status="error",
            records_in=records_in, records_upserted=records_upserted,
            error_code=code, error_message=message,
        )
        raise


async def sync_historical_read_data(
    client: ReadOnlyOKXClient,
    db_path: str,
    *,
    inst_types: tuple[str, ...] = DEFAULT_FILL_INST_TYPES,
    page_limit: int = 100,
    max_pages: int = 100,
    archive_funding: bool = True,
) -> dict[str, Any]:
    """Backfill fills and funding bills with durable per-source checkpoints.

    A source checkpoint is written only after a page has been persisted. If the
    process stops mid-backfill, the next call resumes from the last committed
    cursor. A completed source starts from the newest page on the next call so
    new records are still discovered.
    """
    if not 1 <= page_limit <= 100:
        raise ValueError("page_limit must be between 1 and 100")
    if max_pages < 1:
        raise ValueError("max_pages must be at least 1")
    if not inst_types:
        raise ValueError("inst_types must not be empty")

    results = []
    for inst_type in inst_types:
        results.append(
            await _sync_fills_page(
                client, db_path, inst_type=inst_type,
                page_limit=page_limit, max_pages=max_pages,
            )
        )
    results.append(
        await _sync_funding_bills(
            client, db_path, page_limit=page_limit, max_pages=max_pages,
            archive=archive_funding,
        )
    )
    return {"status": "ok", "sources": results}


async def collect_once(
    client: ReadOnlyOKXClient,
    db_path: str,
    *,
    inst_types: tuple[str, ...] = DEFAULT_FILL_INST_TYPES,
) -> dict[str, Any]:
    """Fetch current account snapshots and persist the newest read-only pages.

    Raw account payloads are returned to the caller only and must not be logged.
    This function intentionally does not calculate consolidated PnL.
    """
    if not inst_types:
        raise ValueError("inst_types must not be empty")

    started = utc_now()
    snapshot_tasks = (
        client.account_balance(),
        client.positions(),
        client.pending_orders(),
        *(client.fills_history(inst_type) for inst_type in inst_types),
        client.account_bills(),
    )
    results = await asyncio.gather(*snapshot_tasks)
    balance, positions, orders = results[:3]
    fills_payloads = results[3:-1]
    bills = results[-1]

    fills_received = 0
    fills_inserted = 0
    for payload in fills_payloads:
        rows = payload.get("data", [])
        if not isinstance(rows, list):
            raise OKXError("OKX fills response data is not a list")
        fills_received += len(rows)
        fills_inserted += sum(
            1 for item in rows if upsert_fill(db_path, normalize_fill(item))
        )

    bills_rows = bills.get("data", [])
    if not isinstance(bills_rows, list):
        raise OKXError("OKX bills response data is not a list")
    funding_bills_inserted = sum(
        1 for item in bills_rows if save_funding_bill(db_path, item)
    )

    return {
        "started_at": started,
        "finished_at": utc_now(),
        "account_balance": balance.get("data", []),
        "positions": positions.get("data", []),
        "pending_orders": orders.get("data", []),
        "fills_received": fills_received,
        "fills_inserted": fills_inserted,
        "funding_bills_inserted": funding_bills_inserted,
        "pnl_status": "not_calculated_insufficient_accounting_context",
    }
