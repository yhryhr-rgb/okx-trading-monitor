from app.analytics import trade_metrics, max_drawdown


def test_incomplete_history_never_reports_pnl():
    result = trade_metrics(["10", "-5"], history_complete=False, currency_consistent=True)
    assert result["status"] == "incomplete"
    assert result["net_pnl"] is None


def test_trade_metrics_when_inputs_complete():
    result = trade_metrics(["10", "-5", "5"], history_complete=True, currency_consistent=True)
    assert result["status"] == "complete_for_supplied_trades"
    assert float(result["win_rate"]) == 2 / 3
    assert result["profit_factor"] == "3"


def test_max_drawdown():
    result = max_drawdown(["100", "120", "90", "130"], history_complete=True)
    assert result["status"] == "complete_for_supplied_curve"
    assert result["max_drawdown"] == "0.25"
