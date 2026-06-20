#!/usr/bin/env python3
"""陪聊·飞书：你私聊 bot 跟军师讨论；"她说的话"由 auto.py 自动从微信读，不走这里。

简单确定的三条路（不靠 glm 路由瞎猜）：
  ① 指令(短词)：主动 / 拉 / 切 <对象>
  ② 归档：以「记: 」开头 → 默认记进她画像；「我记: 」/「意图: 」→ 记你自己；「反馈: 」→ 反馈
  ③ 其它一律 → 跟军师讨论（带全聊天+画像+你画像+当前时间）

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


def do_pull(env, name, chat_id):
    """手动拉一次她最新一条 → 走和 auto 一样的接话建议。"""
    msgs = cc.pull(env, name)
    her = next((m["text"] for m in reversed(msgs) if m["who"] == "她"), "")
    if not her:
        push(f"没读到 {name} 最近她发的消息", chat_id)
        return
    best, why, dbg = cc.best_line(env, cc.fmt(msgs, 30), cc.voice_samples(msgs), her,
                                  profile=cc.load_profile(name), me=cc.load_me(),
                                  time_ctx=cc.time_context(msgs))
    if best:
        push(f"🔔 接话 · {name}\n\n她说：{her}\n\n👉 建议回复：\n{best}\n\n（{why}）", chat_id)
    else:
        push(f"🔔 接话 · {name}\n\n她说：{her}\n\n先别接：{dbg.get('gate', '没好接的')}", chat_id)


def handle(env, text, chat_id):
    name = get_target()
    t = text.strip()

    # —— 路 ① 指令（短词，确定性，不靠 glm 猜）——
    if t.startswith("切 "):
        new = t[2:].strip()
        if new in cc.CONTACTS:
            set_target(new)
            push(f"已切到 **{new}**", chat_id)
        else:
            push(f"不认识「{new}」，已知：{'、'.join(cc.CONTACTS)}", chat_id)
        return
    if t in ("主动", "开场", "主动发", "我想发", "我要发"):
        do_active(env, name, chat_id)
        return
    if t in ("拉", "最新", "读", "看看"):
        do_pull(env, name, chat_id)
        return

    # —— 路 ② 归档（明确前缀，不靠 glm 猜）——
    if t.startswith("记:") or t.startswith("记：") or t.startswith("记 "):
        body = t.split(":", 1)[-1].split("：", 1)[-1].lstrip(" ")
        cc.append_fact(name, body)
        push(f"✅ 记进 **{name}** 的画像了", chat_id)
        return
    if t.startswith("我记:") or t.startswith("我记：") or t.startswith("意图:") or t.startswith("意图："):
        body = t.split(":", 1)[-1].split("：", 1)[-1].lstrip(" ")
        cc.append_line(name, "intents.jsonl", {"note": body})
        push("✅ 记下你的想法/意图", chat_id)
        return
    if t.startswith("反馈:") or t.startswith("反馈："):
        body = t.split(":", 1)[-1].split("：", 1)[-1].lstrip(" ")
        cc.append_line(name, "feedback.jsonl", {"note": body})
        push("✅ 记下反馈，军师会越用越准", chat_id)
        return

    # —— 路 ③ 其它一律：跟军师讨论 ——
    push(cc.consult(env, name, t), chat_id)


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
