from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock, Mock

import pytest

import daily
import watch


DAY = date(2026, 11, 19)
START = daily.midnight(DAY)
END = daily.midnight(DAY + daily.DAY)
NOW = END + 8*3600


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(watch, "DB", tmp_path/"daily.db")
    monkeypatch.setattr(daily.time, "time", lambda: NOW)
    watch.init()


def bill(ident, **kw):
    return dict({"billId":str(ident),"ts":str(START*1000),"instType":"SWAP","instId":"BTC-USDT-SWAP",
                 "ccy":"USDT","pnl":"1.25","fee":"-0.05","type":"2","subType":"5"}, **kw)


def position(upl):
    return {"posId":"1","instType":"SWAP","instId":"BTC-USDT-SWAP","posSide":"net",
            "pos":"-1","ccy":"USDT","upl":upl,"cTime":"1"}


def test_due_day_and_snapshot_boundary(db):
    assert daily.last_due(NOW) == DAY
    assert daily.last_due(NOW-1) == DAY-daily.DAY
    watch.record([position("10")], END-31)
    watch.record([position("-12")], END)
    label, at, rows = daily.report_snapshot(DAY, NOW)
    assert label == "日末快照" and at == END-31 and rows[0][-1] == "10"
    with pytest.raises(ValueError):
        daily.report_snapshot(DAY-daily.DAY, NOW)
    watch.record([],NOW)
    assert "最新" in daily.report_snapshot(DAY-daily.DAY,NOW)[0]
    watch.record([], START-900)
    assert "超过" in daily.report_snapshot(DAY-daily.DAY,NOW)[0]


@pytest.mark.asyncio
async def test_bill_pagination_and_amounts(db):
    first = [bill(i) for i in range(200,100,-1)]
    second = [bill(101),bill(100, instType="SPOT"),bill(99, type="1"),
              bill(98,ts=str(END*1000)),bill(97,ts=str(START*1000-1)),
              bill(96,type="8",subType="173",pnl="-3",balChg="-3"),
              bill(95,type="34",subType="355",pnl="0",fee="0",balChg="2.1"),
              bill(94,type="7",subType="9",fee="0",balChg="-0.1"),
              bill(93,fee="0.25",pnl="-10")]
    calls = []
    async def get(path, params, authenticated):
        calls.append((path,params.copy(),authenticated))
        return {"data": (first,second)[len(calls)-1]}
    client = Mock(get=get)
    result = await daily.realized(client, DAY, NOW)
    assert result[("BTC-USDT-SWAP","USDT")] == [Decimal("115"),Decimal("-4.75"),Decimal("-3"),Decimal("2")]
    assert len(calls) == 2 and calls[1][1]["after"] == "101"
    assert calls[0] == ("/api/v5/account/bills", {"begin":START*1000,"end":END*1000-1,"limit":100},True)


@pytest.mark.asyncio
async def test_invalid_or_repeated_page_fails(db):
    client = Mock(get=AsyncMock(return_value={"data":[bill(42,pnl="garbled")]}))
    with pytest.raises(ValueError):
        await daily.realized(client,DAY,NOW)
    client.get.return_value = {"data":[bill(i) for i in range(100,0,-1)]}
    with pytest.raises(ValueError):
        await daily.realized(client,DAY,NOW+10*86400)
    assert client.get.call_args.args[0].endswith("-archive")


def test_mixed_currency_missing_value_and_flat(db):
    ledger = {("BTC","BTC"):[Decimal("0.01"),daily.ZERO,daily.ZERO,daily.ZERO]}
    snap = ("日末快照", END-2, [["ETH","long","1.2","ETH","2400"],["X","short",None,"USDT",None]])
    body = daily.render(DAY, ledger, {}, snap, NOW)
    assert body.count("无法完整折算") == 2 and "0.01 BTC" in body
    assert "1.2 ETH" in body and "盈亏暂缺" in body
    body = daily.render(DAY,ledger,{"BTC":Decimal("90000")},("日末快照",END-2,[]),NOW)
    assert "900 USDT" in body and "0 USDT" in body and "无未平仓仓位" in body


@pytest.mark.asyncio
async def test_mail_retries_reuse_report_and_restart_is_idempotent(db,monkeypatch):
    watch.record([position("-0.9")],END-5)
    client = Mock(get=AsyncMock(return_value={"data":[bill(1,pnl="-2")]}))
    mail = Mock(side_effect=[TimeoutError,None,None])
    monkeypatch.setattr(watch,"mail",mail)
    with pytest.raises(TimeoutError):
        await daily.run_due(client,{},NOW)
    watch.init()
    await daily.run_due(client,{},NOW)
    await daily.run_due(client,{},NOW)
    assert mail.call_count == 2 and mail.call_args_list[0] == mail.call_args_list[1]
    assert client.get.await_count == 1 and "-2.05 USDT" in mail.call_args.args[2]
    assert "-0.9 USDT" in mail.call_args.args[2]
    watch.record([],END+86400-10)
    await daily.run_due(client,{},NOW+86400)
    assert mail.call_count == 3
    assert str(DAY+daily.DAY) in mail.call_args.args[1]
