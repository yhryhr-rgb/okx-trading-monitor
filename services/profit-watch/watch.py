"""Read-only position sampling, transition tracking, and email delivery."""
import asyncio
import hashlib
import json
import os
import smtplib
import sqlite3
import ssl
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, localcontext
from email.message import EmailMessage
from email.utils import formatdate
from pathlib import Path

import httpx
from app.okx_client import OKXCredentials, OKXError, ReadOnlyOKXClient

BASE = "https://openapi.okx.com"
RECIPIENT_FILE = Path("/etc/okx-profit-watch/recipient")
DB = Path(os.environ.get("WATCH_DB", "/var/lib/okx-monitor-rest/profit-watch.sqlite3"))
INTERVAL = 30
EMAIL_THRESHOLD = Decimal("10")
MAX_SAMPLE_AGE = 2 * INTERVAL
TZ = timezone(timedelta(hours=8))

def stamp(t):
    return datetime.fromtimestamp(t, TZ).strftime("%Y-%m-%d %H:%M:%S")

@contextmanager
def db():
    cn = sqlite3.connect(DB, timeout=5)
    cn.row_factory = sqlite3.Row
    try:
        with cn:
            yield cn
    finally:
        cn.close()

def init():
    with db() as c:
        c.executescript("""
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS positions(k TEXT PRIMARY KEY, instrument TEXT, side TEXT,
          qty TEXT, upl TEXT, ccy TEXT, positive INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, at INTEGER NOT NULL,
          instrument TEXT, side TEXT, upl TEXT, ccy TEXT, sent INTEGER);
        CREATE INDEX IF NOT EXISTS pending_events ON events(sent,id);
        CREATE TABLE IF NOT EXISTS email_thresholds(
          position_key TEXT PRIMARY KEY, event_id INTEGER NOT NULL UNIQUE,
          trigger_usdt TEXT NOT NULL, confirmed INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
        CREATE TABLE IF NOT EXISTS fills(instrument TEXT, trade TEXT, at INTEGER, side TEXT,
          price TEXT, qty TEXT, PRIMARY KEY(instrument,trade));
        CREATE INDEX IF NOT EXISTS fills_at ON fills(at);
        CREATE TABLE IF NOT EXISTS daily_snapshots(day TEXT PRIMARY KEY, sampled_at INTEGER NOT NULL,
          positions TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS daily_reports(day TEXT PRIMARY KEY, body TEXT NOT NULL, sent_at INTEGER);
        """)

def meta(c, k, value):
    c.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", (k, str(value)))

def number(v):
    try:
        n = Decimal(str(v))
        return n if n.is_finite() else None
    except InvalidOperation:
        return None

def position(row):
    inst, ident, side = row.get("instId"), row.get("posId"), row.get("posSide")
    qty = number(row.get("pos"))
    if qty is None or not inst or not ident or side not in ("net","long","short"):
        raise ValueError("incomplete position")
    if qty == 0:
        return None
    if side == "net" and row.get("instType") in ("FUTURES","SWAP","OPTION"):
        side = "long" if qty > 0 else "short"
    elif side == "net" and row.get("instType") == "MARGIN":
        base, _, quote = inst.partition("-")
        side = {base:"long", quote:"short"}.get(row.get("posCcy"), "net")
    key = json.dumps([ident, row.get("cTime"), side])
    return key, inst, side, str(qty), number(row.get("upl")), str(row.get("ccy") or "")

async def conversion_rates(client, snapshot):
    currencies = {r.get("ccy") for r in snapshot} - {None, "", "USDT"}
    if not currencies:
        return {}
    try:
        reply = await asyncio.wait_for(client.get("/api/v5/market/index-tickers", {"quoteCcy": "USDT"}), 5)
        tickers = rows(reply)
    except (TimeoutError, asyncio.TimeoutError, httpx.HTTPError, OKXError, ValueError):
        return {}
    now_ms = int(time.time() * 1000)
    rates = {}
    for ticker in tickers:
        base, sep, quote = str(ticker.get("instId", "")).rpartition("-")
        if not sep or quote != "USDT" or base not in currencies:
            continue
        price, ts = number(ticker.get("idxPx")), number(ticker.get("ts"))
        if price is not None and price > 0 and ts is not None and 0 <= now_ms-ts <= MAX_SAMPLE_AGE*1000:
            rates[base] = price
    return rates

def email_valuation(pnl, ccy, rates):
    if pnl is None:
        return None
    if ccy == "USDT" or pnl == 0:
        return pnl
    rate = rates.get(ccy)
    if rate is None:
        return None
    with localcontext() as ctx:
        ctx.prec = max(ctx.prec, len(pnl.as_tuple().digits) + len(rate.as_tuple().digits))
        return pnl * rate

def record(rows, now, rates=None):
    # Validate a complete snapshot before changing crossing state.
    current = [p for r in rows if (p := position(r)) is not None]
    if len({p[0] for p in current}) != len(current):
        raise ValueError("duplicate position")
    with db() as c:
        c.execute("BEGIN IMMEDIATE")
        prev = dict(c.execute("SELECT k,positive FROM positions"))
        episodes = {r["position_key"]: r for r in c.execute("SELECT * FROM email_thresholds")}
        c.execute("DELETE FROM positions")
        c.execute("DELETE FROM email_thresholds")
        daily_positions = []
        for key, inst, side, qty, pnl, ccy in current:
            positive = prev.get(key, 0) if pnl is None else int(pnl > 0)
            amount = str(pnl) if pnl is not None else None
            usdt = email_valuation(pnl, ccy, rates or {})
            daily_positions.append((inst, side, amount, ccy, str(usdt) if usdt is not None else None))
            # A nonpositive PnL is below threshold in every settlement currency.
            above = None
            if usdt is not None:
                above = usdt >= EMAIL_THRESHOLD
            elif pnl is not None and pnl <= 0:
                above = False
            episode = episodes.get(key)
            entered = above is True and episode is None
            if entered or (pnl is not None and positive and not prev.get(key, 0)):
                event = c.execute("INSERT INTO events(at,instrument,side,upl,ccy) VALUES(?,?,?,?,?)",
                                  (now, inst, side, amount, ccy))
            if entered:
                c.execute("INSERT INTO email_thresholds VALUES(?,?,?,1)", (key, event.lastrowid, str(usdt)))
            elif episode is not None and above is not False:
                c.execute("INSERT INTO email_thresholds VALUES(?,?,?,?)",
                          (key, episode["event_id"], episode["trigger_usdt"], int(above is True)))
            c.execute("INSERT INTO positions VALUES(?,?,?,?,?,?,?)",
                      (key, inst, side, qty, amount, ccy, positive))
        day = datetime.fromtimestamp(now, TZ).date().isoformat()
        c.execute("""INSERT INTO daily_snapshots VALUES(?,?,?) ON CONFLICT(day) DO UPDATE SET
            sampled_at=excluded.sampled_at, positions=excluded.positions
            WHERE excluded.sampled_at >= daily_snapshots.sampled_at""",
            (day, now, json.dumps(daily_positions)))
        meta(c, "last_ok", now)
        meta(c, "read_error", "")

def rows(payload):
    data = payload.get("data")
    if not isinstance(data,list) or not all(isinstance(r,dict) for r in data):
        raise ValueError("invalid data")
    return data

async def verify_key(client):
    cfg = rows(await client.get("/api/v5/account/config", authenticated=True))
    if len(cfg)!=1 or cfg[0].get("perm") != "read_only" or not cfg[0].get("uid"):
        raise PermissionError("OKX key must be read_only")
    return hashlib.sha256(str(cfg[0]["uid"]).encode()).hexdigest()

def client_for(conf, http):
    return ReadOnlyOKXClient(BASE, OKXCredentials(conf["key"],conf["secret"],conf["passphrase"]),client=http)

def enforce_owner(owner, save=False):
    with db() as c:
        old = c.execute("SELECT v FROM meta WHERE k='owner'").fetchone()
        if old and old[0]!=owner:
            raise PermissionError("different account")
        if save:
            meta(c,"owner",owner)

@contextmanager
def smtp_connection(conf, dest):
    ctx = ssl.create_default_context()
    port = conf["smtp_port"]
    if port==465:
        smtp=smtplib.SMTP_SSL("smtp.qq.com",port,timeout=10,context=ctx)
    elif port==587:
        smtp=smtplib.SMTP("smtp.qq.com",port,timeout=10)
    else:
        raise ValueError("unexpected SMTP port")
    try:
        if port==587:
            smtp.starttls(context=ctx)
        smtp.login(dest,conf["smtp_code"])
        yield smtp
    finally:
        smtp.close()

def mail(conf, subject, body, msgid=None):
    dest = RECIPIENT_FILE.read_text().strip()
    msg = EmailMessage()
    msg["From"] = msg["To"] = dest
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=False)
    if msgid:
        msg["Message-ID"] = msgid
    msg.set_content(body)
    with smtp_connection(conf, dest) as smtp:
        if smtp.send_message(msg):
            raise RuntimeError("recipient refused")

SIDE = {"long":"多仓","short":"空仓","net":"净持仓"}
def send_pending(conf):
    with db() as c:
        batch = c.execute("""SELECT e.*, t.trigger_usdt FROM email_thresholds t
            JOIN events e ON e.id=t.event_id
            WHERE t.confirmed=1 AND e.sent IS NULL
            AND (SELECT v FROM meta WHERE k='read_error')=''
            AND ? - CAST((SELECT v FROM meta WHERE k='last_ok') AS INTEGER) BETWEEN 0 AND ?
            ORDER BY e.id LIMIT 30""", (int(time.time()), MAX_SAMPLE_AGE)).fetchall()
    if not batch:
        return False
    lines=[f"{stamp(r['at'])}  {r['instrument']}  {SIDE[r['side']]}  浮盈 {r['trigger_usdt']} USDT" for r in batch]
    body = f"以下仓位的未实现浮盈在采样时达到 {EMAIL_THRESHOLD} USDT（北京时间）：\n\n" + "\n".join(lines)
    body += "\n\n金额为 OKX 返回的未实现盈亏，按采样时的标记价格计算。"
    mail(conf, f"OKX 仓位浮盈提醒 · {len(batch)} 条", body, f"<okx-watch-{batch[0]['at']}-{batch[0]['id']}-{batch[-1]['id']}@qq.com>")
    with db() as c:
        c.executemany("UPDATE events SET sent=? WHERE id=?", [(int(time.time()),r["id"]) for r in batch])
        meta(c,"mail_error","")
    return True

def safe_error(e):
    if isinstance(e,httpx.HTTPStatusError):
        return "HTTP " + str(e.response.status_code)
    if isinstance(e,smtplib.SMTPResponseException):
        return "SMTP " + str(e.smtp_code)
    return type(e).__name__

async def notify_loop(conf, sampled):
    await sampled.wait()
    delay = 10
    while True:
        try:
            await asyncio.to_thread(send_pending,conf)
            delay = 10
        except Exception as exc:
            with db() as c:
                meta(c,"mail_error",safe_error(exc))
            delay=min(900,max(30,delay*2))
        await asyncio.sleep(delay)

async def recent_fills(client):
    # Keep an unfinished backfill cursor across polls and restarts. The first
    # successful poll starts with the latest 100 executions.
    with db() as c:
        state=dict(c.execute("SELECT k,v FROM meta WHERE k IN ('watermark','after','target')"))
    mark, after, target = (state.get(k,"") for k in ("watermark","after","target"))
    for _ in range(3):
        params={"limit":100}
        if after:
            params["after"]=after
        page=rows(await client.get("/api/v5/trade/fills",params,authenticated=True))
        if any(not all(r.get(k) for k in ("billId","tradeId","instId","ts")) for r in page):
            raise ValueError("incomplete execution")
        if page and not target:
            target=str(page[0]["billId"])
        done=not mark or len(page)<100 or any(str(r["billId"])==mark for r in page)
        tail=str(page[-1]["billId"]) if page else ""
        if not done and after==tail:
            raise ValueError("repeated execution cursor")
        with db() as c:
            c.executemany("INSERT OR IGNORE INTO fills VALUES(?,?,?,?,?,?)",[
                (r["instId"],str(r["tradeId"]),int(r["ts"]),r.get("side"),r.get("fillPx"),r.get("fillSz")) for r in page])
            if done:
                if target: meta(c,"watermark",target)
                meta(c,"target","")
                meta(c,"after","")
                return
            meta(c,"target",target)
            meta(c,"after",tail)
        after=tail

async def history_loop(client, verified):
    while True:
        await verified.wait()
        try:
            balances=rows(await client.account_balance())
            summary=[(d.get("ccy"),d.get("eq")) for a in balances for d in a.get("details",[]) if d.get("ccy")]
            with db() as c:
                meta(c,"balances",json.dumps(summary))
                meta(c,"balance_at",int(time.time()))
            await recent_fills(client)
            with db() as c:
                meta(c,"history_error","")
        except Exception as exc:
            with db() as c:
                meta(c,"history_error",safe_error(exc))
        await asyncio.sleep(60)

async def collect_loop(client, verified, sampled):
    verified_until = 0
    while True:
        start=time.monotonic()
        try:
            if start>=verified_until:
                verified.clear()
                enforce_owner(await verify_key(client),save=True)
                verified_until=start+900
                verified.set()
            snapshot=rows(await client.positions())
            sampled_at = int(time.time())
            rates = await conversion_rates(client, snapshot)
            record(snapshot, sampled_at, rates)
            sampled.set()
        except Exception as exc:
            with db() as c:
                meta(c,"read_error",safe_error(exc))
        await asyncio.sleep(max(1, INTERVAL-(time.monotonic()-start)))

async def main():
    conf=json.loads((Path(os.environ["CREDENTIALS_DIRECTORY"])/"account").read_bytes())
    init()
    async with httpx.AsyncClient(base_url=BASE, trust_env=False, timeout=10) as http:
        client=client_for(conf,http)
        verified=asyncio.Event()
        sampled = asyncio.Event()
        await asyncio.gather(collect_loop(client,verified,sampled),notify_loop(conf,sampled),history_loop(client,verified))

if __name__=="__main__":
    asyncio.run(main())
