#!/usr/bin/env python3
"""陪聊·全自动盯梢：bot 自己盯对象，建议自动来飞书；你只管按不按发。

- 反应式：她一回新消息 → 自动出接话建议 → 推飞书。
- 主动式：每天挑一次时机 → 判该不该主动；可发就给开场，该收就提醒你"沉住气等她"。
你啥都不用做。发不发、发出去，你决定（保住"学会自己聊"+发错能拦）。

启动：python3 auto.py
前提：.env 有 FEISHU_CHAT_ID（推给你的私聊）+ WeFlow 在跑。
"""
import datetime
import json
import logging
import time
from pathlib import Path

import core
import chat_copilot as cc
import feishu_push

HERE = Path(__file__).resolve().parent
STATE = HERE / "data" / "auto_state.json"
TARGETS = cc._CFG.get("auto_targets") or list(cc.CONTACTS)   # 要盯谁，见 data/config.json
POLL = 30                 # 秒
DAY_START, DAY_END = 9, 22  # 主动提醒只在白天

logging.basicConfig(filename=str(HERE / "data" / "auto.log"),
                    level=logging.INFO, format="%(asctime)s %(message)s")


def load_state():
    return json.loads(STATE.read_text()) if STATE.exists() else {}


def save_state(s):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(s, ensure_ascii=False, indent=1))


def key_of(m):
    return f"{m['ts']}:{m['text'][:8]}"


def reactive(env, name, msgs, st):
    """她一回新消息 → 推接话建议。"""
    last = msgs[-1]
    k = key_of(last)
    rec = st.get(name)
    if rec is None:                       # 首次见这个人：只记录，不补发历史
        st[name] = {"key": k, "nudge": ""}
        return
    if last["who"] == "我" or rec.get("key") == k:
        rec["key"] = k
        return
    rec["key"] = k                        # 状态先行，防刷屏
    logging.info(f"{name} 新回复：{last['text'][:20]}")
    best, why, dbg = cc.best_line(env, cc.fmt(msgs, 30), cc.voice_samples(msgs), last["text"],
                                  profile=cc.load_profile(name), me=cc.load_me(),
                                  time_ctx=cc.time_context(msgs))
    if best:
        body = f"🔔 接话 · {name}\n\n她说：{last['text']}\n\n👉 建议回复：\n{best}\n\n（{why}）"
    else:
        body = f"🔔 接话 · {name}\n\n她说：{last['text']}\n\n先别接：{dbg.get('gate', '没好接的')}"
    feishu_push.push(body, markdown=True)


def proactive(env, name, msgs, st):
    """每天一次：判该不该主动，给开场 或 提醒沉住气。"""
    today = datetime.date.today().isoformat()
    rec = st.setdefault(name, {})
    if rec.get("nudge") == today:
        return
    if not (DAY_START <= datetime.datetime.now().hour < DAY_END):
        return
    rec["nudge"] = today
    tm = cc.timing(msgs)
    logging.info(f"{name} 每日主动判定：{tm['verdict']}")
    if tm["send"]:
        best, why, _ = cc.best_line(
            env, cc.fmt(msgs, 30), cc.voice_samples(msgs), "",
            profile=cc.load_profile(name), me=cc.load_me(), time_ctx=cc.time_context(msgs),
            intent="主动开场。别嘘寒问暖；由头只能用真聊过的事或她画像里的真事，不许编造场景。")
        if best:
            feishu_push.push(
                f"📿 主动 · {name}\n\n判断：可以发\n{tm['why']}\n\n👉 开场白：\n{best}\n\n（{why}）",
                markdown=True)
    else:
        feishu_push.push(f"📿 主动 · {name}\n\n判断：今天先别主动\n\n{tm['why']}\n（球在她那，沉住气等她）",
                         markdown=True)


def main():
    env = core.load_env()
    if not feishu_push._env().get("FEISHU_CHAT_ID"):
        print("⚠️ .env 缺 FEISHU_CHAT_ID，自动推送没法发")
        return
    st = load_state()
    logging.info(f"全自动盯梢启动，盯：{TARGETS}")
    print(f"全自动盯梢启动，盯：{TARGETS}，每 {POLL}s 看一次。Ctrl+C 停。")
    while True:
        for name in TARGETS:
            try:
                msgs = cc.pull(env, name)
                if not msgs:
                    continue
                reactive(env, name, msgs, st)
                proactive(env, name, msgs, st)
                save_state(st)
            except Exception as e:
                logging.error(f"{name}: {e}")
        time.sleep(POLL)


if __name__ == "__main__":
    main()
