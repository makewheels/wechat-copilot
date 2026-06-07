"""WeFlow 本地只读 API 客户端（读微信）+ 消息格式化。

依赖 .env 里的 WEFLOW_API / WEFLOW_ACCESS_TOKEN。
"""
import datetime
import json
import time
import urllib.parse
import urllib.request

TAG = {1: None, 34: "[语音]", 3: "[图片]", 43: "[视频]", 47: "[表情]",
       49: "[链接/小程序]", 42: "[名片]", 48: "[位置]", 50: "[通话]", 10000: "[系统]"}


def _get(env, path, params):
    base = (env.get("WEFLOW_API") or "http://127.0.0.1:5031").rstrip("/")
    p = dict(params or {})
    p["access_token"] = env.get("WEFLOW_ACCESS_TOKEN", "")
    url = f"{base}{path}?{urllib.parse.urlencode(p)}"
    with urllib.request.urlopen(url, timeout=25) as r:
        return json.loads(r.read().decode("utf-8"))


def sessions(env, limit=80):
    return _get(env, "/api/v1/sessions", {"limit": limit}).get("sessions", [])


def messages(env, talker, limit=40):
    ms = []
    for _ in range(6):  # 首次读偶尔返回空/旧数据，多试几次
        ms = _get(env, "/api/v1/messages", {"talker": talker, "limit": limit}).get("messages", [])
        if ms:
            break
        time.sleep(1)
    ms.sort(key=lambda m: m.get("sortSeq") or m.get("createTime", 0) * 1000)
    return ms


def transcript(msgs, n=40):
    lines = []
    for m in msgs[-n:]:
        ts = m.get("createTime", 0)
        t = datetime.datetime.fromtimestamp(ts).strftime("%m-%d %H:%M") if ts else "?"
        who = "我" if m.get("isSend") else "她"
        lt = m.get("localType")
        txt = (m.get("content") or "") if lt == 1 else (TAG.get(lt) or "[其他]")
        txt = str(txt).replace("\n", " ")
        if len(txt) > 120:
            txt = txt[:120] + "…"
        if txt:
            lines.append(f"[{t}] {who}: {txt}")
    return "\n".join(lines)
