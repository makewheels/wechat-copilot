#!/usr/bin/env python3
"""陪聊·飞书：你私聊 bot，想说啥说啥 → 军师自动分流（接话/时机/讨论/归档/反馈）。

机制同 feishu_chat.py：lark-cli event consume 收 im.message.receive_v1，回到原会话。
引擎 = chat_copilot（生成→硬闸→检测器 + 她的画像 + 你的画像 + 意图路由）。

在飞书里（不用记格式，说人话）：
  她说的话              → 接话：最佳一条 + 为什么
  "主动"/"开场"         → 该不该主动 + 开场
  关于她的事实           → 自动记进她的画像
  你的想法/顾虑/计划      → 自动记下
  问军师/想讨论          → 它带全上下文答你
  "那条发了她秒回"等反馈   → 记进反馈回路（越用越准）
  "切 <对象>"           → 换对象（对象名见 data/config.json）
启动：python3 feishu_copilot.py
前提：lark-cli 配好 + 控制台开 im.message.receive_v1 + WeFlow 在跑。
"""
import json
import logging
import os
import subprocess
from pathlib import Path

import core
import chat_copilot as cc
import feishu_push

HERE = Path(__file__).resolve().parent
logging.basicConfig(filename=str(HERE / "data" / "copilot.log"),
                    level=logging.INFO, format="%(asctime)s %(message)s")
TARGET = HERE / "data" / "copilot_target.json"
DEFAULT = cc._CFG.get("default_target") or next(iter(cc.CONTACTS), "")


def get_target():
    if TARGET.exists():
        try:
            return json.loads(TARGET.read_text()).get("name", DEFAULT)
        except Exception:
            pass
    return DEFAULT


def set_target(name):
    TARGET.write_text(json.dumps({"name": name}, ensure_ascii=False))


def push(text, chat_id, md=True):
    feishu_push.push(text, markdown=md, chat_id=chat_id)


def parse_text(content: str) -> str:
    content = (content or "").strip()
    if content.startswith("{"):
        try:
            return (json.loads(content).get("text") or "").strip()
        except Exception:
            pass
    return content


def do_reply(env, name, her, chat_id):
    msgs = cc.pull(env, name)
    best, why, dbg = cc.best_line(env, cc.fmt(msgs, 30), cc.voice_samples(msgs), her,
                                  profile=cc.load_profile(name), me=cc.load_me(),
                                  time_ctx=cc.time_context(msgs))
    if best:
        out = f"🔔 接话 · {name}\n\n她说：{her}\n\n👉 建议回复：\n{best}\n\n（{why}）"
    else:
        out = f"🔔 接话 · {name}\n\n她说：{her}\n\n先别接：{dbg.get('gate', '没好接的')}"
    push(out, chat_id)


def do_active(env, name, chat_id):
    msgs = cc.pull(env, name)
    tm = cc.timing(msgs)
    if not tm["send"]:
        push(f"📿 主动 · {name}\n\n判断：今天先别主动\n\n{tm['why']}", chat_id)
        return
    best, why, _ = cc.best_line(
        env, cc.fmt(msgs, 30), cc.voice_samples(msgs), "",
        profile=cc.load_profile(name), me=cc.load_me(), time_ctx=cc.time_context(msgs),
        intent="主动开场。别嘘寒问暖；由头只能用你俩真聊过的事或她画像里的真事，不许编造场景。")
    if best:
        push(f"📿 主动 · {name}\n\n判断：可以发\n{tm['why']}\n\n👉 开场白：\n{best}\n\n（{why}）", chat_id)
    else:
        push(f"📿 主动 · {name}\n\n判断：可发，但没攒出像样的开场 → 先别硬发", chat_id)


def handle(env, text, chat_id):
    name = get_target()
    # —— 关键词快捷路（绝不误判）——
    if text.startswith("切"):
        new = text[1:].strip()
        if new in cc.CONTACTS:
            set_target(new)
            push(f"已切到 **{new}**", chat_id)
        else:
            push(f"不认识「{new}」，已知：{'、'.join(cc.CONTACTS)}", chat_id)
        return
    if text in ("主动", "开场", "主动发", "我想发", "我要发"):
        do_active(env, name, chat_id)
        return
    if text in ("拉", "最新", "读", "看看"):
        msgs = cc.pull(env, name)
        her = next((m["text"] for m in reversed(msgs) if m["who"] == "她"), "")
        if her:
            do_reply(env, name, her, chat_id)
        else:
            push(f"没读到 {name} 她发的消息", chat_id)
        return

    # —— 自然语言 → 意图路由 ——
    typ, arg = cc.route(env, text)
    if typ == "switch" and arg in cc.CONTACTS:
        set_target(arg)
        push(f"已切到 **{arg}**", chat_id)
    elif typ == "active":
        do_active(env, name, chat_id)
    elif typ == "fact":
        cc.append_fact(name, text)
        push(f"✅ 记进 **{name}** 的画像了", chat_id)
    elif typ == "intent":
        cc.append_line(name, "intents.jsonl", {"note": text})
        push("✅ 记下你的想法了，回复时会带上", chat_id)
    elif typ == "feedback":
        cc.append_line(name, "feedback.jsonl", {"note": text})
        push("✅ 记下反馈了，军师会越用越准", chat_id)
    elif typ == "consult":
        push(cc.consult(env, name, text), chat_id)
    else:  # reply
        do_reply(env, name, text, chat_id)


def main():
    env = core.load_env()
    cli = feishu_push._env().get("LARK_CLI", "lark-cli")
    cenv = dict(os.environ)
    cenv["PATH"] = cenv.get("PATH", "") + ":" + str(Path(cli).parent)
    cenv.pop("HERMES_HOME", None)
    cenv.pop("OPENCLAW_HOME", None)

    logging.info("陪聊·飞书启动")
    print(f"陪聊·飞书启动，默认对象={get_target()}。飞书私聊 bot 即可（Ctrl+C 停）")
    proc = subprocess.Popen(
        [cli, "event", "consume", "im.message.receive_v1", "--as", "bot", "--quiet"],
        stdout=subprocess.PIPE, stdin=subprocess.PIPE, env=cenv, text=True)
    for line in proc.stdout:
        line = line.strip()
        if not line or not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except Exception:
            continue
        payload = ev.get("data") or ev.get("event") or ev
        chat_id = payload.get("chat_id") or ""
        q = parse_text(payload.get("content", ""))
        if not chat_id or not q:
            continue
        logging.info(f"收到 chat={chat_id}: {q[:50]}")
        try:
            handle(env, q, chat_id)
        except Exception as e:
            logging.error(f"处理失败: {e}")
            try:
                push(f"⚠️ 出错了：{e}", chat_id)
            except Exception:
                pass


if __name__ == "__main__":
    main()
