"""SQLite persistence with idempotent exchange fill keys."""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any


SCHEMA = """
CREATE TABLE IF NOT EXISTS fills_v2 (
    exchange TEXT NOT NULL,
    inst_id TEXT NOT NULL,
    trade_id TEXT NOT NULL,
    ord_id TEXT,
    inst_type TEXT,
    side TEXT,
    pos_side TEXT,
    fill_px TEXT,
    fill_sz TEXT,
    fee TEXT,
    fee_ccy TEXT,
    realized_pnl TEXT,
    realized_pnl_ccy TEXT,
    ts TEXT NOT NULL,
    raw_json TEXT,
    PRIMARY KEY(exchange, inst_id, trade_id)
);
CREATE INDEX IF NOT EXISTS idx_fills_v2_ts ON fills_v2(ts);
CREATE INDEX IF NOT EXISTS idx_fills_v2_inst_ts ON fills_v2(inst_id, ts);

CREATE TABLE IF NOT EXISTS sync_state (
    source TEXT PRIMARY KEY,
    cursor TEXT,
    last_success_at TEXT,
    last_error TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS funding_events (
    bill_id TEXT NOT NULL,
    inst_id TEXT NOT NULL,
    ccy TEXT,
    amount TEXT,
    ts TEXT NOT NULL,
    raw_json TEXT,
    PRIMARY KEY(bill_id, inst_id)
);

CREATE TABLE IF NOT EXISTS ingestion_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    records_in INTEGER NOT NULL DEFAULT 0,
    records_upserted INTEGER NOT NULL DEFAULT 0,
    error_code TEXT,
    error_message TEXT
);
"""


def connect(path: str | Path) -> sqlite3.Connection:
    db_path = Path(path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(db_path), timeout=15)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA busy_timeout=15000")
    return db


def initialize(path: str | Path) -> None:
    with connect(path) as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.executescript(SCHEMA)


def upsert_fill(path: str | Path, fill: dict[str, Any]) -> bool:
    """Insert a fill once. Returns True only when a new row was inserted."""
    required = ("instId", "tradeId", "ts")
    if any(not fill.get(k) for k in required):
        raise ValueError("Fill is missing instId, tradeId, or ts")

    values = (
        "OKX", str(fill["instId"]), str(fill["tradeId"]), fill.get("ordId"),
        fill.get("instType"), fill.get("side"), fill.get("posSide"),
        fill.get("fillPx"), fill.get("fillSz"), fill.get("fee"),
        fill.get("feeCcy"), fill.get("fillPnl"), fill.get("fillPnlCcy"),
        str(fill["ts"]), None,
    )
    with connect(path) as db:
        cursor = db.execute(
            """INSERT OR IGNORE INTO fills_v2 (
                exchange, inst_id, trade_id, ord_id, inst_type, side,
                pos_side, fill_px, fill_sz, fee, fee_ccy, realized_pnl,
                realized_pnl_ccy, ts, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            values,
        )
        return cursor.rowcount == 1


def get_sync_state(path: str | Path, source: str) -> dict[str, Any] | None:
    with connect(path) as db:
        row = db.execute(
            "SELECT source, cursor, last_success_at, last_error, updated_at "
            "FROM sync_state WHERE source = ?",
            (source,),
        ).fetchone()
    return dict(row) if row else None


def save_sync_state(
    path: str | Path,
    source: str,
    *,
    cursor: str | None,
    last_success_at: str | None,
    last_error: str | None,
    updated_at: str,
) -> None:
    with connect(path) as db:
        db.execute(
            """INSERT INTO sync_state (
                   source, cursor, last_success_at, last_error, updated_at
               ) VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(source) DO UPDATE SET
                   cursor = excluded.cursor,
                   last_success_at = excluded.last_success_at,
                   last_error = excluded.last_error,
                   updated_at = excluded.updated_at""",
            (source, cursor, last_success_at, last_error, updated_at),
        )


def start_ingestion_run(path: str | Path, source: str, started_at: str) -> int:
    with connect(path) as db:
        cursor = db.execute(
            """INSERT INTO ingestion_runs
               (source, started_at, status)
               VALUES (?, ?, 'running')""",
            (source, started_at),
        )
        return int(cursor.lastrowid)


def finish_ingestion_run(
    path: str | Path,
    run_id: int,
    *,
    finished_at: str,
    status: str,
    records_in: int,
    records_upserted: int,
    error_code: str | None = None,
    error_message: str | None = None,
) -> None:
    with connect(path) as db:
        db.execute(
            """UPDATE ingestion_runs
               SET finished_at = ?,
                   status = ?,
                   records_in = ?,
                   records_upserted = ?,
                   error_code = ?,
                   error_message = ?
               WHERE id = ?""",
            (
                finished_at, status, records_in, records_upserted,
                error_code, error_message, run_id,
            ),
        )
