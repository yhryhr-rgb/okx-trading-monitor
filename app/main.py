"""Offline-first local API. Does not connect to OKX automatically."""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException

from app.config import load_settings
from app.database import initialize, connect

SETTINGS = load_settings()
DB_PATH = SETTINGS.database_path


@asynccontextmanager
async def lifespan(app: FastAPI):
    initialize(DB_PATH)
    yield


app = FastAPI(
    title="OKX Trading Monitor",
    version="0.2.0",
    description="Read-only monitoring service; exchange connection requires an explicit operator action.",
    lifespan=lifespan,
)


@app.get("/health")
def health():
    return {
        "status": "ok",
        "mode": SETTINGS.mode,
        "api_configured": SETTINGS.api_configured,
        "account_connected": False,
    }


@app.get("/api/status")
def status():
    return {
        "service": "OKX Monitor",
        "version": app.version,
        "mode": SETTINGS.mode,
        "api_configured": SETTINGS.api_configured,
        "account_connected": False,
        "trading_enabled": False,
    }


@app.get("/api/fills")
def fills(limit: int = 50):
    if not 1 <= limit <= 500:
        raise HTTPException(status_code=400, detail="limit must be between 1 and 500")
    with connect(DB_PATH) as db:
        rows = db.execute(
            "SELECT exchange, inst_id, trade_id, inst_type, side, pos_side, "
            "fill_px, fill_sz, fee, fee_ccy, realized_pnl, realized_pnl_ccy, ts "
            "FROM fills_v2 ORDER BY ts DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]
