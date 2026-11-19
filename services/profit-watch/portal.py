"""Local dashboard for OKX position alerts. Credential files belong to the worker."""
import time
import json
from contextlib import asynccontextmanager
from html import escape
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware
from app import main as previous
import watch

@asynccontextmanager
async def lifespan(app):
    async with previous.lifespan(app):
        watch.init()
        yield

app=FastAPI(title="OKX 仓位监控",lifespan=lifespan)
app.add_middleware(TrustedHostMiddleware,allowed_hosts=["localhost","127.0.0.1","[::1]"])
app.add_api_route("/api/fills",previous.fills,methods=["GET"])

def current():
    with watch.db() as c:
        c.execute("BEGIN")
        meta=dict(c.execute("SELECT k,v FROM meta WHERE k!='owner'"))
        positions=[dict(r) for r in c.execute("SELECT instrument,side,qty,upl,ccy FROM positions ORDER BY instrument,side")]
        alerts=[dict(r) for r in c.execute("""SELECT e.*, t.confirmed FROM events e
            LEFT JOIN email_thresholds t ON t.event_id=e.id ORDER BY e.id DESC LIMIT 100""")]
        pending=c.execute("""SELECT COUNT(*) FROM email_thresholds t JOIN events e ON e.id=t.event_id
            WHERE e.sent IS NULL AND t.confirmed=1""").fetchone()[0]
        fills=[dict(r) for r in c.execute("SELECT instrument,at,side,price,qty FROM fills ORDER BY at DESC LIMIT 100")]
    last=int(meta.get("last_ok") or 0)
    configured=Path("/etc/okx-profit-watch/activated").is_file()
    live=configured and bool(last) and 0<=time.time()-last<=90 and not meta.get("read_error")
    return dict(configured=configured,live=live,last=last,read_error=meta.get("read_error"),mail_error=meta.get("mail_error"),positions=positions,events=alerts,pending=pending,
                fills=fills, balances=json.loads(meta.get("balances","[]")), balance_at=int(meta.get("balance_at") or 0),history_error=meta.get("history_error"))

@app.get("/api/status")
def status():
    s=current()
    return dict(service="OKX Monitor",version=previous.app.version,mode="configured" if s["configured"] else "offline",
                api_configured=s["configured"],account_connected=s["live"],trading_enabled=False,
                last_positions_sync=s["last"] or None,pending_emails=s["pending"],
                email_threshold_usdt=str(watch.EMAIL_THRESHOLD))

@app.get("/health")
def health():
    s=status()
    return {key:s[key] for key in ("mode","api_configured","account_connected")} | {"status":"ok"}

def table(headers,rows,empty):
    th="".join("<th>"+escape(cell)+"</th>" for cell in headers)
    tr="".join("<tr>"+"".join("<td>"+escape(str(cell))+"</td>" for cell in row)+"</tr>" for row in rows)
    return "<div class='scroll'><table><thead><tr>"+th+"</tr></thead><tbody>"+tr+"</tbody></table></div>" if tr else "<p>"+escape(empty)+"</p>"

@app.get("/",response_class=HTMLResponse)
def dashboard():
    s=current()
    label="正在监控" if s["live"] else ("采集待恢复" if s["configured"] and s["read_error"] else "等待首次采集" if s["configured"] and not s["last"] else "数据已过期" if s["configured"] else "等待配置只读密钥和邮箱")
    last=watch.stamp(s["last"]) if s["last"] else "尚无成功记录"
    positions=table(["交易品种","方向","持仓量","未实现盈亏","币种"],[
        [r["instrument"],watch.SIDE[r["side"]],r["qty"],r["upl"] if r["upl"] is not None else "暂缺",r["ccy"] or "—"] for r in s["positions"]],
        "最近一次采集时没有未平仓仓位。" if s["last"] else "首次采集完成后显示仓位。")
    events=table(["北京时间","交易品种","方向","触发时浮盈","邮件"],[
        [watch.stamp(r["at"]),r["instrument"],watch.SIDE[r["side"]],r["upl"]+" "+r["ccy"],
         "邮件服务器已接收" if r["sent"] else {1:"待发送",0:"等待确认浮盈"}.get(r["confirmed"],"仅面板记录")]
        for r in s["events"]],"尚无浮盈提醒。")
    info=""
    for k,prefix in (("read_error","读取失败"),("mail_error","邮件暂未发出，将自动重试"),("history_error","资产或成交更新失败")):
        if s[k]: info+="<p>"+prefix+" · "+escape(s[k])+"</p>"
    page="""<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="refresh" content="15"><title>OKX 仓位监控</title>
<style>*{box-sizing:border-box}body{margin:0;background:#f3f5f3;color:#162721;font:16px/1.6 system-ui,sans-serif}main{max-width:1100px;margin:48px auto;padding:0 24px}h1{font-size:2rem;letter-spacing:-.04em;margin:0}header,section{padding:20px 0;border-bottom:1px solid #b8c6c1}h2{font-size:1.2rem}b{color:#006049}p{color:#43594f}.scroll{overflow:auto}table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums;white-space:nowrap}th,td{padding:10px 16px 10px 0;text-align:left;border-bottom:1px solid #dce2de}th{color:#50665a}small{color:#4c6255}</style><main><header><h1>OKX 仓位监控</h1>"""
    page+=f"<p><b>{label}</b> · 最近成功采集（北京时间） {last}</p>{info}</header><section><h2>当前仓位</h2>{positions}</section><section><h2>浮盈提醒</h2><p>单个仓位浮盈达到 {watch.EMAIL_THRESHOLD} USDT 时发送邮件。持续达标只提醒一次，回落后再次达标会重新提醒。</p>{events}</section><small>每 30 秒采集一次，页面每 15 秒刷新。显示最近 100 条提醒。</small></main></html>"
    assets=table(["币种","权益"],s["balances"],"等待资产数据。")
    trades=table(["北京时间","交易品种","方向","成交价","数量"],[
        [watch.stamp(r["at"]//1000),r["instrument"],{"buy":"买","sell":"卖"}.get(r["side"],"—"),r["price"],r["qty"]] for r in s["fills"]],"暂无成交记录。")
    assets_at=watch.stamp(s["balance_at"]) if s["balance_at"] else "尚无成功记录"
    page=page.replace("</main>",f"<section><h2>交易账户权益</h2><small>最近更新 {assets_at}</small>{assets}</section><section><h2>最近成交</h2>{trades}</section></main>")
    return HTMLResponse(page,headers={"Cache-Control":"no-store","Referrer-Policy":"no-referrer","X-Content-Type-Options":"nosniff","Content-Security-Policy":"default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'; form-action 'none'"})
