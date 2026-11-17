from app.collector import normalize_fill


def test_normalize_fill_preserves_decimal_strings_and_ids():
    source = {
        "instId": "BTC-USDT-SWAP",
        "tradeId": "123456",
        "ordId": "789",
        "instType": "SWAP",
        "side": "buy",
        "posSide": "long",
        "fillPx": "65000.120000",
        "fillSz": "0.0100",
        "fee": "-0.000001",
        "feeCcy": "BTC",
        "fillPnl": "12.3400",
        "fillPnlCcy": "USDT",
        "ts": "1760000000000",
        "unexpected": "ignored",
    }
    result = normalize_fill(source)
    assert result["fillPx"] == "65000.120000"
    assert result["fillSz"] == "0.0100"
    assert result["tradeId"] == "123456"
    assert "unexpected" not in result
