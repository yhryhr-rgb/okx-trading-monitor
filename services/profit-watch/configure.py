"""Local root-only interactive activation. No credentials in argv, history or output."""
import asyncio
import fcntl
import getpass
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
import warnings
from contextlib import closing
from pathlib import Path
import httpx
import watch

CONF=Path("/etc/okx-profit-watch")
SECRETS=CONF/"private"
UNIT="okx-profit-watch.service"

def secret(label):
    value=getpass.getpass(label+"：")
    if not value:
        raise ValueError("empty")
    return value

async def check(conf):
    async with httpx.AsyncClient(base_url=watch.BASE, trust_env=False, timeout=10) as h:
        return await watch.verify_key(watch.client_for(conf,h))

def command(*cmd,**kwargs):
    return subprocess.run(cmd,check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,**kwargs)

def activate():
    if os.geteuid()!=0 or not sys.stdin.isatty() or not sys.stderr.isatty():
        sys.exit("请在 VPS 的 root 私有终端中运行。")
    warnings.simplefilter("error",getpass.GetPassWarning)
    print("OKX Global 全天候监控 · 每30秒采集 · 浮盈提醒发往 "+watch.RECIPIENT_FILE.read_text().strip())
    print("以下输入均隐藏。验证后，凭据将在本机加密保存供后台服务使用。")
    conf={k:secret(label) for k,label in (("key","OKX API Key"),("secret","OKX Secret"),("passphrase","OKX Passphrase"))}
    print("正在验证 OKX 只读权限…",flush=True)
    owner=asyncio.run(check(conf))
    if watch.DB.is_file():
        with closing(sqlite3.connect(watch.DB.as_uri()+"?mode=ro",uri=True)) as c:
            previous=c.execute("SELECT v FROM meta WHERE k='owner'").fetchone()
        if previous and previous[0]!=owner:
            sys.exit("已有监控数据属于其他账户，配置未替换。")
    conf["smtp_code"]=secret("QQ 邮箱 SMTP 客户端授权码").replace(" ","")
    conf["smtp_port"]=int((CONF/"port").read_text().strip())
    print("正在验证 QQ 邮箱 SMTP 登录…",flush=True)
    with watch.smtp_connection(conf, watch.RECIPIENT_FILE.read_text().strip()):
        pass
    with tempfile.TemporaryDirectory(dir=SECRETS) as d:
        encrypted=Path(d)/"account"
        command("systemd-creds","encrypt","--with-key=host","--name=account","-",str(encrypted),input=json.dumps(conf).encode())
        os.chmod(encrypted,0o600)
        os.replace(encrypted,SECRETS/"account")
    (CONF/"activated").touch(mode=0o644)
    os.chmod(CONF/"activated",0o644)
    since=int(time.time())
    command("systemctl","restart",UNIT)
    for _ in range(35):
        time.sleep(1)
        with closing(sqlite3.connect(watch.DB.as_uri()+"?mode=ro",uri=True)) as c:
            last=c.execute("SELECT v FROM meta WHERE k='last_ok'").fetchone()
        if last and int(last[0])>=since:
            print(f"WATCH_ACTIVE：首次仓位采集已成功。单仓浮盈达到 {watch.EMAIL_THRESHOLD} USDT 时发送邮件。")
            return
    sys.exit("凭据已配置。首次采集尚未确认，请查看面板。")

if __name__=="__main__":
    try:
        if os.geteuid()!=0 or not sys.stdin.isatty():
            sys.exit("请在 VPS 的 root 私有终端中运行。")
        with open("/run/lock/okx-profit-setup.lock","a") as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            activate()
    except (KeyboardInterrupt,EOFError):
        sys.exit("已取消。")
    except Exception as exc:
        sys.exit("配置未完成："+watch.safe_error(exc))
