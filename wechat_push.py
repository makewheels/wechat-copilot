#!/usr/bin/env python3
"""直接调微信 iLink API 发消息。

不依赖 Hermes 的发送代码——只读它维护的 context_token 文件（iLink 要求每条外发
带上对方最新的 context_token，这个 token 由 getupdates 长轮询刷新，Hermes 网关在做）。
"""
import json
import random
import secrets
import sys
import urllib.request
from pathlib import Path

HERMES = Path.home() / ".hermes"
ILINK = "https://ilinkai.weixin.qq.com/ilink/bot/sendmessage"


def _env():
    env = {}
    f = HERMES / ".env"
    if f.exists():
        for line in f.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def _context_token(account_id, peer):
    f = HERMES / "weixin" / "accounts" / f"{account_id}.context-tokens.json"
    if f.exists():
        try:
            return json.loads(f.read_text()).get(peer, "")
        except Exception:
            return ""
    return ""


def push(text: str) -> dict:
    env = _env()
    token = env["WEIXIN_TOKEN"]
    account = env["WEIXIN_ACCOUNT_ID"]
    home = env["WEIXIN_HOME_CHANNEL"]
    ctx = _context_token(account, home)

    msg = {
        "from_user_id": "",
        "to_user_id": home,
        "client_id": str(secrets.randbits(32)),
        "message_type": 2,
        "message_state": 4,
        "item_list": [{"type": 1, "text_item": {"text": text}}],
    }
    if ctx:
        msg["context_token"] = ctx

    body = json.dumps({"base_info": {"channel_version": "2.2.0"}, "msg": msg},
                      ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    req = urllib.request.Request(ILINK, data=body, headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
        "AuthorizationType": "ilink_bot_token",
        "Content-Length": str(len(body)),
        "iLink-App-Id": "bot",
        "X-WECHAT-UIN": str(random.randint(1000000000, 9999999999)),
    })
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode("utf-8"))


if __name__ == "__main__":
    text = sys.argv[1] if len(sys.argv) > 1 else sys.stdin.read()
    print(json.dumps(push(text.strip()), ensure_ascii=False))
