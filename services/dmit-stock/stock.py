"""Notify one mailbox when a DMIT Los Angeles VPS becomes available."""
import hashlib
import json
import logging
import os
import re
import sqlite3
import ssl
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import formatdate, parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path
from smtplib import SMTP, SMTP_SSL
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request, urlopen


SOURCE = "https://vpsdeer.com/merchants/dmit"
INTERVAL = 30
MAX_AGE = 180
BEIJING = timezone(timedelta(hours=8))


class CatalogParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.inside = False
        self.parts = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "script":
            self.inside = attrs.get("id") == "vpsdeer-seed" and attrs.get("type") == "application/json"

    def handle_endtag(self, tag):
        if tag == "script":
            self.inside = False

    def handle_data(self, data):
        if self.inside:
            self.parts.append(data)


@dataclass(frozen=True)
class Plan:
    pid: int
    name: str
    price: str
    available: bool
    checked_ms: int


def official_pid(url):
    link = urlsplit(url)
    if link.scheme != "https" or link.netloc not in {"www.dmit.io", "dmit.io"}:
        raise ValueError("unexpected seller URL")
    if link.path not in {"/cart.php", "/aff.php"}:
        raise ValueError("unexpected seller path")
    pid = parse_qs(link.query).get("pid", [])
    if len(pid) != 1 or not re.fullmatch(r"[1-9][0-9]{0,9}", pid[0]):
        raise ValueError("invalid product ID")
    return int(pid[0])


def catalog(html, now):
    parser = CatalogParser()
    parser.feed(html)
    parser.close()
    seed = json.loads("".join(parser.parts))
    if seed["page"] != "merchant" or seed["key"] != "dmit":
        raise ValueError("unexpected merchant")
    plans = seed["plans"]["data"]
    meta = seed["plans"]["meta"]
    complete = not meta.get("nextCursor") and len(plans) == meta["total"]
    observations, count = {}, 0
    for item in plans:
        name = item["name"]
        if (item["merchant"]["key"] != "dmit" or item["region"]["code"] != "US"
                or not name.upper().startswith("LAX.") or item.get("catalogStatus") != "active"):
            continue
        count += 1
        stock = item["availability"]
        if stock.get("freshness") != "fresh" or stock.get("status") not in ("in_stock","out_of_stock"):
            continue
        at = datetime.fromisoformat(stock["checkedAt"].replace("Z","+00:00"))
        if at.tzinfo is None or not -10 <= now-at.timestamp() <= MAX_AGE:
            continue
        pid = official_pid(item["purchaseUrl"])
        plan = Plan(pid, name, (item.get("price") or {}).get("display") or "以官网为准",
                    stock["status"] == "in_stock", int(at.timestamp()*1000))
        if pid in observations and observations[pid] != plan:
            raise ValueError("conflicting product records")
        observations[pid] = plan
    if not count or not observations:
        raise ValueError("no fresh Los Angeles observations")
    return list(observations.values()), count, complete


def fetch():
    req = Request(SOURCE, headers={"User-Agent":"DMIT-Stock-Notifier/1.0", "Accept":"text/html"})
    with urlopen(req, timeout=12) as response:
        if response.url != SOURCE or response.headers.get_content_type() != "text/html":
            raise ValueError("unexpected catalog response")
        html = response.read(2_000_001)
    if len(html) > 2_000_000:
        raise ValueError("oversized catalog")
    return catalog(html.decode("utf-8"), time.time())


def initialize(db):
    db.row_factory = sqlite3.Row
    with db:
        db.execute("""CREATE TABLE IF NOT EXISTS products(
            pid INTEGER PRIMARY KEY, checked_ms INTEGER NOT NULL, in_stock INTEGER NOT NULL,
            since_ms INTEGER NOT NULL, notified_at INTEGER)""")
        db.execute("""CREATE TABLE IF NOT EXISTS health(
            id INTEGER PRIMARY KEY CHECK(id=1), polled_at INTEGER, covered INTEGER, fresh INTEGER,
            in_stock INTEGER, complete INTEGER)""")


def pending(db, plans, count, complete, now):
    ready = []
    with db:
        for plan in plans:
            last = db.execute("SELECT * FROM products WHERE pid=?", (plan.pid,)).fetchone()
            if last and plan.checked_ms < last["checked_ms"]:
                continue
            if last and plan.checked_ms == last["checked_ms"] and plan.available != last["in_stock"]:
                continue
            changed = last is None or plan.available != last["in_stock"]
            since = plan.checked_ms if changed else last["since_ms"]
            sent = None if changed else last["notified_at"]
            db.execute("INSERT OR REPLACE INTO products VALUES(?,?,?,?,?)",
                       (plan.pid, plan.checked_ms, plan.available, since, sent))
            if plan.available and sent is None:
                ready.append((plan, since))
        db.execute("INSERT OR REPLACE INTO health VALUES(1,?,?,?,?,?)",
                   (int(now), count, len(plans), sum(p.available for p in plans), complete))
    return ready


def send(conf, batch):
    dest = conf["recipient"]
    msg = EmailMessage()
    msg["From"] = msg["To"] = dest
    msg["Date"] = formatdate(localtime=False)
    msg["Subject"] = f"DMIT 洛杉矶有货 · {batch[0][0].name}" if len(batch) == 1 else f"DMIT 洛杉矶有货 · {len(batch)} 款套餐"
    events = sorted((p.pid, since) for p, since in batch)
    digest = hashlib.sha256(str(events).encode()).hexdigest()[:32]
    msg["Message-ID"] = f"<dmit-lax-{digest}@qq.com>"
    lines = ["VPSDeer 最新检测到以下 DMIT 洛杉矶套餐可购买。库存可能随时变动，请点开官方商品页确认。"]
    for p, _ in batch:
        stamp = datetime.fromtimestamp(p.checked_ms/1000, BEIJING).strftime("%Y-%m-%d %H:%M:%S")
        lines += ["",p.name, f"标价　{p.price}", f"检测时间　{stamp}（北京时间）",
                  f"https://www.dmit.io/cart.php?a=add&pid={p.pid}"]
    msg.set_content("\n".join(lines))
    tls = ssl.create_default_context()
    port = conf["smtp_port"]
    if port == 465:
        smtp = SMTP_SSL("smtp.qq.com", port, timeout=12, context=tls)
    elif port == 587:
        smtp = SMTP("smtp.qq.com", port, timeout=12)
    else:
        raise ValueError("unexpected mail port")
    try:
        if port == 587:
            smtp.starttls(context=tls)
        smtp.login(dest, conf["smtp_code"])
        if smtp.send_message(msg):
            raise RuntimeError("recipient refused")
    finally:
        smtp.close()


def poll(db, conf):
    plans, count, complete = fetch()
    batch = pending(db, plans, count, complete, time.time())
    if not complete:
        logging.warning("Source returned a partial catalog")
    if batch and conf is not None:
        send(conf, batch)
        with db:
            db.executemany("UPDATE products SET notified_at=? WHERE pid=? AND since_ms=?",
                           [(int(time.time()),p.pid,since) for p,since in batch])
        logging.info("Mail accepted for %d available plans", len(batch))


def retry_after(value):
    if not value:
        return 0
    if value.isascii() and value.isdigit():
        return int(value)
    try:
        return max(0, parsedate_to_datetime(value).timestamp()-time.time())
    except (TypeError, ValueError, OverflowError):
        return 0


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    directory = os.environ.get("CREDENTIALS_DIRECTORY")
    conf = json.loads((Path(directory)/"smtp").read_bytes()) if directory else None
    if conf is None:
        logging.info("Monitoring inventory; waiting for separate SMTP setup")
    db = sqlite3.connect(Path(os.environ["STATE_DIRECTORY"])/"stock.sqlite3")
    initialize(db)
    delay = INTERVAL
    while True:
        start = time.monotonic()
        try:
            poll(db, conf)
            delay = INTERVAL
        except HTTPError as exc:
            delay = max(min(900, delay*2), retry_after(exc.headers.get("Retry-After")))
            logging.warning("Catalog returned HTTP %d", exc.code)
        except Exception as exc:
            logging.warning("Check failed (%s)", type(exc).__name__)
            delay = min(300, max(INTERVAL, delay*2))
        time.sleep(max(1, delay - (time.monotonic()-start)))


if __name__ == "__main__":
    main()
