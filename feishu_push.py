#!/usr/bin/env python3
"""飞书推送 —— 用 lark-cli 把消息发到指定群。

读 .env 的 FEISHU_CHAT_ID / LARK_CLI。
"""
import os
import subprocess
import sys
from pathlib import Path

ENV = Path.home() / "workspace" / "tools" / "wechat-copilot" / ".env"


def _env():
    e = {}
    for line in ENV.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            e[k.strip()] = v.strip()
    return e


def push(text: str, markdown: bool = False, chat_id: str = "") -> dict:
    e = _env()
    cli = e.get("LARK_CLI", "lark-cli")
    chat = chat_id or e["FEISHU_CHAT_ID"]
    env = dict(os.environ)  # 继承完整环境(HOME等), lark-cli 才找得到配置
    env["PATH"] = env.get("PATH", "") + ":" + str(Path(cli).parent)
    env.pop("HERMES_HOME", None)
    env.pop("OPENCLAW_HOME", None)
    flag = "--markdown" if markdown else "--text"
    r = subprocess.run(
        [cli, "im", "+messages-send", "--as", "bot", "--chat-id", chat, flag, text],
        capture_output=True, timeout=30, env=env,
    )
    out = r.stdout.decode("utf-8", "replace")
    if '"ok": true' in out or '"ok":true' in out:
        return {"ok": True}
    return {"ok": False, "err": (out or r.stderr.decode("utf-8", "replace"))[:200]}


if __name__ == "__main__":
    text = sys.argv[1] if len(sys.argv) > 1 else sys.stdin.read()
    print(push(text.strip()))
