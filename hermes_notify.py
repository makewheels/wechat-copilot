#!/usr/bin/env python3
"""把一条消息通过 Hermes 推到用户微信(home channel)。

必须用 Hermes 自己的 venv 跑：
  ~/.hermes/hermes-agent/venv/bin/python hermes_notify.py "消息"
  或   ... hermes_notify.py   (消息从 stdin 读)
"""
import os
import sys
from pathlib import Path

H = Path.home() / ".hermes" / "hermes-agent"
ENV = Path.home() / ".hermes" / ".env"


def main():
    os.chdir(str(H))
    sys.path.insert(0, str(H))
    if ENV.exists():
        for line in ENV.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    text = sys.argv[1] if len(sys.argv) > 1 else sys.stdin.read()
    if not text.strip():
        print("空消息，不发"); return
    from tools.send_message_tool import send_message_tool
    print(send_message_tool({"action": "send", "target": "weixin", "message": text}))


if __name__ == "__main__":
    main()
