#!/usr/bin/env python3
"""军师监听器：盯着 watchlist 里的对象，她一回新消息 → 自动出建议 → 推到飞书群。

用法：
  python3 watch.py          前台持续监听（Ctrl+C 停）
  python3 watch.py --once   立刻给每个对象出一版建议并推送（测试用）

要盯谁，改下面的 WATCH。状态存 data/watch_state.json（只对"她发的、且比上次新的"消息触发）。
"""
import datetime
import json
import logging
import sys
import time
from pathlib import Path

logging.basicConfig(
    filename=str(Path(__file__).resolve().parent / 'data' / 'watch.log'),
    level=logging.INFO,
    format='%(asctime)s %(message)s'
)

import core
import weflow

HERE = Path(__file__).resolve().parent
STATE = HERE / "data" / "watch_state.json"
POLL = 15  # 秒

ADVICE = HERE / "data" / "advice.txt"


import re


def try_wechat_push(text: str, markdown: bool = False, relay_too: bool = False):
    # 只推飞书（微信 relay / Windows 通道已下线，relay_too 保留参数但不再生效）
    try:
        from feishu_push import push   # 飞书群（唯一推送渠道）
        logging.info(f"feishu push: {push(text, markdown=markdown)}")
    except Exception as e:
        logging.info(f"feishu push skip: {e}")


CHAT_STATE = HERE / "data" / "chat_state.json"


def set_current(name: str, wxid: str):
    CHAT_STATE.write_text(json.dumps({"name": name, "wxid": wxid}, ensure_ascii=False))


def deliver(name: str, text: str, wxid: str = "", push_fs: bool = True):
    if wxid:
        set_current(name, wxid)   # 记下当前在聊谁，双向问答默认指这人
    ts = datetime.datetime.now().strftime("%H:%M")
    with ADVICE.open("a", encoding="utf-8") as f:   # 存文件兜底
        f.write(f"\n{'━' * 42}\n🕐 {ts}  【{name}】\n{'━' * 42}\n{text}\n")

    if push_fs:
        # 只给一条建议：取第一非空行，去掉可能的序号/标签/引号
        line = next((l for l in text.splitlines() if l.strip()), text).strip()
        d = re.sub(r'^\s*[\d①-⑩\-\*•.、)）]+\s*', '', line).strip().strip('"""')
        try_wechat_push(d or text.strip(), relay_too=False)

# 要盯的对象：读 data/contacts.json
def load_contacts():
    f = HERE / "data" / "contacts.json"
    return json.loads(f.read_text()) if f.exists() else {}



def load_state():
    return json.loads(STATE.read_text()) if STATE.exists() else {}


def save_state(s):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(s, ensure_ascii=False, indent=2))




def advise(env, name, profile, msgs):
    trans = weflow.transcript(msgs, 60)
    last = datetime.datetime.fromtimestamp(msgs[-1].get("createTime", 0)).strftime("%m-%d %H:%M")
    time_ctx = f"今天 {datetime.date.today()}。她刚发来消息（最新 {last}）。"
    user = core.build_user(name, profile, "", trans, "", time_ctx, single=True)
    return core.call_qwen(env, core.build_system(single=True), user)


def msg_key(m):
    return f"{m.get('createTime')}:{m.get('localId')}"


def main():
    env = core.load_env()

    contacts = load_contacts()

    if "--once" in sys.argv:
        for wxid, info in contacts.items():
            name = info.get("name", wxid)
            msgs = weflow.messages(env, wxid, 40)
            if not msgs:
                print(f"{name}: 本机库无消息，跳过"); continue
            print(f"{name}: 生成建议中…")
            adv = advise(env, name, info.get("profile", ""), msgs)
            deliver(name, adv)
            print(f"{name}: 已推送到微信")
        return

    state = load_state()
    names = [v.get("name", k) for k, v in contacts.items()]
    logging.info(f"启动，盯：{names}，每 {POLL}s 一次。")
    print(f"军师监听启动，盯：{names}，每 {POLL}s 看一次。Ctrl+C 停。")
    while True:
        for wxid, info in contacts.items():
            name = info.get("name", wxid)
            profile = info.get("profile", "")
            try:
                msgs = weflow.messages(env, wxid, 40)
                if not msgs:
                    continue
                last = msgs[-1]
                key = msg_key(last)
                if wxid not in state:
                    state[wxid] = ""; save_state(state)
                    logging.info(f"{name} 首次见，最后一条 key={key} isSend={last.get('isSend')}")
                    if last.get("isSend"):
                        continue
                if last.get("isSend"):
                    state[wxid] = key; save_state(state); continue
                if state.get(wxid) == key:
                    continue
                logging.info(f"{name} 有新回复 key={key}，出建议…")
                print(f"[{datetime.datetime.now():%H:%M:%S}] {name} 有新回复，出建议…")
                # 状态先行：先记下这条已处理，无论出建议成败都不再重判（防死循环刷屏）
                state[wxid] = key
                save_state(state)
                last_text = (last.get("content") or "")[:30] if last.get("localType") == 1 else "[非文字]"
                try:
                    adv = advise(env, name, profile, msgs)
                except Exception as e:
                    logging.error(f"{name} 出建议失败，跳过这条：{e}")
                    print(f"[err] {name} 出建议失败：{e}")
                    continue
                logging.info(f"{name} 建议长度={len(adv)}")
                now = datetime.datetime.now().strftime("%H:%M")
                quote = last_text[:8] + ("…" if len(last_text) > 8 else "")
                header = f"【回复建议】{name} {now} ·「{quote}」"
                # 只发飞书（去掉了「军师思考中…」的噪音预告）
                try_wechat_push(f"{header}\n{adv}")
                # 写本地日志
                deliver(name, adv, wxid, push_fs=False)
                logging.info(f"{name} 已投递")
            except Exception as e:
                logging.error(f"{name}: {e}")
                print(f"[err] {name}: {e}")
        time.sleep(POLL)


if __name__ == "__main__":
    main()
