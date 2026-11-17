from app.collector import normalize_fill, save_funding_bill
from app.database import initialize, connect


def test_normalize_fill_preserves_decimal_strings_and_ids():
    source = {
        "instId": "BTC-USDT-SWAP", "tradeId": "123456", "ordId": "789",
        "instType": "SWAP", "side": "buy", "posSide": "long",
        "fillPx": "65000.120000", "fillSz": "0.0100", "fee": "-0.000001",
        "feeCcy": "BTC", "fillPnl": "12.3400", "fillPnlCcy": "USDT",
        "ts": "1760000000000", "unexpected": "ignored",
    }
    result = normalize_fill(source)
    assert result["fillPx"] == "65000.120000"
    assert result["fillSz"] == "0.0100"
    assert result["tradeId"] == "123456"
    assert "unexpected" not in result


def test_funding_expense_and_income_are_persisted_once(tmp_path):
    path = str(tmp_path / "monitor.db")
    initialize(path)
    expense = {
        "billId": "bill-1", "instId": "BTC-USDT-SWAP",
        "subType": "173", "ccy": "USDT", "pnl": "-1.25",
        "balChg": "-1.25", "ts": "1760000000000",
    }
    income = {
        "billId": "bill-2", "instId": "BTC-USDT-SWAP",
        "subType": "174", "ccy": "USDT", "pnl": "0.75",
        "balChg": "0.75", "ts": "1760000001000",
    }
    assert save_funding_bill(path, expense) is True
    assert save_funding_bill(path, expense) is False
    assert save_funding_bill(path, income) is True
    with connect(path) as db:
        rows = db.execute("SELECT amount FROM funding_events ORDER BY bill_id").fetchall()
    assert [row["amount"] for row in rows] == ["-1.25", "0.75"]
