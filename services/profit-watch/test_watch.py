import asyncio
import json
import sqlite3
from decimal import Decimal
from unittest.mock import patch, MagicMock
import httpx
import pytest
import watch

@pytest.fixture
def instance(tmp_path,monkeypatch):
    monkeypatch.setattr(watch,"DB",tmp_path/"monitor.db")
    monkeypatch.setattr(watch,"RECIPIENT_FILE",tmp_path/"recipient")
    watch.RECIPIENT_FILE.write_text("owner@example.test")
    watch.init()

def pos(upl,qty="2",ident="1",**extra):
    return dict({"posId":ident,"instType":"SWAP","instId":"ETH-USDT-SWAP","posSide":"net","pos":qty,"ccy":"USDT","upl":upl,"cTime":"100"},**extra)

def events():
    with watch.db() as c:
        return [tuple(r) for r in c.execute("SELECT at,side,upl,sent FROM events ORDER BY id")]

def test_crossings_close_reopen_and_restart(instance):
    watch.record([pos("-3")],1)
    watch.record([pos("0")],2)
    watch.record([pos("0.000001")],3)
    watch.init()
    watch.record([pos("2")],4)
    watch.record([pos("")],5)
    watch.record([pos("3")],6)
    watch.record([pos("0")],7)
    watch.record([pos("4")],8)
    watch.record([pos("99","0")],9)
    watch.record([pos("5")],10)
    watch.record([pos("6","-2")],11)
    assert [e[:2] for e in events()]==[(3,"long"),(8,"long"),(10,"long"),(11,"short")]

def test_partial_invalid_preserves_snapshot(instance):
    watch.record([pos("4")],1)
    with pytest.raises(ValueError):
        watch.record([pos("0"),{}],2)
    with pytest.raises(ValueError):
        watch.record([pos("0"),pos("10")],3)
    watch.record([pos("8")],4)
    assert len(events())==1

def test_missing_unrealized_and_numeric_precision(instance):
    watch.record([pos("NaN")],1)
    watch.record([pos("-0")],2)
    watch.record([pos("1e-30")],3)
    assert len(events())==1
    assert watch.position(pos("2",instType="MARGIN",instId="BTC-USDT",posCcy="USDT"))[2]=="short"

def test_outbox_retries_and_deduplicates(instance):
    c={"smtp_port":465,"smtp_code":"dummy"}
    watch.record([pos("12"),pos("13","-1","2")],int(watch.time.time()))
    with patch("watch.mail",side_effect=TimeoutError):
        with pytest.raises(TimeoutError):watch.send_pending(c)
    assert [e[-1] for e in events()]==[None,None]
    with patch("watch.smtplib.SMTP_SSL") as smtp:
        smtp.return_value.send_message.return_value={}
        assert watch.send_pending(c)
        assert not watch.send_pending(c)
        smtp.return_value.login.assert_called_once_with("owner@example.test","dummy")
        msg=smtp.return_value.send_message.call_args.args[0]
        assert msg["To"]=="owner@example.test" and "浮盈 13 USDT" in msg.get_content()
        assert "dummy" not in msg.as_string()
    assert all(e[-1] for e in events())

def test_threshold_per_position_and_rearm(instance):
    now = int(watch.time.time())
    with patch("watch.mail") as mail:
        for first in ("-10", "0", "6", "9.99999999999999999999999999999"):
            watch.record([pos(first), pos("6",ident="2")], now)
            assert not watch.send_pending({})
        watch.record([pos("10.00")], now)
        assert watch.send_pending({})
        watch.init()
        for amount in ("12", "", "10.0001"):
            watch.record([pos(amount)], now)
            assert not watch.send_pending({})
        watch.record([pos("9.99")], now)
        watch.record([pos("10")], now)
        assert watch.send_pending({})
        watch.record([], now)
        watch.record([pos("11")], now)
        assert watch.send_pending({})
        assert mail.call_count == 3

def test_smtp_login_check_does_not_send(instance):
    with patch("watch.smtplib.SMTP_SSL") as smtp:
        with watch.smtp_connection({"smtp_port":465, "smtp_code":"dummy"}, "owner@example.test"):
            pass
        smtp.return_value.login.assert_called_once_with("owner@example.test","dummy")
        smtp.return_value.send_message.assert_not_called()

def test_old_backlog_and_retries_use_latest_position(instance):
    import portal
    now = int(watch.time.time())
    with watch.db() as c:
        c.execute("DROP TABLE email_thresholds")
        for amt in ("1", "20"):
            c.execute("INSERT INTO events(at,instrument,side,upl,ccy) VALUES(?,?,?,?,?)",
                      (now, "old", "long", amt, "USDT"))
    watch.init()
    with patch("watch.mail") as mail:
        watch.record([pos("11")],now-100)
        assert not watch.send_pending({})
        watch.record([pos("")],now)
        assert not watch.send_pending({})
        watch.record([pos("9")],now)
        assert not watch.send_pending({})
        watch.record([pos("12")],now)
        watch.record([],now)
        assert not watch.send_pending({})
        assert portal.status()["pending_emails"] == 0
        assert "仅面板记录" in portal.dashboard().body.decode()
        watch.record([pos("10")],now)
        assert watch.send_pending({})
        mail.assert_called_once()
        assert "old" not in mail.call_args.args[2]

@pytest.mark.asyncio
async def test_currency_conversion_and_missing_or_stale_quote(instance):
    now = int(watch.time.time())
    snap = [pos("0.00025", ccy="BTC"), pos("99", ident="2", ccy="XYZ")]
    class Client:
        async def get(self, path, params):
            assert path == "/api/v5/market/index-tickers" and params == {"quoteCcy":"USDT"}
            return {"data":[{"instId":"BTC-USDT","idxPx":"40000","ts":str(now*1000)},
                            {"instId":"XYZ-USDT","idxPx":"1","ts":str((now-100)*1000)}]}
    rates = await watch.conversion_rates(Client(),snap)
    assert rates == {"BTC":Decimal("40000")}
    with patch("watch.mail") as mail:
        watch.record(snap,now,{})
        assert not watch.send_pending({})
        watch.record(snap,now,rates)
        watch.record(snap,now,{})
        assert not watch.send_pending({})
        watch.record(snap,now,rates)
        assert watch.send_pending({})
        assert "10.00000 USDT" in mail.call_args.args[2]
        watch.record(snap,now,rates)
        assert not watch.send_pending({})
        mail.assert_called_once()

@pytest.mark.asyncio
@pytest.mark.parametrize("perm,ok",[("read_only",True),("read_only,trade",False),("read_only,withdraw",False),("",False)])
async def test_only_read_only_keys(instance,perm,ok):
    def fake(req):
        assert req.url.path=="/api/v5/account/config" and req.method=="GET"
        return httpx.Response(200,json={"code":"0","data":[{"perm":perm,"uid":"example"}]})
    async with httpx.AsyncClient(base_url=watch.BASE,transport=httpx.MockTransport(fake)) as h:
        c=watch.client_for({"key":"test","secret":"test","passphrase":"test"},h)
        if ok:
            watch.enforce_owner(await watch.verify_key(c),save=True)
            with pytest.raises(PermissionError):watch.enforce_owner("different")
        else:
            with pytest.raises(PermissionError):await watch.verify_key(c)

def test_dashboard_escape_and_stale(instance):
    import portal
    watch.record([pos("7",instId="<script>bad()")],1)
    page=portal.dashboard().body.decode()
    assert "<script>bad()" not in page and "&lt;script&gt;" in page
    assert portal.status()["account_connected"] is False

@pytest.mark.asyncio
async def test_trades_resume_and_deduplicate(instance):
    class Client:
        top=100
        async def get(self,path,params,authenticated):
            assert path=="/api/v5/trade/fills" and authenticated
            start=int(params.get("after",self.top+1))-1
            return {"data":[dict(billId=str(i),tradeId=str(i),instId="X",ts=str(i)) for i in range(start,max(0,start-100),-1)]}
    client=Client()
    await watch.recent_fills(client)
    client.top=550
    await watch.recent_fills(client)
    with watch.db() as c:
        assert c.execute("SELECT v FROM meta WHERE k='after'").fetchone()[0]=="251"
    await watch.recent_fills(client)
    await watch.recent_fills(client)
    with watch.db() as c:
        assert c.execute("SELECT COUNT(*) FROM fills").fetchone()[0]==550
        assert c.execute("SELECT v FROM meta WHERE k='watermark'").fetchone()[0]=="550"
