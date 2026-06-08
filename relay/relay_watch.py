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

可靠性（防丢消息 / 断链自愈）：
- 每条转发消息末尾换行附「收到时间」，积压补发时能看出先后。
- 发送失败的消息持久化到 relay_pending.json，每轮按顺序重试，不丢、不乱序。
- 连不上服务器（WinRM 隧道断，端口 15985 拒连）→ 弹 macOS 通知 +
  自动重拉 tunnel.sh（launchd 只保 relay_watch，隧道由 relay_watch 自己保活）。

用法：
  python relay_watch.py            SSE 订阅（默认，近实时）
  python relay_watch.py --poll     轮询模式（每 4s，兼容无推送的情况）
  python relay_watch.py --send X   往队列推一条测试消息 X
"""
import base64
import datetime
import io
import json
import os
import re
import struct
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
import wave
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

import core
import weflow
import queue_push

STATE = HERE / "relay_state.json"
PENDING = HERE / "relay_pending.json"  # 失败的消息（文本/图片），持久化、周期性重试
POLL = 4  # 秒
MAX_RETRIES = 30  # 图片下载最多重试 30 次（~2 分钟）
TRANSCRIBE_MODEL_DIR = os.path.expanduser("~/Documents/WeFlow/models/sensevoice")
_recog = None  # 懒加载，避免启动就占模型内存
_last_notify = {}  # 通知去重：key -> 上次通知时间戳


def _on_signal(signum, frame):
    import traceback as _tb
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    msg = f"{ts} SIGNAL {signum}\n{''.join(_tb.format_stack(frame))}"
    print(msg)
    try:
        with open(HERE / "relay_watch.log", "a", encoding="utf-8") as f:
            f.write(msg + "\n")
    except Exception:
        pass
    import os as _os
    _os._exit(128 + signum)


import signal
signal.signal(signal.SIGHUP, _on_signal)
signal.signal(signal.SIGTERM, _on_signal)


def notify(key, text, cooldown=300):
    """弹一条 macOS 通知（同 key 在 cooldown 秒内只弹一次）。真正弹了返回 True。"""
    now = time.time()
    if now - _last_notify.get(key, 0) < cooldown:
        return False
    _last_notify[key] = now
    print(f"[{datetime.datetime.now():%H:%M:%S}] ⚠ {text}")
    try:
        safe = text.replace('"', "'")
        subprocess.run(
            ["osascript", "-e", f'display notification "{safe}" with title "微信转发"'],
            timeout=10, capture_output=True)
    except Exception:
        pass
    return True


def ensure_tunnel():
    """确保 WinRM 隧道在（连不上时重拉，tunnel.sh 自身幂等：已在则秒退）。"""
    try:
        subprocess.run(["bash", str(HERE / "tunnel.sh")], timeout=45, capture_output=True)
    except Exception:
        pass


def on_disconnect():
    """判定为断链：弹通知 + 自动重拉隧道。通知有 60s 冷却，一轮多条只重连一次。"""
    if notify("conn", "连不上服务器(隧道断)，消息已本地暂存，正在重连隧道…", cooldown=60):
        ensure_tunnel()


def ts_str(ts):
    """时间格式：同日 HH:MM，跨日 MM-DD HH:MM。"""
    if not ts:
        return ""
    dt = datetime.datetime.fromtimestamp(ts)
    fmt = "%H:%M" if dt.date() == datetime.date.today() else "%m-%d %H:%M"
    return dt.strftime(fmt)


def _get_recognizer():
    global _recog
    if _recog is None:
        from sherpa_onnx import offline_recognizer
        _recog = offline_recognizer.OfflineRecognizer.from_sense_voice(
            model=os.path.join(TRANSCRIBE_MODEL_DIR, "model.int8.onnx"),
            tokens=os.path.join(TRANSCRIBE_MODEL_DIR, "tokens.txt"),
            language="zh",
            use_itn=True,
            num_threads=2,
        )
    return _recog


def transcribe_wav(wav_bytes):
    """16-bit PCM WAV → 转文字。失败返回 None。"""
    try:
        with wave.open(io.BytesIO(wav_bytes), 'rb') as wf:
            sr = wf.getframerate()
            raw = wf.readframes(wf.getnframes())
        n = len(raw) // 2
        floats = [s / 32768.0 for s in struct.unpack(f'<{n}h', raw)]
        rec = _get_recognizer()
        s = rec.create_stream()
        s.accept_waveform(sr, floats)
        rec.decode_stream(s)
        return (s.result.text or "").strip()
    except Exception as e:
        print(f"    语音转文字失败: {e}")
        return None


def load_contacts():
    f = ROOT / "data" / "contacts.json"
    return json.loads(f.read_text()) if f.exists() else {}


def load_state():
    return json.loads(STATE.read_text()) if STATE.exists() else {}


def save_state(s):
    STATE.write_text(json.dumps(s, ensure_ascii=False, indent=2))


def load_pending():
    return json.loads(PENDING.read_text()) if PENDING.exists() else {}


def save_pending(p):
    PENDING.write_text(json.dumps(p, ensure_ascii=False, indent=2))


def msg_key(m):
    return f"{m.get('createTime')}:{m.get('localId')}"


def fmt(name, m):
    """返回纯内容（不含发送者名），调用方拼「名字 时间\\n内容」。"""
    lt = m.get("localType")
    content = (m.get("content") or "").strip()

    if lt == 10000 and "<revokemsg>" in content:
        who_str = "你" if m.get("isSend") else name
        return f"[{who_str} 撤回了一条消息]"

    if lt == 34:  # 语音
        if content:
            return f"[语音] {content}"
        return "[语音]"

    txt = content if lt == 1 else (weflow.TAG.get(lt) or "[其他]")
    if not txt:
        return None
    return txt


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


def _extract_image_md5(raw_content):
    """从 rawContent XML 提取图片 md5。失败返回 None。"""
    try:
        m = re.search(r'md5="([a-f0-9]{32})"', raw_content)
        return m.group(1) if m else None
    except Exception:
        return None


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
            # Fallback: media=1 有时候不给 mediaUrl（WeFlow bug），
            # 从 rawContent 提取 md5 直接构造媒体 URL
            if msg.get("localType") == 3:
                md5 = _extract_image_md5(msg.get("rawContent", ""))
                if md5:
                    for ext in (".jpg", ".png", ".gif", ".webp"):
                        fb = f"{base}/api/v1/media/{wxid}/images/{md5}{ext}?access_token={tok}"
                        try:
                            with urllib.request.urlopen(fb, timeout=10) as r:
                                b64 = base64.b64encode(r.read()).decode()
                                print(f"    fallback 图片下载成功: {md5}{ext}")
                                return "image", b64
                        except Exception:
                            continue
    return None, None


def try_push_text(text, client_holder, retries=3):
    """发一条文本到服务器队列。失败立即重试（隧道闪断常见），都失败才返回 False。"""
    for i in range(retries):
        try:
            if client_holder[0] is None:
                client_holder[0] = queue_push.make_client()
            queue_push.push(text, client_holder[0])
            return True
        except Exception:
            client_holder[0] = None
            if i < retries - 1:
                time.sleep(1)
                ensure_tunnel()
    return False


def _push_img(b64, client_holder, retries=3):
    """把 base64 图片写进服务器 .img 队列。失败立即重试。"""
    for i in range(retries):
        try:
            if client_holder[0] is None:
                client_holder[0] = queue_push.make_client()
            name = f"{time.time_ns()}.img"
            ps = (
                f"$b=[Convert]::FromBase64String('{b64}');"
                f"[IO.File]::WriteAllBytes('C:\\relay\\queue\\{name}',$b);'{name}'"
            )
            _, _, had_err = client_holder[0].execute_ps(ps)
            if not had_err:
                return True
        except Exception:
            client_holder[0] = None
            if i < retries - 1:
                time.sleep(1)
                ensure_tunnel()
    return False


def _push_media(env, wxid, m, client_holder):
    """导出图片原图并推送到服务器 .img 队列。成功 True，否则 False。"""
    mtype, b64 = download_media(env, wxid, m.get("localId"))
    if not b64:
        return False
    return _push_img(b64, client_holder)


def _enqueue_text(pending, wxid, m, text):
    pending[f"{wxid}:{m.get('localId')}"] = {
        "kind": "text", "text": text, "ts": m.get("createTime"), "retries": 0}


def retry_pending(env, pending, client_holder):
    """补发本地积压的失败消息（文本/图片）。按插入顺序发，突发连不上则整体留到下轮。
    返回是否改动了 pending。"""
    modified = False
    for key in list(pending.keys()):
        e = pending[key]
        kind = e.get("kind", "image")
        if kind == "text":
            if try_push_text(e["text"], client_holder):
                head = e["text"].splitlines()[0]
                print(f"[{datetime.datetime.now():%H:%M:%S}] -> 补发成功 {head[:40]}")
                del pending[key]
                modified = True
            else:
                on_disconnect()
                break  # 连不上，保留顺序与全部消息，下轮再来
        else:  # image
            if e.get("retries", 0) >= MAX_RETRIES:
                print(f"[{datetime.datetime.now():%H:%M:%S}] 放弃重试 {key}")
                del pending[key]
                modified = True
                continue
            mtype, b64 = download_media(env, e["wxid"], e["local_id"])
            if not b64:
                e["retries"] = e.get("retries", 0) + 1  # 媒体还没导出好，下次再下
                modified = True
                continue
            if _push_img(b64, client_holder):
                print(f"[{datetime.datetime.now():%H:%M:%S}] -> 图片补发成功")
                del pending[key]
                modified = True
            else:
                on_disconnect()
                break
    return modified


def process_once(env, contacts, state, echo_names, client_holder, pending):
    """扫一遍所有盯的人，把新消息（文字+图片）转发出去；失败的本地记录并重试。"""
    changed = False
    pending_modified = retry_pending(env, pending, client_holder)

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
            for m in fresh:
                lt = m.get("localType")
                who = "我" if m.get("isSend") else name
                time_str = ts_str(m.get("createTime"))
                header = f"【消息】{who} {time_str}"
                if lt == 3:  # 图片
                    prefix = f"{header}\n发了图片"
                    if not try_push_text(prefix, client_holder):
                        _enqueue_text(pending, wxid, m, prefix)
                        pending_modified = True
                        on_disconnect()
                    if _push_media(env, wxid, m, client_holder):
                        print(f"[{datetime.datetime.now():%H:%M:%S}] -> {who} 发了图片 (已传原图)")
                    else:
                        pending[f"img:{wxid}:{m.get('localId')}"] = {
                            "kind": "image", "wxid": wxid,
                            "local_id": m.get("localId"), "retries": 0}
                        pending_modified = True
                        print(f"[{datetime.datetime.now():%H:%M:%S}] -> 图片暂存，待重试")
                elif lt == 34:  # 语音
                    dur = ""
                    raw = m.get("rawContent") or m.get("content") or ""
                    m_len = re.search(r'voicelength="(\d+)"', str(raw))
                    if m_len:
                        sec = int(m_len.group(1)) // 1000
                        dur = f" {sec}s" if sec < 60 else f" {sec//60}m{sec%60}s"
                    _, wav_b64 = download_media(env, wxid, m.get("localId"))
                    text = ""
                    if wav_b64:
                        text = transcribe_wav(base64.b64decode(wav_b64)) or ""
                    body = f"发了语音{dur}{f': {text}' if text else ''}"
                    line_full = f"{header}\n{body}"
                    if try_push_text(line_full, client_holder):
                        print(f"[{datetime.datetime.now():%H:%M:%S}] -> {who} 语音")
                    else:
                        _enqueue_text(pending, wxid, m, line_full)
                        pending_modified = True
                        on_disconnect()
                        print(f"[{datetime.datetime.now():%H:%M:%S}] -> 语音暂存，待重试")
                else:
                    line = fmt(name, m)
                    if not line:
                        continue
                    if is_echo(m, echo_names):
                        print(f"[{datetime.datetime.now():%H:%M:%S}] 跳过回声 {line[:40]}")
                        continue
                    line_full = f"{header}\n{line}"
                    if try_push_text(line_full, client_holder):
                        print(f"[{datetime.datetime.now():%H:%M:%S}] -> {header} {line[:20]}")
                    else:
                        _enqueue_text(pending, wxid, m, line_full)
                        pending_modified = True
                        on_disconnect()
                        print(f"[{datetime.datetime.now():%H:%M:%S}] -> 暂存待重试 {header}")
            state[wxid] = msg_key(msgs[-1]); changed = True
        except Exception as e:
            print(f"[err] {name}: {e}")
            client_holder[0] = None
    if changed:
        save_state(state)
    if pending_modified:
        save_pending(pending)


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
    for wxid, info in contacts.items():
        ms = weflow.messages(env, wxid, 40)
        if not ms:
            continue
        name = info.get("name", wxid)
        if wxid not in state:
            state[wxid] = msg_key(ms[-1])
            continue
        # 检查上次转发后来了多少新消息
        fresh = new_after(ms, state.get(wxid))
        if fresh:
            print(f"[启动] {name} 有 {len(fresh)} 条新消息待转发")
    save_state(state)


def sse_url(env):
    base = (env.get("WEFLOW_API") or "http://127.0.0.1:5031").rstrip("/")
    tok = urllib.parse.quote(env.get("WEFLOW_ACCESS_TOKEN", ""))
    return f"{base}/api/v1/push/messages?access_token={tok}"


def run_sse(env, contacts):
    import threading

    state = load_state()
    pending = load_pending()
    echo_names = {v.get("name", k) for k, v in contacts.items()} | {"我"}
    monitored_ids = set(contacts.keys())  # 只处理这些人的 SSE 事件
    client_holder = [None]
    lock = threading.Lock()
    ensure_tunnel()  # 启动先确保隧道在
    init_state(env, contacts, state)
    names = [v.get("name", k) for k, v in contacts.items()]
    print(f"SSE 订阅启动，盯：{names}。message.new/revoke 近实时 + {POLL}s 兜底。")

    def periodic():
        while True:
            time.sleep(POLL)
            try:
                with lock:
                    process_once(env, contacts, state, echo_names, client_holder, pending)
            except Exception:
                import traceback
                ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                print(f"{ts} [periodic] {traceback.format_exc()}")

    threading.Thread(target=periodic, daemon=True).start()

    while True:
        last_event_id = ""
        try:
            req = urllib.request.Request(sse_url(env))
            if last_event_id:
                req.add_header("Last-Event-ID", last_event_id)
            with urllib.request.urlopen(req, timeout=70) as r:
                with lock:
                    process_once(env, contacts, state, echo_names, client_holder, pending)
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
                                            with lock:
                                                hdr = f"系统 {ts_str(int(time.time()))}"
                                                full = f"{hdr}\n{revoke_line}"
                                                if try_push_text(full, client_holder):
                                                    print(f"[{datetime.datetime.now():%H:%M:%S}] -> {revoke_line}")
                                                else:
                                                    on_disconnect()
                                                    print(f"[{datetime.datetime.now():%H:%M:%S}] 撤回通知发送失败，已暂记")
                                    except Exception:
                                        pass
                        elif ev and ev != "ready":
                            with lock:
                                process_once(env, contacts, state, echo_names, client_holder, pending)
        except KeyboardInterrupt:
            raise
        except Exception as e:
            print(f"[sse] 断开，2s 后重连：{e}")
            time.sleep(2)


def run_poll(env, contacts):
    state = load_state()
    pending = load_pending()
    echo_names = {v.get("name", k) for k, v in contacts.items()} | {"我"}
    client_holder = [None]
    ensure_tunnel()  # 启动先确保隧道在
    init_state(env, contacts, state)
    names = [v.get("name", k) for k, v in contacts.items()]
    print(f"轮询监听启动，盯：{names}，每 {POLL}s 一次。")
    while True:
        try:
            process_once(env, contacts, state, echo_names, client_holder, pending)
        except Exception:
            import traceback
            ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            print(f"{ts} [poll] {traceback.format_exc()}")
        time.sleep(POLL)


def main():
    try:
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
    except Exception:
        import traceback
        ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        msg = f"{ts} FATAL\n{traceback.format_exc()}"
        print(msg)
        # 写进日志文件
        try:
            with open(HERE / "relay_watch.log", "a", encoding="utf-8") as f:
                f.write(msg + "\n")
        except Exception:
            pass
        sys.exit(1)


if __name__ == "__main__":
    main()
