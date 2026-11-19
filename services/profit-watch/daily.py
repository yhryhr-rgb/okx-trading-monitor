"""Daily OKX position P&L email. Runs under the VPS systemd timer."""
import asyncio
import json
import os
import sys
import time
from collections import defaultdict
from datetime import date, datetime, time as clock_time, timedelta
from decimal import Decimal
from pathlib import Path

import httpx
import watch

DAY = timedelta(days=1)
ZERO = Decimal("0")
POSITION_TYPES = {"SWAP", "FUTURES", "OPTION", "MARGIN", "EVENTS"}
TRADE_TYPES = {"2", "3", "5", "9", "10", "14", "34"}


def last_due(now):
    return (datetime.fromtimestamp(now, watch.TZ) - timedelta(hours=8)).date() - DAY


def midnight(day):
    return int(datetime.combine(day, clock_time(), watch.TZ).timestamp())


async def bills_for_day(client, day, now):
    start, end = midnight(day)*1000, midnight(day+DAY)*1000
    endpoint = "/api/v5/account/bills"
    if now - start/1000 > 6*86400:
        endpoint += "-archive"
    params = {"begin":start, "end":end-1, "limit":100}
    seen = set()
    while True:
        page = watch.rows(await client.get(endpoint, params, authenticated=True))
        for row in page:
            ident, ts = str(row["billId"]), int(row["ts"])
            if not ident.isascii() or not ident.isdigit():
                raise ValueError("invalid bill ID")
            if start <= ts < end and ident not in seen:
                seen.add(ident)
                yield row
        if len(page) < 100:
            return
        tail = str(page[-1]["billId"])
        if "after" in params and int(tail) >= int(params["after"]):
            raise ValueError("nonadvancing bill cursor")
        params["after"] = tail


def field(row, name, *, required=False):
    value = row.get(name)
    if value in (None,"") and not required:
        return ZERO
    amount = watch.number(value)
    if amount is None:
        raise ValueError("missing or invalid bill amount")
    return amount


async def realized(client, day, now):
    # Each entry contains booked P&L, signed fees, funding, and other position charges.
    totals = defaultdict(lambda: [ZERO, ZERO, ZERO, ZERO])
    async for r in bills_for_day(client, day, now):
        kind, subtype = r["type"], r["subType"]
        if r.get("instType") not in POSITION_TYPES:
            continue
        pnl, fee, funding, adjustment = ZERO, ZERO, ZERO, ZERO
        if kind == "8":
            funding = field(r, "pnl", required=True)
        elif kind == "7" and subtype == "9":
            adjustment = field(r, "balChg", required=True)
        elif kind in TRADE_TYPES:
            pnl, fee = field(r, "pnl"), field(r, "fee")
            if subtype in {"116","117","355"} and pnl == 0:
                # Dedicated liquidation deduction, overloss recovery or settlement bill.
                adjustment = field(r, "balChg", required=True) - fee
        else:
            continue
        values = (pnl, fee, funding, adjustment)
        if any(values):
            if not r.get("ccy"):
                raise ValueError("missing bill currency")
            bucket = totals[(r.get("instId") or "未分配交易品种", r["ccy"])]
            for i, amount in enumerate(values):
                bucket[i] += amount
    return totals


def report_snapshot(day, now):
    with watch.db() as c:
        row = c.execute("SELECT * FROM daily_snapshots WHERE day=?", (day.isoformat(),)).fetchone()
        if row:
            age = midnight(day+DAY) - row["sampled_at"]
            label = "日末快照" if 0 < age <= 90 else "当日最后可用快照，距日末超过 90 秒"
        else:
            row = c.execute("SELECT * FROM daily_snapshots ORDER BY sampled_at DESC LIMIT 1").fetchone()
            if row is None or not 0 <= now-row["sampled_at"] <= 90:
                raise ValueError("awaiting current position snapshot")
            label = "当日日末快照缺失，以下为最新仓位快照"
    return label, row["sampled_at"], json.loads(row["positions"])


def amount(value):
    if value == 0:
        return "0"
    return format(value.normalize(), "f")


def lines_by_currency(totals):
    return "，".join(f"{amount(value)} {ccy}" for ccy, value in sorted(totals.items())) or "0 USDT"


def total_usdt(values):
    if any(value is None for value in values):
        return "无法完整折算，见下方明细"
    return amount(sum(values, ZERO)) + " USDT"


def render(day, ledger, rates, snapshot, priced_at):
    label, sampled_at, positions = snapshot
    net = defaultdict(Decimal)
    components = [defaultdict(Decimal) for _ in range(4)]
    for (_, ccy), values in ledger.items():
        net[ccy] += sum(values, ZERO)
        for bucket, value in zip(components, values):
            bucket[ccy] += value
    realized_usdt = total_usdt([watch.email_valuation(v, ccy, rates) for ccy, v in net.items()])
    open_usdt = total_usdt([watch.number(row[4]) for row in positions])
    lines = [f"{day} 仓位日报（北京时间）", "",
             f"当日已实现盈亏合计　{realized_usdt}",
             f"未实现盈亏合计　　　{open_usdt}",
             f"{label}　{watch.stamp(sampled_at)}，共 {len(positions)} 个仓位", "",
             "当日入账明细（按结算币种）"]
    for name, bucket in zip(("交易及交割盈亏","手续费及返佣","资金费收支","仓位扣息及结算调整"), components):
        lines.append(f"{name}　{lines_by_currency(bucket)}")
    lines.append(f"已实现合计　{lines_by_currency(net)}")
    for (inst, ccy), values in sorted(ledger.items()):
        lines.append(f"  {inst}　{amount(sum(values,ZERO))} {ccy}")
    lines.extend(["", "未平仓明细（标记价格）"])
    for inst, side, upl, ccy, usdt in sorted(positions, key=lambda p: (p[0],p[1])):
        native = f"{upl} {ccy}" if upl is not None else "盈亏暂缺"
        converted = f"（{usdt} USDT）" if usdt is not None else "（缺少 USDT 估值）"
        lines.append(f"  {inst}　{watch.SIDE[side]}　{native}" + (converted if ccy != "USDT" else ""))
    if not positions:
        lines.append("采样时无未平仓仓位。")
    lines.extend(["", "已实现按该自然日入账的仓位账单统计，包含正负盈亏。现货买卖、充提划转及账户级费用不计入。"])
    if any(ccy != "USDT" for ccy in net):
        lines.append(f"已实现的 USDT 折算采用制表时（{watch.stamp(priced_at)}）的可用指数价，原币入账金额见上。")
    return "\n".join(lines)


async def run_due(client, conf, now):
    due = last_due(now)
    with watch.db() as c:
        c.execute("INSERT OR IGNORE INTO meta VALUES('next_report_day',?)", (due.isoformat(),))
        day = date.fromisoformat(c.execute("SELECT v FROM meta WHERE k='next_report_day'").fetchone()[0])
    while day <= due:
        with watch.db() as c:
            saved = c.execute("SELECT body,sent_at FROM daily_reports WHERE day=?", (day.isoformat(),)).fetchone()
        if not saved:
            snapshot = report_snapshot(day, int(time.time()))
            if now-midnight(day) >= 90*86400:
                # Beyond archive retention, an empty API response cannot establish zero.
                body = (f"{day} 仓位日报：账单已超出查询期限，当日已实现盈亏无法确认。\n"
                        f"{snapshot[0]} {watch.stamp(snapshot[1])}，未实现盈亏 "
                        + total_usdt([watch.number(p[4]) for p in snapshot[2]]))
            else:
                totals = await realized(client, day, now)
                rates = await watch.conversion_rates(client, [{"ccy":ccy} for _,ccy in totals])
                body = render(day, totals, rates, snapshot, int(time.time()))
            with watch.db() as c:
                c.execute("INSERT INTO daily_reports(day,body) VALUES(?,?)", (day.isoformat(),body))
        else:
            body = saved["body"]
        if not saved or saved["sent_at"] is None:
            await asyncio.to_thread(watch.mail, conf, f"OKX 仓位日报 · {day}", body, f"<okx-position-daily-{day}@qq.com>")
        with watch.db() as c:
            c.execute("UPDATE daily_reports SET sent_at=COALESCE(sent_at,?) WHERE day=?", (int(time.time()), day.isoformat()))
            watch.meta(c, "next_report_day", (day+DAY).isoformat())
        day += DAY


async def main():
    conf = json.loads((Path(os.environ["CREDENTIALS_DIRECTORY"])/"account").read_bytes())
    watch.init()
    async with httpx.AsyncClient(base_url=watch.BASE, trust_env=False, timeout=10) as http:
        client = watch.client_for(conf, http)
        watch.enforce_owner(await watch.verify_key(client))
        await run_due(client, conf, int(time.time()))


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as exc:
        sys.exit("Daily report failed: " + watch.safe_error(exc))
