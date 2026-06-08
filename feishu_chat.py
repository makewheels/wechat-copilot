#!/usr/bin/env python3
"""飞书双向：消费 im.message.receive_v1 事件 —— 你在飞书发问 → 军师带上下文回你。

机制：lark-cli event consume 流式吐 NDJSON，本脚本逐行读，
每条用户消息当成对军师的提问，默认指"当前在聊的对象"(watch 写的 chat_state.json)，
生成回答后回到原会话(chat_id)。

启动：python3 feishu_chat.py
前提：控制台启用 im.message.receive_v1 事件 + 加 im:message.p2p_msg:readonly /
      im:message.group_at_msg:readonly 权限 + 发布版本。
"""
import json
import logging
import os
import subprocess
from pathlib import Path

logging.basicConfig(
    filename=str(Path(__file__).resolve().parent / 'data' / 'chat.log'),
    level=logging.INFO, format='%(asctime)s %(message)s')

import core
import weflow
import feishu_push

HERE = Path(__file__).resolve().parent
CHAT_STATE = HERE / "data" / "chat_state.json"
CONTACTS = HERE / "data" / "contacts.json"


def current_target():
    """当前在聊谁：watch 推送时写的；没有就拿 contacts 第一个。"""
    if CHAT_STATE.exists():
        try:
            d = json.loads(CHAT_STATE.read_text())
            return d.get("name", ""), d.get("wxid", "")
        except Exception:
            pass
    cs = json.loads(CONTACTS.read_text()) if CONTACTS.exists() else {}
    for wxid, info in cs.items():
        return info.get("name", wxid), wxid
    return "", ""


def profile_of(wxid):
    cs = json.loads(CONTACTS.read_text()) if CONTACTS.exists() else {}
    return cs.get(wxid, {}).get("profile", "")


def parse_text(content: str) -> str:
    """事件 content 多数已是纯文本；若是 {"text":"..."} 就取出来。"""
    content = (content or "").strip()
    if content.startswith("{"):
        try:
            return (json.loads(content).get("text") or "").strip()
        except Exception:
            pass
    return content


def answer(env, question: str, chat_id: str):
    name, wxid = current_target()
    trans = ""
    if wxid:
        try:
            msgs = weflow.messages(env, wxid, 60)
            trans = weflow.transcript(msgs, 60)
        except Exception as e:
            logging.info(f"取对话失败: {e}")
    user = core.build_chat_user(name, profile_of(wxid), trans, question)
    reply = core.call_qwen(env, core.build_system(), user)
    feishu_push.push(reply, markdown=True, chat_id=chat_id)
    logging.info(f"已回答（对象={name}）：{question[:30]} -> {len(reply)}字")


def main():
    env = core.load_env()
    cli = feishu_push._env().get("LARK_CLI", "lark-cli")
    cenv = dict(os.environ)
    cenv["PATH"] = cenv.get("PATH", "") + ":" + str(Path(cli).parent)
    cenv.pop("HERMES_HOME", None); cenv.pop("OPENCLAW_HOME", None)

    logging.info("飞书双向监听启动")
    print("飞书双向监听启动，在飞书里问军师吧（Ctrl+C 停）")
    proc = subprocess.Popen(
        [cli, "event", "consume", "im.message.receive_v1", "--as", "bot", "--quiet"],
        stdout=subprocess.PIPE, stdin=subprocess.PIPE,  # stdin 保持开着，否则 consume 立刻退出
        env=cenv, text=True)
    for line in proc.stdout:
        line = line.strip()
        if not line or not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except Exception:
            continue
        # event consume 可能包一层 data/payload，尽量兼容
        payload = ev.get("data") or ev.get("event") or ev
        chat_id = payload.get("chat_id") or ""
        q = parse_text(payload.get("content", ""))
        if not chat_id or not q:
            continue
        logging.info(f"收到提问 chat={chat_id}: {q[:50]}")
        try:
            answer(env, q, chat_id)
        except Exception as e:
            logging.error(f"回答失败: {e}")
            try:
                feishu_push.push(f"⚠️ 军师出错了：{e}", chat_id=chat_id)
            except Exception:
                pass


if __name__ == "__main__":
    main()
