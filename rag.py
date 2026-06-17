"""全局聊天记录向量检索：建库 + 语义搜。

不知道是谁、要在所有人/群里找时用（"谁跟我提过装修""我之前纠结的那事咋样了"）。
盯一个人问不用这个——那种直接把人的记录整段喂模型即可（见 qa_server.run_qa）。

索引存 data/_index/：vectors.npy (N×1024 float32, 已 L2 归一) + chunks.jsonl (N 行元数据)。
embedding 用 DashScope text-embedding-v4。建库跑 index_all.py，查询用 search()。
"""
import datetime
import json
import time
import urllib.request
from pathlib import Path

import core
import weflow

HERE = Path(__file__).resolve().parent
INDEX_DIR = HERE / "data" / "_index"
VEC_FILE = INDEX_DIR / "vectors.npy"
META_FILE = INDEX_DIR / "chunks.jsonl"

EMB_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1/embeddings"
EMB_MODEL = "text-embedding-v4"
DIM = 1024
BATCH = 10            # 兼容模式每次最多 10 条
GAP = 30 * 60         # 相邻消息间隔 > 30 分钟就切新块
MAX_LINES = 20        # 单块最多消息条数
MAX_CHARS = 800       # 单块最多字数


def _fetch(env, talker, limit):
    """建库用：取消息。WeFlow 首次常返回空，重试几次（否则会漏抓整段会话）。"""
    ms = []
    for _ in range(4):
        ms = weflow._get(env, "/api/v1/messages", {"talker": talker, "limit": limit}).get("messages", [])
        if ms:
            break
        time.sleep(0.8)
    ms.sort(key=lambda m: m.get("sortSeq") or m.get("createTime", 0) * 1000)
    return ms


def chunk_session(msgs, name, is_group):
    """把一个人的消息切成块。每块带说话人和时间，返回 [{text, start_ts, end_ts}]。"""
    chunks = []
    cur, cur_chars, last_ts = [], 0, None

    def flush():
        nonlocal cur, cur_chars
        if cur:
            chunks.append({"text": "\n".join(l for _, l in cur),
                           "start_ts": cur[0][0], "end_ts": cur[-1][0]})
        cur, cur_chars = [], 0

    for m in msgs:
        lt = m.get("localType")
        txt = (m.get("content") or "") if lt == 1 else (weflow.TAG.get(lt) or "")
        txt = str(txt).replace("\n", " ").strip()
        if not txt:
            continue
        if len(txt) > 200:
            txt = txt[:200] + "…"
        ts = m.get("createTime", 0)
        if is_group:
            who = "我" if m.get("isSend") else "群友"
        else:
            who = "我" if m.get("isSend") else name
        t = datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M") if ts else "?"
        line = f"[{t}] {who}: {txt}"
        if cur and (ts - last_ts > GAP or len(cur) >= MAX_LINES or cur_chars + len(txt) > MAX_CHARS):
            flush()
        cur.append((ts, line))
        cur_chars += len(txt)
        last_ts = ts
    flush()
    return chunks


def embed(env, texts):
    """文本列表 → 向量列表（自动分批、失败重试）。
    优先用 DASHSCOPE_EMBED_KEY（公司 key，量大走它），没有再退回主 key。"""
    key = env.get("DASHSCOPE_EMBED_KEY") or env.get("DASHSCOPE_API_KEY")
    out = []
    for i in range(0, len(texts), BATCH):
        batch = texts[i:i + BATCH]
        body = json.dumps({"model": EMB_MODEL, "input": batch,
                           "dimensions": DIM, "encoding_format": "float"}).encode()
        for attempt in range(3):
            try:
                req = urllib.request.Request(EMB_URL, data=body, headers={
                    "Authorization": f"Bearer {key}", "Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=40) as r:
                    d = json.loads(r.read())
                out.extend(item["embedding"] for item in sorted(d["data"], key=lambda x: x["index"]))
                break
            except Exception as e:
                if attempt == 2:
                    raise
                time.sleep(1.5 * (attempt + 1))
        time.sleep(0.2)
    return out


def build(env, per_person=10000, per_group=10000, months=3, log=print):
    """拉会话 → 切块 → embedding → 存索引。
    筛选：最近 months 个月活跃、且我在窗口内发过言的人/群（冷门/单向/公众号不要）。
    入库内容：选中会话的【全部历史】（不止 3 个月那一截），每会话最多 per_* 条。"""
    import numpy as np
    INDEX_DIR.mkdir(parents=True, exist_ok=True)
    cutoff = time.time() - months * 30 * 86400
    sess = [s for s in weflow.chat_sessions(env) if s["ts"] >= cutoff]  # 最近还活跃的
    log(f"最近 {months} 个月活跃会话 {len(sess)} 个，开始拉取+切块…")
    metas, texts = [], []
    kept = 0
    for i, s in enumerate(sess, 1):
        # 第一步：拉少量判断资格（近 3 个月我发过言没），不合格不浪费全量拉取
        try:
            recent = _fetch(env, s["username"], 150)
        except Exception as e:
            log(f"  [{i}/{len(sess)}] {s['name']} 探测失败: {e}")
            continue
        window = [m for m in recent if (m.get("createTime") or 0) >= cutoff]
        if not any(m.get("isSend") for m in window):  # 近 3 个月我没发言 → 跳过（冷门/单向）
            continue
        # 第二步：合格 → 拉全量历史进库
        cap = per_group if s["is_group"] else per_person
        try:
            msgs = _fetch(env, s["username"], cap)
        except Exception as e:
            log(f"  [{i}/{len(sess)}] {s['name']} 全量拉取失败，用近期: {e}")
            msgs = recent
        kept += 1
        cks = chunk_session(msgs, s["name"], s["is_group"])  # ← 全部历史进库
        for c in cks:
            metas.append({"name": s["name"], "is_group": s["is_group"],
                          "start_ts": c["start_ts"], "end_ts": c["end_ts"], "text": c["text"]})
            texts.append(c["text"])
        log(f"  入库[{kept}] {'群' if s['is_group'] else '人'} {s['name']}: {len(msgs)} 条 → {len(cks)} 块")
    log(f"实际入库 {kept} 个会话（近 3 个月我发过消息的），共 {len(texts)} 块")
    log(f"共 {len(texts)} 块，开始 embedding（约 {len(texts)//BATCH+1} 批）…")
    vecs = []
    for i in range(0, len(texts), 200):
        vecs.extend(embed(env, texts[i:i + 200]))
        log(f"  embedding {min(i+200, len(texts))}/{len(texts)}")
    arr = np.asarray(vecs, dtype="float32")
    arr /= (np.linalg.norm(arr, axis=1, keepdims=True) + 1e-8)  # L2 归一，点积=余弦
    np.save(VEC_FILE, arr)
    with META_FILE.open("w", encoding="utf-8") as f:
        for m in metas:
            f.write(json.dumps(m, ensure_ascii=False) + "\n")
    log(f"✅ 建库完成：{arr.shape[0]} 块 → {VEC_FILE}")
    return arr.shape[0]


def search(env, query, k=10):
    """语义搜：返回 top-k 块元数据（含 score）。索引不存在返回 []。"""
    import numpy as np
    if not VEC_FILE.exists() or not META_FILE.exists():
        return []
    arr = np.load(VEC_FILE)
    metas = [json.loads(l) for l in META_FILE.read_text(encoding="utf-8").splitlines() if l.strip()]
    qv = np.asarray(embed(env, [query])[0], dtype="float32")
    qv /= (np.linalg.norm(qv) + 1e-8)
    sims = arr @ qv
    idx = np.argsort(-sims)[:k]
    res = []
    for i in idx:
        m = dict(metas[i])
        m["score"] = float(sims[i])
        res.append(m)
    return res


def index_stats():
    if not META_FILE.exists():
        return None
    n = sum(1 for _ in META_FILE.open(encoding="utf-8"))
    return {"chunks": n, "mtime": datetime.datetime.fromtimestamp(META_FILE.stat().st_mtime)}
