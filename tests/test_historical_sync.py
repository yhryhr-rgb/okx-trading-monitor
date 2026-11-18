import pytest

from app.collector import sync_historical_read_data
from app.database import connect, get_sync_state, initialize


class FakeClient:
    def __init__(self):
        self.fill_calls = []
        self.bill_calls = []

    async def fills_history(self, inst_type, *, after=None, limit=100):
        self.fill_calls.append((inst_type, after, limit))
        if after is None:
            return {
                "code": "0",
                "data": [
                    {
                        "instId": "BTC-USDT-SWAP",
                        "tradeId": "t2",
                        "billId": "b2",
                        "ts": "2",
                    },
                    {
                        "instId": "BTC-USDT-SWAP",
                        "tradeId": "t1",
                        "billId": "b1",
                        "ts": "1",
                    },
                ],
            }
        return {
            "code": "0",
            "data": [
                {
                    "instId": "BTC-USDT-SWAP",
                    "tradeId": "t0",
                    "billId": "b0",
                    "ts": "0",
                },
            ],
        }

    async def account_bills_archive(self, *, after=None, limit=100):
        self.bill_calls.append((after, limit))
        if after is None:
            return {
                "code": "0",
                "data": [
                    {
                        "billId": "fb2",
                        "instId": "BTC-USDT-SWAP",
                        "subType": "173",
                        "ccy": "USDT",
                        "pnl": "-1.0",
                        "ts": "2",
                    },
                    {
                        "billId": "fb1",
                        "instId": "BTC-USDT-SWAP",
                        "subType": "174",
                        "ccy": "USDT",
                        "pnl": "0.5",
                        "ts": "1",
                    },
                ],
            }
        return {
            "code": "0",
            "data": [
                {
                    "billId": "fb0",
                    "instId": "BTC-USDT-SWAP",
                    "subType": "173",
                    "ccy": "USDT",
                    "pnl": "-0.25",
                    "ts": "0",
                },
            ],
        }


@pytest.mark.asyncio
async def test_historical_sync_resumes_from_durable_checkpoints(tmp_path):
    path = str(tmp_path / "monitor.db")
    initialize(path)
    client = FakeClient()

    first = await sync_historical_read_data(
        client,
        path,
        inst_types=("SWAP",),
        page_limit=2,
        max_pages=1,
    )
    assert all(item["status"] == "partial" for item in first["sources"])
    assert get_sync_state(path, "fills_history:SWAP")["cursor"] == "b1"
    assert get_sync_state(path, "funding_bills_archive")["cursor"] == "fb1"

    second = await sync_historical_read_data(
        client,
        path,
        inst_types=("SWAP",),
        page_limit=2,
        max_pages=2,
    )
    assert all(item["status"] == "success" for item in second["sources"])
    assert client.fill_calls == [
        ("SWAP", None, 2),
        ("SWAP", "b1", 2),
    ]
    assert client.bill_calls == [
        (None, 2),
        ("fb1", 2),
    ]

    assert get_sync_state(path, "fills_history:SWAP")["cursor"] is None
    assert get_sync_state(path, "fills_history:SWAP")["last_success_at"]
    assert get_sync_state(path, "funding_bills_archive")["cursor"] is None

    with connect(path) as db:
        assert db.execute("SELECT COUNT(*) FROM fills_v2").fetchone()[0] == 3
        assert db.execute("SELECT COUNT(*) FROM funding_events").fetchone()[0] == 3
        runs = db.execute(
            "SELECT source, status, records_in, records_upserted "
            "FROM ingestion_runs ORDER BY id"
        ).fetchall()

    assert [(row["source"], row["status"]) for row in runs] == [
        ("fills_history:SWAP", "partial"),
        ("funding_bills_archive", "partial"),
        ("fills_history:SWAP", "success"),
        ("funding_bills_archive", "success"),
    ]
