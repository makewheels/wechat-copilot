#!/usr/bin/env python3
"""Mac 端镜像监听器：盯 contacts.json 里的人，把双方新消息（含图片）原样转发到
服务器队列 → 服务器粘进那个微信群。

核心依赖 WeFlow HTTP API (https://github.com/hicccc77/WeFlow)：
- SSE /api/v1/push/messages — message.new / message.revoke 事件近实时推送
- /api/v1/messages?media=1&image=1 — 导出解密后的图片等媒体
- /api/v1/media/<path> — 下载已导出的媒体文件

WeFlow 设置需开启「HTTP API 服务」+「主动推送」。
WeFlow 源码：~/workspace/tools/WeFlow/
API 文档：~/workspace/tools/WeFlow/docs/HTTP-API.md

用法：
  python relay_watch.py            SSE 订阅（默认，近实时）
  python relay_watch.py --poll     轮询模式（每 4s，兼容无推送的情况）
  python relay_watch.py --send X   往队列推一条测试消息 X
"""
import base64
import datetime
import json
import os
import re
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

import core
import weflow
import queue_push

STATE = HERE / "relay_state.json"
POLL = 4  # 秒（--poll 模式的轮询间隔 / SSE 模式的定时兜底）


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

    if lt == 10000 and "<revokemsg>" in content:
        who_str = "你" if m.get("isSend") else name
        return f"[{who_str} 撤回了一条消息]"

    if lt == 34:  # 语音
        if content:
            return f"{who}: [语音] {content}"
        return f"{who}: [语音]"

    txt = content if lt == 1 else (weflow.TAG.get(lt) or "[其他]")
    if not txt:
        return None
    return f"{who}: {txt}"


def is_echo(m, names):
    """防回环：转发出去的消息形如「名字: 内容」，若它又被读回来则跳过。"""
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
    last_ct = int(last_key.split(":")[0])
    return [m for m in msgs if (m.get("createTime") or 0) > last_ct]


def download_media(env, wxid, local_id):
    """给图片/语音的 localId，走 WeFlow media=1 导出并下载解密后的媒体，
    返回 (媒体类型, base64编码数据)。失败返回 (None, None)。"""
    base = (env.get("WEFLOW_API") or "http://127.0.0.1:5031").rstrip("/")
    tok = urllib.parse.quote(env.get("WEFLOW_ACCESS_TOKEN", ""))
    # 用足够大的 limit 拉最近消息，确保覆盖目标消息（WeFlow offset 语义不稳定）
    url = f"{base}/api/v1/messages?talker={wxid}&limit={local_id + 5}&media=1&image=1&voice=1&access_token={tok}"
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            data = json.loads(r.read().decode("utf-8"))
    except Exception as e:
        print(f"    media 导出请求失败: {e}")
        return None, None
    for msg in data.get("messages", []):
        if msg.get("localId") == local_id:
            media_url = msg.get("mediaUrl", "")
            media_type = msg.get("mediaType", "")
            if media_url:
                parsed = urllib.parse.urlparse(media_url)
                dl = f"{base}{parsed.path}?access_token={tok}"
                try:
                    with urllib.request.urlopen(dl, timeout=30) as r:
                        return media_type, base64.b64encode(r.read()).decode()
                except Exception as e:
                    print(f"    下载媒体失败: {e}")
                    return None, None
    return None, None


def push_media(env, wxid, m, client, prefix):
    """导出图片原图，base64 后推送到 Windows .img 队列。前缀文字单独发。"""
    mtype, b64 = download_media(env, wxid, m.get("localId"))
    if not b64:
        return False
    name = f"{time.time_ns()}.img"
    ps = (
        f"$b=[Convert]::FromBase64String('{b64}');"
        f"[IO.File]::WriteAllBytes('C:\\relay\\queue\\{name}',$b);'{name}'"
    )
    out, streams, had_err = client.execute_ps(ps)
    return not had_err


def process_once(env, contacts, state, echo_names, client_holder):
    """扫一遍所有盯的人，把新消息（文字+图片）转发出去。"""
    changed = False
    for wxid, info in contacts.items():
        name = info.get("name", wxid)
        try:
            msgs = weflow.messages(env, wxid, 40)
            if not msgs:
                continue
            if wxid not in state:
                state[wxid] = msg_key(msgs[-1]); changed = True
                continue
            fresh = new_after(msgs, state.get(wxid))
            if not fresh:
                continue
            if client_holder[0] is None:
                client_holder[0] = queue_push.make_client()
            for m in fresh:
                lt = m.get("localType")
                who = "我" if m.get("isSend") else name
                # 图片：先发文字前缀，再传原图（两条消息）
                if lt == 3:
                    prefix = f"{who} 发了图片"
                    queue_push.push(prefix, client_holder[0])
                    ok = push_media(env, wxid, m, client_holder[0], prefix)
                    status = "已传原图" if ok else "原图获取失败"
                    print(f"[{datetime.datetime.now():%H:%M:%S}] -> {prefix} ({status})")
                elif lt == 34:
                    prefix = f"{who} 发了语音"
                    queue_push.push(prefix, client_holder[0])
                    print(f"[{datetime.datetime.now():%H:%M:%S}] -> {prefix}")
                else:
                    line = fmt(name, m)
                    if not line:
                        continue
                    if is_echo(m, echo_names):
                        print(f"[{datetime.datetime.now():%H:%M:%S}] 跳过回声 {line[:40]}")
                        continue
                    queue_push.push(line, client_holder[0])
                    print(f"[{datetime.datetime.now():%H:%M:%S}] -> {line[:60]}")
            state[wxid] = msg_key(msgs[-1]); changed = True
        except Exception as e:
            print(f"[err] {name}: {e}")
            client_holder[0] = None
    if changed:
        save_state(state)


def parse_sse_revoke(data_line):
    """解析 SSE message.revoke 事件，返回格式化的撤回通知。
    事件 content 格式：'对方撤回了一条消息（rawid：xxx） 内容为"你好"'"""
    try:
        evt = json.loads(data_line)
    except json.JSONDecodeError:
        return None
    if evt.get("event") != "message.revoke":
        return None
    content = (evt.get("content") or "").strip()
    # 提取 "内容为"xxx"" 部分（支持中英文双引号和书名号）
    m = re.search(r'内容为[""“”「」](.+?)[""“”「」]', content)
    original = m.group(1) if m else ""
    src = (evt.get("sourceName") or evt.get("sessionId") or "").strip()
    if not src:
        return f"[撤回了一条消息{f'：{original}' if original else ''}]"
    if original:
        return f"[{src} 撤回了一条消息: {original}]"
    return f"[{src} 撤回了一条消息]"


def init_state(env, contacts, state):
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
    monitored_ids = set(contacts.keys())  # 只处理这些人的 SSE 事件
    client_holder = [None]
    lock = threading.Lock()
    init_state(env, contacts, state)
    names = [v.get("name", k) for k, v in contacts.items()]
    print(f"SSE 订阅启动，盯：{names}。message.new/revoke 近实时 + {POLL}s 兜底。")

    def periodic():
        while True:
            time.sleep(POLL)
            with lock:
                process_once(env, contacts, state, echo_names, client_holder)

    threading.Thread(target=periodic, daemon=True).start()

    while True:
        last_event_id = ""
        try:
            req = urllib.request.Request(sse_url(env))
            if last_event_id:
                req.add_header("Last-Event-ID", last_event_id)
            with urllib.request.urlopen(req, timeout=70) as r:
                with lock:
                    process_once(env, contacts, state, echo_names, client_holder)
                for raw in r:
                    line = raw.decode("utf-8", "replace").rstrip("\r\n")
                    if line.startswith("id:"):
                        last_event_id = line[3:].strip()
                        continue
                    if line.startswith("event:"):
                        ev = line[6:].strip()
                        if ev == "message.revoke":
                            try:
                                data_raw = next(r).decode("utf-8", "replace").rstrip("\r\n")
                            except StopIteration:
                                continue
                            if data_raw.startswith("data:"):
                                revoke_line = parse_sse_revoke(data_raw[5:].strip())
                                if revoke_line:
                                    # 解析看是不是盯的人在撤回
                                    try:
                                        evt = json.loads(data_raw[5:].strip())
                                        sid = evt.get("sessionId", "")
                                        # 只转发被盯的联系人的撤回
                                        if sid in monitored_ids:
                                            if client_holder[0] is None:
                                                client_holder[0] = queue_push.make_client()
                                            queue_push.push(revoke_line, client_holder[0])
                                            print(f"[{datetime.datetime.now():%H:%M:%S}] -> {revoke_line}")
                                    except Exception:
                                        pass
                        elif ev and ev != "ready":
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
    print(f"轮询监听启动，盯：{names}，每 {POLL}s 一次。")
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
