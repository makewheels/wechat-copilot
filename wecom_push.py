#!/usr/bin/env python3
"""企业微信群机器人 webhook 推送。无 token / 无 IP 白名单 / 无域名，随时能发。

读 .env 的 WECOM_WEBHOOK。
"""
import json
import sys
import urllib.request
from pathlib import Path

ENV = Path.home() / "workspace" / "tools" / "wechat-copilot" / ".env"


def _webhook():
    for line in ENV.read_text().splitlines():
        if line.startswith("WECOM_WEBHOOK="):
            return line.split("=", 1)[1].strip()
    raise RuntimeError("WECOM_WEBHOOK 未配置")


def push(text: str) -> dict:
    body = json.dumps({"msgtype": "text", "text": {"content": text}}).encode("utf-8")
    req = urllib.request.Request(_webhook(), data=body,
                                 headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=15).read())


if __name__ == "__main__":
    text = sys.argv[1] if len(sys.argv) > 1 else sys.stdin.read()
    print(json.dumps(push(text.strip()), ensure_ascii=False))
