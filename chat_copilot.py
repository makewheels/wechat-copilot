#!/usr/bin/env python3
"""陪聊 MVP：glm-5.2 + 真实数据标准 + 生成→检测器闸 + 主动时机判定 + 回测渲染。

用法（对象名见 data/config.json）：
  python3 chat_copilot.py demo <对象> [N]   # 拿真实历史回测 N 个点 + 主动时机判定 → 渲染网页
  python3 chat_copilot.py reply <对象>      # 对她最新一条，出一条能发的（过闸）
  python3 chat_copilot.py active <对象>     # 我现在想主动发：先判该不该发，再给开场

标准来自真实数据：咨询师对他的原话 + 他自己的真实口吻 + 已诊断的毛病。
质量靠：① 真数据标准 ② 硬规则代码闸 ③ 独立的"检测器"二次打分（生成易翻车、评判靠谱）。
"""
import datetime
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

import core

HERE = Path(__file__).resolve().parent


def _load_json(name, default):
    f = HERE / "data" / name
    try:
        return json.loads(f.read_text(encoding="utf-8")) if f.exists() else default
    except Exception:
        return default


def _load_text(name, default=""):
    f = HERE / "data" / name
    return f.read_text(encoding="utf-8") if f.exists() else default


# 所有可识别个人信息（wxid/真名/咨询师原话/个人画像）都在 data/（gitignore），代码只留通用逻辑
_CFG = _load_json("config.json", {})
MY_WXID = _CFG.get("my_wxid", "")          # 本机登录账号；sender==这个 = 我发的
CONTACTS = _CFG.get("contacts", {})        # 名字 → chatlab 会话 id（私密，data/config.json）
MOMO = _load_text("coach_rules.md")        # 咨询师对他的纠正（标准·心态层，data/coach_rules.md）

# 通病（泛化、不含可识别信息；具体私密画像在 data/_me.md，运行时注入）
DIAGNOSIS = ("他是理工直男、程序员，追相亲对象时的通病：心态急、把追人当任务交付、"
             "过度投入讨好(舔)、只会嘘寒问暖、爱暴露努力。")

PERSONA = f"""你是恋爱军师，帮他跟相亲女生微信聊天，目标是让她也对他有兴趣、再自然推进到见面。
{DIAGNOSIS}
你的任务：帮他"投入对等、不急、有来有回、有自己的东西可聊"，**不是**教他更卖力讨好，也**不是**教油腻套路。

{MOMO}"""

DRAFT_RULES = """草稿硬要求：
- 短：≤20 字，像微信随手打的，从不超过一行。
- 像他本人：可带"哈哈""嘿嘿""呗""呀""嘛"，可碎、可不完整、可没标点。别写完整论述句、别排比抒情、别分号。
- 别每条都关心：嘘寒问暖（在干嘛/吃了吗/累不累/多喝水）要极稀疏，绝大多数时候聊有意思的、给情绪价值或自顾自分享你的事。
- 投入对等：她回得短/冷 → 你也收着，别追问、别加倍关心。
- 严禁：炫耀（资产/收入/自己做的作品）、解释讨好、暴露在学撩妹、土味情话、爹味说教、查户口连环发问、编店名地名。
- **不许提聊天记录里没出现过的具体往事/场景/梗/人名**（别编"昨晚那个梗""上次那个分享""刚看到只柯基"）；没有真由头，宁可发句不依赖往事的轻话，或干脆别硬找。"""

# ---------- 硬规则闸（代码卡死，不靠模型自觉）----------
BANNED = ["在吗", "在么", "在不在", "美女", "么么", "宝贝", "亲爱的", "一秒心动",
          "学撩", "多喝水", "早点睡", "注意身体", "照顾好自己"]
SHOWOFF = ["资产", "收入", "工资", "台积电", "我做的", "我开发", "我写的", "点播", "网站"]


def hard_ok(text: str):
    t = text.strip()
    if len(t) > 22:
        return False, "太长(>22字)"
    if t.count("?") + t.count("？") >= 2:
        return False, "连环发问"
    for b in BANNED:
        if b in t:
            return False, f"踩禁词:{b}"
    for s in SHOWOFF:
        if s in t:
            return False, f"炫耀:{s}"
    return True, ""


# ---------- WeFlow chatlab 拉取（正确归属）----------
def pull(env, talker, retries=6):
    sid = CONTACTS.get(talker, talker)
    base = (env.get("WEFLOW_API") or "http://127.0.0.1:5031").rstrip("/")
    tok = env.get("WEFLOW_ACCESS_TOKEN", "")
    all_m = []
    off = 0
    while True:
        u = (f"{base}/api/v1/sessions/{urllib.parse.quote(sid)}/messages"
             f"?access_token={tok}&chatlab=1&limit=500&offset={off}")
        page = []
        for _ in range(retries):
            page = json.load(urllib.request.urlopen(u, timeout=30)).get("messages", [])
            if page:
                break
            time.sleep(1)
        if not page:
            break
        all_m += page
        off += len(page)
        if len(page) < 500:
            break
    out = []
    for x in all_m:
        if str(x.get("type")) != "0":   # 只要文字
            continue
        txt = (x.get("content") or x.get("text") or "").strip()
        txt = re.sub(r"<[^>]+>", " ", txt)
        txt = re.sub(r"\s+", " ", txt).strip()
        if not txt:
            continue
        ts = int(x.get("timestamp") or x.get("createTime") or 0)
        who = "我" if x.get("sender") == MY_WXID else "她"
        out.append({"ts": ts, "who": who, "text": txt})
    out.sort(key=lambda m: m["ts"])
    return out


def fmt(msgs, n=40):
    lines = []
    for m in msgs[-n:]:
        t = datetime.datetime.fromtimestamp(m["ts"]).strftime("%m-%d %H:%M") if m["ts"] else "?"
        lines.append(f"[{t}] {m['who']}: {m['text']}")
    return "\n".join(lines)


def time_context(msgs):
    """当前时间 + 距她最后一条多久——喂给军师，免得它不知道现在几点、隔了多久。"""
    now = datetime.datetime.now()
    s = f"现在 {now:%m-%d %H:%M} 周{'一二三四五六日'[now.weekday()]}"
    if msgs and msgs[-1].get("ts"):
        gap = (now.timestamp() - msgs[-1]["ts"]) / 3600
        s += f"；最后一条是「{msgs[-1]['who']}」发的，在 {gap:.0f} 小时前"
    return s


def voice_samples(msgs, k=20):
    seen, out = set(), []
    for m in msgs:
        if m["who"] != "我":
            continue
        t = m["text"]
        if 1 <= len(t) <= 18 and t not in seen:
            seen.add(t)
            out.append(t)
    # 取靠后的（更近的口吻）
    return out[-k:]


# ---------- 料：她的画像 / 你的画像 / 意图 / 反馈（全在 data/，gitignore）----------
ME_FILE = HERE / "data" / "_me.md"


def _cdir(name):
    d = HERE / "data" / name
    d.mkdir(parents=True, exist_ok=True)
    return d


def profile_path(name):
    return _cdir(name) / "profile.md"


def load_profile(name):
    p = profile_path(name)
    return p.read_text(encoding="utf-8") if p.exists() else ""


def load_me():
    return ME_FILE.read_text(encoding="utf-8") if ME_FILE.exists() else DIAGNOSIS


def append_line(name, fname, rec):
    with (_cdir(name) / fname).open("a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": int(time.time()), **rec}, ensure_ascii=False) + "\n")


def append_fact(name, text):
    p = profile_path(name)
    old = p.read_text(encoding="utf-8") if p.exists() else f"# {name} · 画像\n"
    if "## 补充（我随手加的）" not in old:
        old += "\n## 补充（我随手加的）\n"
    p.write_text(old + f"- {text}\n", encoding="utf-8")


def draft_profile(env, name):
    """从真实聊天起草她的画像（只提炼看得出的，不编）。"""
    msgs = pull(env, name)
    sys_p = """你从一段真实微信聊天里提炼这个女生的画像。只写聊天里看得出或能合理推断的；
拿不准的标 "(待确认)"，绝不编造。输出 markdown，含小节：
基本信息(年龄/籍贯/现居/职业/学历) / 兴趣爱好 / 在意的点·价值观 / 雷区禁忌 /
关系阶段(刚加微信？熟络？约过？) / 邀约记录 / 她对他的态度(投入度趋势) / 给军师的判断。"""
    user = f"女生：{name}\n聊天记录：\n{fmt(msgs, 250)}\n\n提炼她的画像（markdown）。"
    md = call_ark(env, sys_p, user, temperature=0.3, max_tokens=900)
    p = profile_path(name)
    p.write_text(f"# {name} · 画像\n> glm-5.2 从真实聊天起草，**请核对、改错的、补漏的**。\n\n{md}\n",
                 encoding="utf-8")
    return p


def consult(env, name, question):
    """讨论模式：他直接问军师 / 想分析。带全部上下文回他。"""
    msgs = pull(env, name)
    sys_p = (PERSONA + "\n\n你现在是他的私人军师，他在问你或想跟你讨论。"
             "口语、简短、直接，敢戳他的急和舔，别打官腔。要话术就按草稿硬要求（短、像他、不油）。")
    user = (f"## 当前对象：{name}\n## 她的画像\n{load_profile(name) or '(暂无)'}\n\n"
            f"## 你本人\n{load_me()}\n\n## 最近聊天\n{fmt(msgs, 30)}\n\n## 他问军师\n{question}")
    return call_ark(env, sys_p, user, temperature=0.7, max_tokens=700)


ROUTER_SYS = """判断用户对"恋爱军师"说的这句话属于哪类，只输出 JSON {"type":"...","arg":""}：
- reply：这是对方女生发来的话，他想要接话建议
- active：他想主动发起/开场（如"主动""帮我开场""我想找她"）
- fact：关于某个女生的客观事实/资料（如"她江西人""她做护士的""她不吃辣"）
- intent：他自己的意图/想法/顾虑/计划（如"我这周想约她""我怕太急"）
- consult：他在问军师问题或想讨论分析（如"你觉得她对我有意思吗""我是不是又急了"）
- feedback：他在反馈某条发出去后对方的反应（如"那条发了她秒回""我发了她没理"）
- switch：他想换聊天对象，arg 填人名
拿不准时优先 reply。只输出 JSON。"""


def route(env, text):
    raw = call_ark(env, ROUTER_SYS, text, temperature=0.0, max_tokens=120)
    d = _json(raw) or {}
    return d.get("type", "reply"), (d.get("arg") or "")


# ---------- glm-5.2 ----------
def call_ark(env, system, user, temperature=0.8, max_tokens=600):
    base = (env.get("ARK_BASE_URL") or "https://ark.cn-beijing.volces.com/api/coding/v3").rstrip("/")
    key = env["ARK_API_KEY"]
    model = env.get("ARK_MODEL", "glm-5.2")
    body = json.dumps({
        "model": model,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }).encode("utf-8")
    req = urllib.request.Request(base + "/chat/completions", data=body, headers={
        "Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read().decode("utf-8"))["choices"][0]["message"]["content"]


def _json(text):
    text = text.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if m:
        text = m.group(1).strip()
    m = re.search(r"[\[{].*[\]}]", text, re.S)
    return json.loads(m.group(0)) if m else None


# ---------- ① 生成 ----------
def gen_drafts(env, transcript, voice, her_last, profile="", me="", intent="", time_ctx=""):
    sys_p = PERSONA + "\n\n" + DRAFT_RULES + """

输出 JSON：{"drafts":[{"text":"要发的话","why":"≤15字为什么这么发"}]}，给 2-3 条，宁缺毋滥。
只输出 JSON。"""
    parts = []
    if time_ctx:
        parts.append(f"## 现在（按这个判断时机/别发不合时宜的话）\n{time_ctx}")
    parts.append("## 他的真实口吻（模仿这个味儿，别写得比这漂亮）\n" + " / ".join(voice))
    if me:
        parts.append(f"## 你本人（要像他、扬他的长，但严禁炫耀）\n{me}")
    if profile:
        parts.append(f"## 她的画像（用真事实，别编造场景/由头）\n{profile}")
    parts.append(f"## 最近聊天\n{transcript}")
    if her_last:
        parts.append(f"## 她刚发来（接这条）\n她：{her_last}")
    if intent:
        parts.append(f"## 他的意图\n{intent}")
    parts.append("给候选草稿（JSON）。")
    raw = call_ark(env, sys_p, "\n\n".join(parts))
    d = _json(raw) or {}
    return d.get("drafts", []) if isinstance(d, dict) else []


# ---------- ② 检测器闸（独立二次打分）----------
def critique(env, transcript, her_last, drafts, profile="", time_ctx=""):
    sys_p = f"""你是毒舌质检员，专挑直男舔狗气和瞎编。{DIAGNOSIS}
给每条候选打分（0=好，2=差）：
- 急(显得猴急/追)、舔(讨好/嘘寒问暖/投入过头)、油(套路/土味/油腻)
- 编造(提到下面聊天记录里**根本没出现过**的人/事/往事/场景/梗 = 编造)
再打"像他"(0=很像他随手打，2=太完整太AI)。
**编造≥1 的一律不许当 best。** 选唯一最该发的一条；若都不行，best_index 给 -1。
输出 JSON：{{"scores":[{{"急":n,"舔":n,"油":n,"编造":n,"像他":n}}],"best_index":i,"reason":"≤20字"}} 只输出 JSON。"""
    listing = "\n".join(f"{i}. {d.get('text','')}" for i, d in enumerate(drafts))
    tp = f"现在：{time_ctx}\n\n" if time_ctx else ""
    user = (f"{tp}她的画像（画像里有的事/兴趣，提到了不算编造）：\n{profile or '(无)'}\n\n"
            f"最近聊天（也拿来核对编造）：\n{transcript[-1500:]}\n\n"
            f"她刚发：{her_last or '(无，他想主动发)'}\n\n候选：\n{listing}")
    raw = call_ark(env, sys_p, user, temperature=0.2, max_tokens=400)
    return _json(raw) or {}


def best_line(env, transcript, voice, her_last, profile="", me="", intent="", time_ctx=""):
    """生成→硬闸→检测器，返回 (最佳一条, why, 调试信息)。"""
    drafts = gen_drafts(env, transcript, voice, her_last, profile, me, intent, time_ctx)
    kept, dropped = [], []
    for d in drafts:
        ok, why = hard_ok(d.get("text", ""))
        (kept if ok else dropped).append((d, why))
    if not kept:
        return None, "", {"drafts": drafts, "dropped": dropped, "gate": "全被硬闸毙"}
    cand = [d for d, _ in kept]
    c = critique(env, transcript, her_last, cand, profile, time_ctx)
    bi = c.get("best_index", 0)
    scores = c.get("scores") or []
    if bi is None or bi < 0 or bi >= len(cand):
        return None, "", {"drafts": drafts, "dropped": dropped, "critique": c, "gate": "检测器全毙"}
    if bi < len(scores) and (scores[bi].get("编造", 0) or 0) >= 1:   # 硬保险：编造的不发
        return None, "", {"drafts": drafts, "dropped": dropped, "critique": c, "gate": "最佳含编造，毙"}
    best = cand[bi]
    return best.get("text"), best.get("why", c.get("reason", "")), {
        "drafts": drafts, "dropped": dropped, "critique": c}


# ---------- 主动时机判定（纯代码，可靠）----------
def timing(msgs, now_ts=None):
    now = now_ts or int(time.time())
    if not msgs:
        return {"verdict": "无记录", "send": True, "why": "还没聊过，可以开场"}
    last = msgs[-1]
    gap_h = (now - last["ts"]) / 3600
    # 谁主动得多：统计"沉默>3h 后的第一条"是谁起的
    starts = {"我": 0, "她": 0}
    prev_ts = None
    for m in msgs:
        if prev_ts is None or (m["ts"] - prev_ts) > 3 * 3600:
            starts[m["who"]] += 1
        prev_ts = m["ts"]
    imbalanced = starts["我"] > starts["她"] * 2 and starts["我"] >= 3
    if last["who"] == "我":
        send = False
        why = f"上一条是你发的、她还没回（隔{gap_h:.0f}h）→ 别追，等她"
        if gap_h > 72 and not imbalanced:
            send = True
            why = f"你上一条她没回，但已隔{gap_h:.0f}h，可以轻起个新由头（别提'怎么不理我'）"
    else:
        if gap_h < 1:
            send = False
            why = f"她刚回（{gap_h*60:.0f}分钟前）→这是接话不是主动，别急着再追发"
        else:
            send = True
            why = f"她是最后说话的人（{gap_h:.0f}h前），轮到你接得上"
    # 投入失衡 = 硬否决：你已经够主动了，别再主动（这是上一版漏掉的）
    if imbalanced and send:
        send = False
        why = (f"⚠️你发起{starts['我']}次 vs 她{starts['她']}次，投入太失衡 → 今天别主动、沉住气等她"
               f"（哪怕她{gap_h:.0f}h前最后说话，你也已经够主动了）")
    return {"verdict": "可发" if send else "先别发", "send": send,
            "gap_h": round(gap_h, 1), "starts": starts, "why": why}


# ---------- 渲染（白底大字，看产物）----------
def esc(s):
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def render(name, tm, cases, opener, out=HERE / "data" / "demo.html"):
    def scorebox(c):
        s = (c.get("critique") or {}).get("scores") or []
        if not s:
            return ""
        b = (c.get("critique") or {}).get("best_index", "?")
        rows = "".join(
            f"<tr><td>{i}{' ✅' if i==b else ''}</td><td>急{x.get('急','-')} 舔{x.get('舔','-')} 油{x.get('油','-')} 像他{x.get('像他','-')}</td></tr>"
            for i, x in enumerate(s))
        return f"<table class=sc>{rows}</table><div class=rs>判定：{esc((c.get('critique') or {}).get('reason',''))}</div>"

    case_html = ""
    for cs in cases:
        dbg = cs["dbg"]
        drafts = "".join(f"<li>{esc(d.get('text',''))} <span class=w>— {esc(d.get('why',''))}</span></li>"
                         for d in dbg.get("drafts", []))
        dropped = "".join(f"<li class=x>{esc(d.get('text',''))} <span class=w>✗ {esc(w)}</span></li>"
                          for d, w in dbg.get("dropped", []))
        case_html += f"""
        <div class=case>
          <div class=her><b>她说：</b>{esc(cs['her'])}</div>
          <div class=mine><b>你当年发的：</b>{esc(cs['actual'])}</div>
          <div class=tool><b>工具建议：</b><span class=pick>{esc(cs['best'] or '（全被毙了，宁可不发）')}</span>
             <span class=w>{esc(cs['why'])}</span></div>
          <details><summary>它生成的全部候选 + 闸门打分</summary>
            <ul>{drafts}</ul>
            {('<div class=dh>被硬闸毙的：</div><ul>'+dropped+'</ul>') if dropped else ''}
            {scorebox(dbg)}
          </details>
        </div>"""

    op = ""
    if opener:
        op = f"""<div class=case><div class=tool><b>开场建议：</b><span class=pick>{esc(opener[0] or '（判定先别发）')}</span> <span class=w>{esc(opener[1])}</span></div></div>"""

    htmlp = f"""<!doctype html><meta charset=utf-8>
<title>陪聊 demo · {esc(name)}</title>
<style>
body{{background:#fff;color:#1a1a1a;font:18px/1.7 -apple-system,"PingFang SC",sans-serif;max-width:760px;margin:30px auto;padding:0 18px}}
h1{{font-size:26px}} h2{{font-size:21px;margin-top:30px;border-bottom:2px solid #eee;padding-bottom:6px}}
.tm{{background:#f4f8ff;border:1px solid #d6e6ff;border-radius:10px;padding:14px 18px;font-size:19px}}
.tm .v{{font-weight:700;font-size:22px;color:{'#c0392b'}}}
.case{{border:1px solid #eee;border-radius:12px;padding:16px 18px;margin:16px 0;box-shadow:0 1px 4px #0001}}
.her{{color:#444}} .mine{{color:#888;margin:6px 0}} .tool{{margin-top:8px}}
.pick{{background:#fff7d6;font-weight:700;font-size:20px;padding:2px 8px;border-radius:6px}}
.w{{color:#999;font-size:15px}}
details{{margin-top:10px;font-size:16px;color:#555}} summary{{cursor:pointer;color:#3b7}}
ul{{margin:6px 0}} li.x{{color:#bbb;text-decoration:line-through}}
.dh{{color:#c0392b;font-size:14px;margin-top:6px}}
table.sc{{font-size:14px;color:#666;margin-top:8px;border-collapse:collapse}} .sc td{{border:1px solid #eee;padding:2px 8px}}
.rs{{font-size:14px;color:#888;margin-top:4px}}
</style>
<h1>陪聊 demo · {esc(name)} <span style=font-size:15px;color:#999>glm-5.2 · 真实历史回测</span></h1>

<h2>① 我主动发？——时机判定（纯代码，不靠模型）</h2>
<div class=tm><span class=v>{esc(tm['verdict'])}</span>　<span class=w>隔 {tm.get('gap_h','?')}h · 发起 我{tm['starts']['我']}/她{tm['starts']['她']}</span><br>{esc(tm['why'])}</div>
{op}

<h2>② 接话质量——拿你真实历史回测</h2>
<p class=w>每个点：盖住你当年的回复 → 让工具生成 → 硬闸 + 检测器筛 → 给最佳一条。你可以对比"工具建议"和"你当年发的"。</p>
{case_html}
"""
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(htmlp, encoding="utf-8")
    return out


def demo(env, name, n=4):
    msgs = pull(env, name)
    print(f"拉到 {len(msgs)} 条文字消息")
    voice = voice_samples(msgs)
    tm = timing(msgs)
    # 选回测点：她说→我回 的位置，均匀采样
    pts = [i for i in range(len(msgs) - 1)
           if msgs[i]["who"] == "她" and msgs[i + 1]["who"] == "我" and len(msgs[i]["text"]) >= 4]
    if len(pts) > n:
        step = len(pts) // n
        pts = pts[::step][:n]
    cases = []
    for i in pts:
        her = msgs[i]["text"]
        # 合并他当年连发的我方消息
        j = i + 1
        actual = []
        while j < len(msgs) and msgs[j]["who"] == "我":
            actual.append(msgs[j]["text"]); j += 1
        ctx = fmt(msgs[:i + 1], 30)
        print(f"  回测点 i={i}：她说「{her[:14]}」…生成中")
        best, why, dbg = best_line(env, ctx, voice, her)
        cases.append({"her": her, "actual": " / ".join(actual), "best": best, "why": why, "dbg": dbg})
    # 主动开场（若判定可发）
    opener = None
    if tm["send"]:
        ctx = fmt(msgs, 30)
        best, why, _ = best_line(env, ctx, voice, "", intent="我想主动找她，开个场，别用嘘寒问暖")
        opener = (best, why)
    out = render(name, tm, cases, opener)
    print(f"✅ 渲染完成：{out}")
    return out


def main():
    env = core.load_env()
    if len(sys.argv) < 3:
        print(__doc__); return
    cmd, name = sys.argv[1], sys.argv[2]
    if cmd == "demo":
        n = int(sys.argv[3]) if len(sys.argv) > 3 else 4
        demo(env, name, n)
    elif cmd == "reply":
        msgs = pull(env, name)
        her = next((m["text"] for m in reversed(msgs) if m["who"] == "她"), "")
        best, why, dbg = best_line(env, fmt(msgs, 30), voice_samples(msgs), her,
                                   profile=load_profile(name), me=load_me(), time_ctx=time_context(msgs))
        print(f"她：{her}\n→ {best}　（{why}）")
        if not best:
            print("调试：", json.dumps(dbg, ensure_ascii=False)[:500])
    elif cmd == "active":
        msgs = pull(env, name)
        tm = timing(msgs)
        print(f"时机：{tm['verdict']} —— {tm['why']}")
        if tm["send"]:
            best, why, _ = best_line(env, fmt(msgs, 30), voice_samples(msgs), "",
                                     profile=load_profile(name), me=load_me(), time_ctx=time_context(msgs),
                                     intent="我想主动找她，开个场，别用嘘寒问暖，由头只能用真聊过的事")
            print(f"开场建议：{best}　（{why}）")
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
