#!/usr/bin/python3 -I
"""Configure this monitor's SMTP credential from a private terminal on the VPS."""
import fcntl
import getpass
import json
import os
import re
from pathlib import Path
import smtplib
import ssl
import subprocess
import sys
import tempfile
import warnings


CONFIG = Path("/etc/dmit-stock")
DROPIN = Path("/etc/systemd/system/dmit-stock.service.d")
UNIT = "dmit-stock.service"


def verify_login(recipient, code):
    tls = ssl.create_default_context()
    for port in (465,587):
        try:
            if port == 465:
                smtp = smtplib.SMTP_SSL("smtp.qq.com",port,timeout=12,context=tls)
            else:
                smtp = smtplib.SMTP("smtp.qq.com",port,timeout=12)
            try:
                if port == 587:
                    smtp.starttls(context=tls)
                smtp.login(recipient,code)
                return port
            finally:
                smtp.close()
        except smtplib.SMTPAuthenticationError:
            raise
        except (OSError,smtplib.SMTPException):
            if port == 587:
                raise


def configure():
    if os.geteuid() != 0 or not sys.stdin.isatty() or not sys.stderr.isatty():
        sys.exit("请在 VPS 的 root 私有终端里执行。")
    warnings.simplefilter("error",getpass.GetPassWarning)
    os.umask(0o077)
    recipient = input("QQ SMTP sender and recipient address: ").strip()
    if not recipient.isascii() or not re.fullmatch(r"[A-Za-z0-9._%+-]+@qq\.com", recipient, re.IGNORECASE):
        sys.exit("Enter a valid QQ Mail address.")
    code = getpass.getpass("请输入此监控专用的 QQ 邮箱 SMTP 授权码（输入隐藏）：").replace(" ","")
    if not code:
        sys.exit("已取消。")
    port = verify_login(recipient, code)
    with tempfile.TemporaryDirectory(dir=CONFIG) as temp:
        target = Path(temp)/"smtp.cred"
        secret = json.dumps({"recipient":recipient,"smtp_code":code,"smtp_port":port}).encode()
        subprocess.run(["systemd-creds","encrypt","--with-key=host","--name=smtp","-",str(target)],
                       input=secret,check=True,stdout=subprocess.DEVNULL)
        os.chmod(target,0o600)
        os.replace(target,CONFIG/"smtp.cred")
    DROPIN.mkdir(mode=0o755,exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w",dir=DROPIN,delete=False) as tmp:
        tmp.write("[Service]\nLoadCredentialEncrypted=smtp:/etc/dmit-stock/smtp.cred\n")
    os.replace(tmp.name,DROPIN/"smtp.conf")
    subprocess.run(["systemctl","daemon-reload"],check=True)
    subprocess.run(["systemctl","restart",UNIT],check=True)
    print("邮箱登录验证成功。监控将在下一次检查时发送仍然有货的套餐。")


if __name__ == "__main__":
    try:
        if os.geteuid() != 0:
            sys.exit("请在 VPS 的 root 私有终端里执行。")
        with open("/run/lock/dmit-stock-mail.lock","a") as lock:
            fcntl.flock(lock,fcntl.LOCK_EX | fcntl.LOCK_NB)
            configure()
    except (EOFError,KeyboardInterrupt):
        sys.exit("已取消。")
    except Exception as exc:
        sys.exit("配置未完成：" + type(exc).__name__)
