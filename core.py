"""共享内核：取数据、组装上下文、调模型。被 coach.py / backtest.py 复用。

设计原则：薄、确定性、无框架。本质就是"把上下文塞进一次模型调用"。
"""
import datetime
import json
import sys
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
HISTORY_DIR = Path.home() / "Downloads" / "wechat-history"
DATA = HERE / "data"
BOOK = HERE / "book" / "一秒心动_精要.md"
DASHSCOPE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"

TYPE_TAG = {3: "[图片]", 34: "[语音]", 43: "[视频]", 47: "[表情]", 49: "[链接/小程序]",
            42: "[名片]", 48: "[位置]", 50: "[通话]", 10000: "[系统]"}


def load_env():
    env = {}
    f = HERE / ".env"
    if f.exists():
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def _find_zip(keyword):
    hits = sorted(HISTORY_DIR.glob(f"*{keyword}*.zip"))
    return hits[0] if hits else None


def ensure_ingested(keyword):
    """确保 data/<名字>/messages.jsonl 存在（没有就从 zip 导一次），返回规范名字。"""
    if DATA.exists():
        for d in sorted(DATA.iterdir()):
            if d.is_dir() and keyword in d.name and (d / "messages.jsonl").exists():
                return d.name
    z = _find_zip(keyword)
    if not z:
        sys.exit(f"没找到联系人「{keyword}」(zip 应在 {HISTORY_DIR})")
    name = z.name.split("_")[0]
    with zipfile.ZipFile(z) as zf:
        mn = next((n for n in zf.namelist() if n.endswith("messages.json")), None)
        if not mn:
            sys.exit(f"{z.name} 里没有 messages.json")
        raw = json.loads(zf.read(mn).decode("utf-8"))
    raw = [m for m in raw if isinstance(m, dict)]
    raw.sort(key=lambda m: m.get("sortSeq") or m.get("createTime", 0) * 1000)
    out = DATA / name
    out.mkdir(parents=True, exist_ok=True)
    with (out / "messages.jsonl").open("w", encoding="utf-8") as fh:
        for m in raw:
            t = m.get("localType")
            rec = {"ts": m.get("createTime", 0),
                   "who": "我" if m.get("isSend") == 1 else "她",
                   "type": t,
                   "text": (m.get("content") or "") if t == 1 else TYPE_TAG.get(t, "[其他]")}
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return name


def contact_dir(name):
    return DATA / name


def load_messages(name):
    f = DATA / name / "messages.jsonl"
    return [json.loads(x) for x in f.read_text(encoding="utf-8").splitlines() if x.strip()]


def append_jsonl(name, fname, rec):
    d = DATA / name
    d.mkdir(parents=True, exist_ok=True)
    with (d / fname).open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


def fmt_transcript(msgs, n=40):
    lines = []
    for m in msgs[-n:]:
        t = datetime.datetime.fromtimestamp(m.get("ts", 0)).strftime("%m-%d %H:%M")
        txt = (m.get("text") or "").replace("\n", " ")
        if len(txt) > 120:
            txt = txt[:120] + "…"
        if txt:
            lines.append(f"[{t}] {m['who']}: {txt}")
    return "\n".join(lines)


def build_system(single=False):
    book = BOOK.read_text(encoding="utf-8") if BOOK.exists() else ""
    persona = f"""你是恋爱军师，帮一个理工直男跟相亲女生聊微信，目标是让她也对他产生兴趣、再自然推进到见面。
**核心立场：他最大的毛病是过度投入、一味讨好关心（像舔狗/供养者），结果对方越来越淡。你的任务是帮他"投入对等、有趣有价值"，不是教他更卖力地讨好，也不是教套路。**
他是程序员，说话直接、短、偶尔带"哈哈""嘿嘿"，不油腻不写小作文。

下面是《一秒心动》打法（句式参考，别照抄）：
————
{book}
————"""

    draft_rules = """草稿硬要求（最重要）：
- **短**：10-20 字，跟微信随手打的一样。他的回复从不超过一行。
- **像人**：可以带"哈哈""嘿嘿"，可以碎、不完整句、没标点、有语气词（呢 呗 啊 嘛）。别写完整论述句、别排比、别抒情、别用分号。
- 参考他口吻：'啊你起好早' '嘿嘿' '周末啦周末啦' '还没下班' '那你对啥感兴趣呀' 'good'
- **别每条都关心**：关心要稀疏、真诚，偶尔一句就够（她说累/加班可以关心一下身体），但**绝大多数时候聊有意思的、给情绪价值**（调侃、共鸣、有来有回），而不是反复嘘寒问暖——反复关心显得舔、掉价。
- **投入对等**：先看她的投入度——回复短/慢/不主动＝她没投入，这时**你也要收着，别热脸贴冷屁股、别追问、别加倍关心**；她热你再热，永远别做投入明显更多的那个。
- **别掉价**：严禁炫耀（资产、收入、成就、自己做的作品/视频），严禁解释讨好，严禁暴露"我在学怎么聊天/撩妹"。价值是聊出来的，不是甩出来的。
- **不能让对方发现是AI**：不完美比完美重要。偶尔回得随意一点没关系。
- 严禁：土味情话、爹味说教、肉麻、长篇、查户口式连环发问、编具体店名地名。
- **该约就约**：他的老毛病是从不开口、无限文字空转。所以只要聊得有来有回、她有回应，就**主动但轻量地试探约**（给低压力的二选一，如"周末一起吃个饭还是逛逛？"）。她明显冷淡时不硬约，但别一直拖着不推进。"""

    if single:
        return persona + f"""

输出：**只给一条**现在该发的话。直接就是要发出去的内容本身，纯文字。**不要分析、不要解释、不要前缀、不要序号、不要标签、不要引号**——整个回复就是那一句话。

{draft_rules}"""

    return persona + f"""

输出格式：两部分，中间用**单独一行 `---`** 隔开。

【第一部分 · 分析】（会渲染 Markdown，多换行、每点之间空一行、关键处加粗，别挤成一坨）：
**她的兴趣信号**：高 / 中 / 低（依据：回复长短、快慢、是否主动起话题、是否反问你）

**她在想啥**：……

**她感兴趣的点**：……

**投入建议**：信号低→收着、别追、别加倍关心；信号中/高→可多给一点、可试探约

**她可能怎么回**：热→…；冷→…

**别踩**：……

---

【第二部分 · 草稿】（要发的话，**每条单独占一行，纯文字，无序号、无标签、无引号、无解释**。给 1～2 条就行，宁可 1 条精的）：
草稿一
草稿二

{draft_rules}"""



def build_user(name, profile, intent, transcript, her_msg, time_ctx, single=False):
    parts = [f"# 对象：{name}", f"## 时间\n{time_ctx}"]
    parts.append(f"## 她的档案\n{profile or '(暂无，从对话里推断她大概是什么人)'}")
    parts.append(f"## 我这轮的意图 / 画外音\n{intent or '(无，默认目标：推进关系、争取约出来见面)'}")
    parts.append(f"## 最近的聊天记录\n{transcript}")
    if her_msg:
        parts.append(f"## 她刚发来的最新一条（请接着这条给我下一句）\n她：{her_msg}")
    if single:
        parts.append("现在直接给我**一条**该发的话（就这一句，纯文字，别的都不要）。")
    else:
        parts.append("现在给我：① 她在想啥　② 现在啥阶段/该干啥　③ 2-3 条候选回复(带策略+理由)　④ 风险提醒。")
    return "\n\n".join(parts)


def build_chat_user(name, profile, transcript, question):
    """双向问答：用户在飞书里直接问军师。"""
    return (
        f"# 当前在聊的对象：{name}\n"
        f"## 她的档案\n{profile or '(暂无)'}\n\n"
        f"## 我和她最近的聊天记录\n{transcript}\n\n"
        f"## 我（理工直男本人）现在问军师\n{question}\n\n"
        "用军师身份回答我的问题，口语、简洁、直接。"
        "如果我让你给话术/草稿，就按草稿硬要求给（短、像人、不油、每条单独一行）。"
    )


def call_qwen(env, system, user, temperature=0.75):
    key = env.get("DASHSCOPE_API_KEY")
    if not key:
        raise RuntimeError("缺 DASHSCOPE_API_KEY(放 .env)")
    model = env.get("QWEN_MODEL", "qwen3-max")
    body = json.dumps({
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "temperature": temperature,
    }).encode("utf-8")
    req = urllib.request.Request(DASHSCOPE_URL, data=body, headers={
        "Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    last_err = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                return json.loads(r.read().decode("utf-8"))["choices"][0]["message"]["content"]
        except urllib.error.HTTPError as e:
            # 4xx/5xx 是明确的服务端拒绝，重试无意义，直接抛（不再 sys.exit 杀进程）
            raise RuntimeError(f"模型调用失败 HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:400]}")
        except Exception as e:
            # URLError / 超时等瞬时网络抖动：退避重试
            last_err = e
            if attempt < 2:
                time.sleep(2 ** attempt)
    raise last_err
