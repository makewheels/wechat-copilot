#!/usr/bin/env python3
"""回测：拿历史真实对话验证军师 —— 唯一能量化"好坏"的办法。

做法：截到"她说完、我该回"的点，让军师出建议，再揭开我当时真发了啥 + 她真怎么回，
对照看军师比当时的我强不强、它对"在升温/在凉"的判断对不对。

用法：
  python3 backtest.py 秦艺轩            # 成了的对象(3约3应)
  python3 backtest.py 张艺薇 --points 3 # 黄了的对象(已凉)
"""
import argparse
import datetime

import core


def find_points(msgs, k):
    """找'她发言(文字) → 我紧接着回复'的位置：她在抛球/试探，我要接。取靠后、分散的 k 个。"""
    cands = [i for i in range(len(msgs) - 1)
             if msgs[i]["who"] == "她" and msgs[i + 1]["who"] == "我"
             and msgs[i].get("type") == 1 and (msgs[i].get("text") or "").strip()]
    cands = [i for i in cands if i > len(msgs) * 0.3]  # 跳过太早期的
    if len(cands) <= k:
        return cands
    step = len(cands) / k
    return [cands[int((j + 0.5) * step)] for j in range(k)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("keyword")
    ap.add_argument("--points", type=int, default=2, help="回测几个点(默认2)")
    ap.add_argument("--n", type=int, default=25, help="每个点回看多少条上下文")
    a = ap.parse_args()

    env = core.load_env()
    name = core.ensure_ingested(a.keyword)
    msgs = core.load_messages(name)
    pts = find_points(msgs, a.points)
    if not pts:
        print("没找到合适的回测点（她发言→我回复）")
        return

    sys_p = core.build_system()
    print(f"### 回测【{name}】 共 {len(msgs)} 条，测 {len(pts)} 个点")
    for n, i in enumerate(pts, 1):
        hist = msgs[:i + 1]
        her = msgs[i]["text"]
        when = datetime.datetime.fromtimestamp(msgs[i]["ts"])
        time_ctx = f"（历史复盘）当时是 {when:%Y-%m-%d %H:%M}。"
        user = core.build_user(name, "", "(回测，无额外意图)", core.fmt_transcript(hist, a.n), her, time_ctx)
        sug = core.call_qwen(env, sys_p, user)

        actual = []
        for m in msgs[i + 1:i + 7]:
            t = datetime.datetime.fromtimestamp(m["ts"]).strftime("%m-%d %H:%M")
            actual.append(f"[{t}] {m['who']}：{m.get('text', '')}")

        print(f"\n{'=' * 62}\n## 回测点 {n} · {when:%Y-%m-%d}")
        print(f"【她最后说】{her}\n")
        print("———— 军师的建议 ————\n" + sug + "\n")
        print("———— 你当时实际怎么走的（含她的反应）————\n" + "\n".join(actual))


if __name__ == "__main__":
    main()
