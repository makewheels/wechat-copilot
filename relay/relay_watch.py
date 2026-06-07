#!/usr/bin/env python3
"""Mac 端镜像监听器：盯 contacts.json 里的人，把双方新消息原样转发到
服务器队列 → 服务器粘进那个微信群。

- 读：复用 copilot 的 weflow（WeFlow 本地只读 API，读本机微信库）
- 发：queue_push.push()（WinRM 写服务器队列）
- 双向：她发的(isSend=0) 和 我发的(isSend=1) 都转
- 首次启动不补历史，只记当前最新位置
- 文字消息转「名字: 内容」；非文字转占位符（[图片]/[语音]…）

用法：
  python relay_watch.py            前台持续镜像（Ctrl+C 停）
  python relay_watch.py --send X   往队列推一条测试消息 X
"""
import datetime
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))  # 复用 copilot 根目录的 core / weflow

import core
import weflow
import queue_push

STATE = HERE / "relay_state.json"
POLL = 8  # 秒


def load_contacts():
    f = ROOT / "data" / "contacts.json"
    return json.loads(f.read_text()) if f.exists() else {}


def load_state():
    return json.loads(STATE.read_text()) if STATE.exists() else {}


def save_state(s):
    STATE.write_text(json.dumps(s, ensure_ascii=False, indent=2))


def msg_key(m):
    return f"{m.get('createTime')}:{m.get('localId')}"


def fmt(name, m):
    who = "我" if m.get("isSend") else name
    lt = m.get("localType")
    if lt == 1:
        txt = (m.get("content") or "").strip()
    else:
        txt = weflow.TAG.get(lt) or "[其他]"
    if not txt:
        return None
    return f"{who}: {txt}"


def is_echo(m, names):
    """防回环：转发出去的消息形如「名字: 内容」，若它又被读回来（比如发错成了
    被监听的私聊），跳过，避免无限套娃。"""
    if m.get("localType") != 1:
        return False
    txt = (m.get("content") or "").strip()
    return any(txt.startswith(n + ": ") or txt.startswith(n + "：") for n in names)


def new_after(msgs, last_key):
    """返回 last_key 之后的新消息列表；首次(无 last_key)返回空（不补历史）。"""
    if not last_key:
        return []
    keys = [msg_key(m) for m in msgs]
    if last_key in keys:
        return msgs[keys.index(last_key) + 1:]
    # last_key 已滑出窗口：按时间兜底
    last_ct = int(last_key.split(":")[0])
    return [m for m in msgs if (m.get("createTime") or 0) > last_ct]


def main():
    env = core.load_env()
    contacts = load_contacts()

    if "--send" in sys.argv:
        text = sys.argv[sys.argv.index("--send") + 1]
        print("入队:", queue_push.push(text))
        return

    state = load_state()
    names = [v.get("name", k) for k, v in contacts.items()]
    echo_names = set(names) | {"我"}  # 防回环：这些前缀开头的消息是转发回声，不再转
    print(f"镜像监听启动，盯：{names}，每 {POLL}s 一次。Ctrl+C 停。")

    client = None
    while True:
        for wxid, info in contacts.items():
            name = info.get("name", wxid)
            try:
                msgs = weflow.messages(env, wxid, 40)
                if not msgs:
                    continue
                if wxid not in state:  # 首次：只记位置，不补历史
                    state[wxid] = msg_key(msgs[-1])
                    save_state(state)
                    continue
                fresh = new_after(msgs, state.get(wxid))
                if not fresh:
                    continue
                if client is None:
                    client = queue_push.make_client()
                for m in fresh:
                    line = fmt(name, m)
                    if not line:
                        continue
                    if is_echo(m, echo_names):  # 防回环
                        print(f"[{datetime.datetime.now():%H:%M:%S}] 跳过回声 {line[:40]}")
                        continue
                    queue_push.push(line, client)
                    print(f"[{datetime.datetime.now():%H:%M:%S}] -> {line[:60]}")
                state[wxid] = msg_key(msgs[-1])
                save_state(state)
            except Exception as e:
                print(f"[err] {name}: {e}")
                client = None  # 下轮重连
        time.sleep(POLL)


if __name__ == "__main__":
    main()
