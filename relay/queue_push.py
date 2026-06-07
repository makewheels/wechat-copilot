#!/usr/bin/env python3
"""Mac 端：把一条消息通过 WinRM 写进服务器的 C:\\relay\\queue\\。

走的是反向隧道：Mac -> 本地 SSH 转发 127.0.0.1:15985 -> 跳板机
49.233.60.29 -> 101.42.94.17:5985 (WinRM)。隧道得先起着（见 README）。

直接用：  uv run --with pypsrp python queue_push.py "要发的内容"
"""
import base64
import os
import sys
import time
from pathlib import Path

from pypsrp.client import Client


def _load_env():
    """读 relay/.env（KEY=VALUE）补到环境变量，不覆盖已有的。"""
    f = Path(__file__).resolve().parent / ".env"
    if not f.exists():
        return
    for line in f.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


_load_env()

WINHOST = os.environ.get("WINHOST", "127.0.0.1")
WINPORT = int(os.environ.get("WINPORT", "15985"))
WINUSER = os.environ.get("WINUSER", "administrator")
WINPASS = os.environ.get("WINPASS", "")


def make_client():
    return Client(WINHOST, port=WINPORT, username=WINUSER, password=WINPASS,
                  ssl=False, auth="ntlm", cert_validation=False, connection_timeout=40)


def push(text, client=None):
    """把 text 作为一条消息写进服务器队列。返回队列文件名。"""
    c = client or make_client()
    b64 = base64.b64encode(text.encode("utf-8")).decode()
    name = f"{time.time_ns()}.txt"
    ps = (
        f"$b=[Convert]::FromBase64String('{b64}');"
        f"[IO.File]::WriteAllBytes('C:\\relay\\queue\\{name}',$b);'{name}'"
    )
    out, streams, had_err = c.execute_ps(ps)
    if had_err:
        raise RuntimeError("WinRM push 失败: " + "; ".join(str(e) for e in streams.error))
    return name


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: queue_push.py '消息内容'"); sys.exit(1)
    print("已入队:", push(sys.argv[1]))
