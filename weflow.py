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


# 系统/工具账号，列表里没意义，过滤掉
SYS_ACCOUNTS = {"filehelper", "weixin", "newsapp", "fmessage", "medianote",
                "floatbottle", "qqmail", "qmessage", "tmessage", "qqsync",
                "mphelper", "brandsessionholder", "notifymessage", "officialaccounts"}


def chat_sessions(env, limit=500):
    """过滤 + 按最近时间排好的真实会话：[{username, name, is_group, ts}]，最新在前。
    去掉公众号(gh_)和系统账号；保留个人(含老式自定义号)和群。"""
    out = []
    for s in sessions(env, limit):
        u = s.get("username", "")
        if not u or u.startswith("gh_") or u in SYS_ACCOUNTS or "gelivable" in u:
            continue
        is_group = (s.get("sessionType") == "group") or u.endswith("@chatroom")
        out.append({"username": u, "name": s.get("displayName") or u,
                    "is_group": is_group, "ts": s.get("lastTimestamp") or 0})
    out.sort(key=lambda x: x["ts"], reverse=True)
    return out


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
