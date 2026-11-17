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
