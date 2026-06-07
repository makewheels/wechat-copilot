#!/usr/bin/env python3
"""从 WeFlow 只读 API 拉取与某联系人的最近聊天,输出可读文本 + 原始 JSON。

零第三方依赖(纯标准库),Windows / macOS 通用。
用法: python3 fetch.py "联系人备注名" --n 50
"""
import argparse
import json
import sys
from pathlib import Path

from weflow_client import WeFlowAPIError, WeFlowClient

HERE = Path(__file__).resolve().parent


def load_env(path):
    env = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def resolve_talker(client, keyword):
    """在会话和联系人里按关键词找出 talker 的 username/wxid 和显示名。"""
    for getter in (client.get_sessions, client.get_contacts):
        try:
            items = getter(keyword=keyword, limit=50)
        except WeFlowAPIError:
            continue
        for it in items:
            name = it.get("nickName") or it.get("remark") or it.get("name") or ""
            uname = (it.get("userName") or it.get("username") or it.get("wxid")
                     or it.get("talker") or it.get("id") or "")
            if uname and (keyword in name or keyword in uname):
                return uname, (name or uname)
    return None, None


def fmt_msg(m):
    # NOTE: 字段名待接上真实 WeFlow 返回后校准(见下方 main 里的原始 JSON dump)
    ts = m.get("createTime") or m.get("time") or m.get("timestamp") or ""
    is_self = m.get("isSelf") or m.get("isSender") or m.get("isSend") or 0
    who = "我" if is_self else "对方"
    text = m.get("content") or m.get("text") or m.get("strContent") or ""
    return f"[{ts}] {who}: {text}"


def main():
    ap = argparse.ArgumentParser(description="拉取与某联系人的最近微信聊天")
    ap.add_argument("keyword", help="联系人备注名/昵称关键词")
    ap.add_argument("--n", type=int, default=50, help="最近条数(默认 50)")
    ap.add_argument("--api", help="WeFlow API 地址(默认读 .env)")
    ap.add_argument("--token", help="access_token(默认读 .env)")
    args = ap.parse_args()

    env = load_env(HERE / ".env")
    api = args.api or env.get("WEFLOW_API") or "http://127.0.0.1:5031"
    token = args.token or env.get("WEFLOW_ACCESS_TOKEN")

    client = WeFlowClient(api, access_token=token)
    if not client.health_check():
        sys.exit(f"连不上 WeFlow API({api})。请先在 WeFlow 里 设置→API 服务→启动服务。")

    uname, disp = resolve_talker(client, args.keyword)
    if not uname:
        sys.exit(f"没找到联系人「{args.keyword}」。换个备注名/昵称关键词试试。")

    msgs, _ = client.get_messages(uname, limit=args.n)
    out_dir = HERE / "output"
    out_dir.mkdir(exist_ok=True)
    (out_dir / f"{disp}.json").write_text(
        json.dumps(msgs, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"# 与「{disp}」最近 {len(msgs)} 条")
    for m in msgs:
        print(fmt_msg(m))
    print(f"\n原始数据已存: output/{disp}.json", file=sys.stderr)


if __name__ == "__main__":
    main()
