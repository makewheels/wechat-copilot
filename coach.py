#!/usr/bin/env python3
"""聊天军师：给某联系人出「下一句发啥」。本机命令行版。

用法：
  python3 coach.py 王玲                              # 基于历史，给下一步该咋走
  python3 coach.py 王玲 --her "在干嘛呢"              # 她刚发了新消息，接着这条给
  python3 coach.py 王玲 --intent "想周末约她划船"      # 带上我的意图/画外音
  python3 coach.py 王玲 --her "在吗" --intent "约饭" --save   # 顺手把这轮存进 data/

数据：首次用某人会从 ~/Downloads/wechat-history/ 的 zip 自动导入 data/<名字>/。
模型：火山方舟 Agent Plan（CHAT_* 读 .env）。
"""
import argparse
import datetime

import core


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("keyword", help="联系人名字关键词")
    ap.add_argument("--her", default="", help="她刚发来的最新消息")
    ap.add_argument("--intent", default="", help="我的意图/画外音")
    ap.add_argument("--n", type=int, default=40, help="读最近多少条(默认40)")
    ap.add_argument("--save", action="store_true", help="把这轮(她的话+我的意图)存进 data/")
    a = ap.parse_args()

    env = core.load_env()
    name = core.ensure_ingested(a.keyword)
    msgs = core.load_messages(name)
    last = datetime.datetime.fromtimestamp(msgs[-1]["ts"]) if msgs else datetime.datetime.now()
    gap = (datetime.datetime.now() - last).days
    print(f"【{name}】{len(msgs)} 条 · 最后联系 {last:%Y-%m-%d}（{gap} 天前）\n" + "=" * 50)

    pf = core.contact_dir(name) / "profile.md"
    profile = pf.read_text(encoding="utf-8") if pf.exists() else ""
    time_ctx = f"今天 {datetime.date.today()}。最后一次聊天 {last:%Y-%m-%d}，已 {gap} 天没联系。"
    user = core.build_user(name, profile, a.intent, core.fmt_transcript(msgs, a.n), a.her, time_ctx)
    print(core.call_qwen(env, core.build_system(), user))

    if a.save:
        ts = int(datetime.datetime.now().timestamp())
        if a.her:
            core.append_jsonl(name, "messages.jsonl", {"ts": ts, "who": "她", "type": 1, "text": a.her})
        if a.intent:
            core.append_jsonl(name, "intents.jsonl", {"ts": ts, "note": a.intent})
        print(f"\n[已存入 data/{name}/]")


if __name__ == "__main__":
    main()
