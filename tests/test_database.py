from app.database import initialize, upsert_fill, connect


def test_fill_insert_is_idempotent(tmp_path):
    path = tmp_path / "monitor.db"
    initialize(path)
    fill = {"instId": "BTC-USDT-SWAP", "tradeId": "123",
            "ts": "1700000000000", "fillPx": "35000.10",
            "fillSz": "2", "fee": "-0.01"}
    assert upsert_fill(path, fill) is True
    assert upsert_fill(path, fill) is False
    with connect(path) as db:
        count = db.execute("SELECT COUNT(*) FROM fills_v2").fetchone()[0]
    assert count == 1
