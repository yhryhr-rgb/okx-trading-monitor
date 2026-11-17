"""Conservative performance metrics; incomplete input stays explicitly incomplete."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Iterable


def D(value: object) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal("0")


def trade_metrics(net_trade_pnls: Iterable[str], *, history_complete: bool,
                  currency_consistent: bool) -> dict[str, object]:
    values = [D(v) for v in net_trade_pnls]
    if not history_complete or not currency_consistent or not values:
        return {
            "status": "incomplete", "trade_count": len(values),
            "win_rate": None, "profit_factor": None, "net_pnl": None,
            "reason": "requires_complete_history_and_single_reporting_currency",
        }
    wins = [v for v in values if v > 0]
    losses = [v for v in values if v < 0]
    gross_profit = sum(wins, Decimal("0"))
    gross_loss = -sum(losses, Decimal("0"))
    pf = str(gross_profit / gross_loss) if gross_loss > 0 else ("infinite" if gross_profit > 0 else None)
    return {
        "status": "complete_for_supplied_trades",
        "trade_count": len(values),
        "win_rate": str(Decimal(len(wins)) / Decimal(len(values))),
        "profit_factor": pf,
        "net_pnl": str(sum(values, Decimal("0"))),
    }


def max_drawdown(equity_curve: Iterable[str], *, history_complete: bool) -> dict[str, object]:
    values = [D(v) for v in equity_curve]
    if not history_complete or len(values) < 2:
        return {"status": "incomplete", "max_drawdown": None}
    peak, worst = values[0], Decimal("0")
    for value in values:
        peak = max(peak, value)
        if peak > 0:
            worst = max(worst, (peak - value) / peak)
    return {"status": "complete_for_supplied_curve", "max_drawdown": str(worst)}
