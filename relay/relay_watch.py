#!/usr/bin/env python3
"""Mac 端镜像监听器：盯 contacts.json 里的人，把双方新消息原样转发到
服务器队列 → 服务器粘进那个微信群。

- 读：复用 copilot 的 weflow（WeFlow 本地只读 API，读本机微信库）
- 发：queue_push.push()（WinRM 写服务器队列）
- 双向：她发的(isSend=0) 和 我发的(isSend=1) 都转
- 首次启动不补历史，只记当前最新位置
- 文字消息转「名字: 内容」；非文字转占位符（[图片]/[语音]…）
- 默认走 WeFlow 的 SSE 推送（/api/v1/push/messages），来一条转一条、近实时；
  WeFlow 设置里需打开"消息推送"。失败/未开则可用 --poll 退回轮询。

用法：
  python relay_watch.py            SSE 订阅（默认，近实时）
  python relay_watch.py --poll     轮询模式（每 POLL 秒）
  python relay_watch.py --send X   往队列推一条测试消息 X
"""
import datetime
import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))  # 复用 copilot 根目录的 core / weflow

import core
import weflow
import queue_push

STATE = HERE / "relay_state.json"
POLL = 4  # 秒（--poll 模式的轮询间隔）


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
    content = (m.get("content") or "").strip()

    # 撤回检测：localType=10000 且含 revokemsg 的 XML
    if lt == 10000 and "<revokemsg>" in content:
        who_str = "你" if m.get("isSend") else name
        return f"[{who_str} 撤回了一条消息]"

    # 语音：WeFlow 开了自动转文字后 content 就是识别结果
    if lt == 34:
        if content:
            return f"{who}: [语音] {content}"
        return f"{who}: [语音]"

    txt = content if lt == 1 else (weflow.TAG.get(lt) or "[其他]")
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
    last_ct = int(last_key.split(":")[0])  # last_key 滑出窗口：按时间兜底
    return [m for m in msgs if (m.get("createTime") or 0) > last_ct]


def process_once(env, contacts, state, echo_names, client_holder):
    """扫一遍所有盯的人，把新消息转出去。被 SSE 事件和轮询共用。"""
    changed = False
    for wxid, info in contacts.items():
        name = info.get("name", wxid)
        try:
            msgs = weflow.messages(env, wxid, 40)
            if not msgs:
                continue
            if wxid not in state:  # 首次：只记位置，不补历史
                state[wxid] = msg_key(msgs[-1]); changed = True
                continue
            fresh = new_after(msgs, state.get(wxid))
            if not fresh:
                continue
            if client_holder[0] is None:
                client_holder[0] = queue_push.make_client()
            for m in fresh:
                line = fmt(name, m)
                if not line:
                    continue
                if is_echo(m, echo_names):  # 防回环
                    print(f"[{datetime.datetime.now():%H:%M:%S}] 跳过回声 {line[:40]}")
                    continue
                queue_push.push(line, client_holder[0])
                print(f"[{datetime.datetime.now():%H:%M:%S}] -> {line[:60]}")
            state[wxid] = msg_key(msgs[-1]); changed = True
        except Exception as e:
            print(f"[err] {name}: {e}")
            client_holder[0] = None  # 下次重连
    if changed:
        save_state(state)


def init_state(env, contacts, state):
    """首次启动：给没记录过的人记下当前最新位置（不补历史）。"""
    for wxid in contacts:
        if wxid not in state:
            ms = weflow.messages(env, wxid, 10)
            if ms:
                state[wxid] = msg_key(ms[-1])
    save_state(state)


def sse_url(env):
    base = (env.get("WEFLOW_API") or "http://127.0.0.1:5031").rstrip("/")
    tok = urllib.parse.quote(env.get("WEFLOW_ACCESS_TOKEN", ""))
    return f"{base}/api/v1/push/messages?access_token={tok}"


def run_sse(env, contacts):
    import threading

    state = load_state()
    echo_names = {v.get("name", k) for k, v in contacts.items()} | {"我"}
    client_holder = [None]
    lock = threading.Lock()
    init_state(env, contacts, state)
    names = [v.get("name", k) for k, v in contacts.items()]
    print(f"SSE 订阅启动，盯：{names}。SSE 收消息近实时 + {POLL}s 定时兜底发消息。Ctrl+C 停。")

    def periodic():
        """定时兜底：WeFlow SSE 只推 incoming (isSend=0)，不推自己发的 (isSend=1)。
        所以每 POLL 秒扫一遍，补上 outgoing 的。"""
        while True:
            time.sleep(POLL)
            with lock:
                process_once(env, contacts, state, echo_names, client_holder)

    threading.Thread(target=periodic, daemon=True).start()

    while True:
        try:
            with urllib.request.urlopen(urllib.request.Request(sse_url(env)), timeout=70) as r:
                with lock:
                    process_once(env, contacts, state, echo_names, client_holder)  # 连上先补一遍
                for raw in r:
                    line = raw.decode("utf-8", "replace").rstrip("\r\n")
                    if line.startswith("event:"):
                        ev = line[6:].strip()
                        if ev and ev != "ready":
                            with lock:
                                process_once(env, contacts, state, echo_names, client_holder)
        except KeyboardInterrupt:
            raise
        except Exception as e:
            print(f"[sse] 断开，2s 后重连：{e}")
            time.sleep(2)


def run_poll(env, contacts):
    state = load_state()
    echo_names = {v.get("name", k) for k, v in contacts.items()} | {"我"}
    client_holder = [None]
    init_state(env, contacts, state)
    names = [v.get("name", k) for k, v in contacts.items()]
    print(f"轮询监听启动，盯：{names}，每 {POLL}s 一次。Ctrl+C 停。")
    while True:
        process_once(env, contacts, state, echo_names, client_holder)
        time.sleep(POLL)


def main():
    env = core.load_env()
    contacts = load_contacts()

    if "--send" in sys.argv:
        text = sys.argv[sys.argv.index("--send") + 1]
        print("入队:", queue_push.push(text))
        return

    if "--poll" in sys.argv:
        run_poll(env, contacts)
    else:
        run_sse(env, contacts)


if __name__ == "__main__":
    main()
