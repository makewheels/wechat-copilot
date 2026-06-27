#!/usr/bin/env python3
"""聊天记录问答网页 —— 选一个微信联系人，用大白话问你跟他聊过的事，
模型读他的记录回答你，并标出处（哪天说的）。任何联系人都能问，不限相亲对象。

不是 RAG：单人记录直接整段喂模型通读，比向量检索更准（不会漏掉"后来解决了"那段）。

启动: python3 qa_server.py  →  浏览器 http://localhost:8766
依赖: WeFlow API 在 5031 跑着；.env 里 CHAT_API_KEY / WEFLOW_*。
"""
import datetime
import html
import http.server
import json
import socketserver

import core
import weflow
import rag

PORT = 8766
N_MSGS = 600  # 喂给模型的最近消息条数

SEARCH_SYSTEM = """你根据我提供的、从我全部微信聊天里检索出来的片段，回答我的问题。规则：
- 只用片段里的信息回答，别编。没找到就直说「记录里没找到」。
- 每个片段都标了【和谁、哪天】。回答时说清是跟谁聊的、大概什么时候。
- 多个相关的就归纳一下。简洁，口语，直接给结论。"""

QA_SYSTEM = """你根据我和某人的微信聊天记录，回答我的问题。规则：
- 只用记录里的信息回答，**别编**。记录里没有就直说「记录里没提到」。
- 「那事解决了吗」这类，要顺着时间看：先找事情提出，再看后面有没有跟进/解决，给出结论。
- 「他给了我什么建议」这类，把相关的话都捞出来，归纳成几条。
- 答完用一句话标出处，格式：（依据 X月X日的对话）。
- 简洁，口语，直接给结论，别复述记录。"""


def sessions_data(env):
    """返回 (个人JSON, 群组JSON)，各按最近时间排，喂给前端切换用。"""
    persons, groups = [], []
    try:
        for s in weflow.chat_sessions(env):
            (groups if s["is_group"] else persons).append({"v": s["username"], "n": s["name"]})
    except Exception as e:
        print("[sessions_data] 读会话失败:", e)
    def js(x):
        return json.dumps(x, ensure_ascii=False).replace("<", "\\u003c")
    return js(persons), js(groups)


def transcript_dated(msgs, name):
    """带完整日期+真实姓名的逐字记录（问答要标'哪天说的'）。"""
    lines = []
    for m in msgs:
        ts = m.get("createTime", 0)
        t = datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M") if ts else "?"
        who = "我" if m.get("isSend") else name
        lt = m.get("localType")
        txt = (m.get("content") or "") if lt == 1 else (weflow.TAG.get(lt) or "[其他]")
        txt = str(txt).replace("\n", " ")
        if len(txt) > 200:
            txt = txt[:200] + "…"
        if txt:
            lines.append(f"[{t}] {who}: {txt}")
    return "\n".join(lines)


def run_qa(data):
    env = core.load_env()
    talker = (data.get("talker") or "").strip()
    name = (data.get("name") or talker).strip()
    question = (data.get("question") or "").strip()
    if not talker:
        return {"answer": "（请先在上面选一个人）"}
    if not question:
        return {"answer": "（请输入你想问的问题）"}
    msgs = weflow.messages(env, talker, N_MSGS)
    if not msgs:
        return {"answer": "（这个人在**本机微信库**里没读到消息——多半你主要在手机上跟 TA 聊。换个最近在这台 Mac 上聊过的人试试。）"}
    trans = transcript_dated(msgs, name)
    user = f"# 和 {name} 的聊天记录\n{trans}\n\n# 我的问题\n{question}"
    answer = core.call_qwen(env, QA_SYSTEM, user, temperature=0.3)
    return {"answer": answer, "system": QA_SYSTEM, "user": user, "n_msgs": len(msgs)}


def run_search(data):
    """全局搜索：向量检索全部记录 → 喂命中片段给模型作答（带出处）。"""
    env = core.load_env()
    question = (data.get("question") or "").strip()
    if not question:
        return {"answer": "（请输入要搜的问题）"}
    hits = rag.search(env, question, k=12)
    if not hits:
        return {"answer": "（向量库还没建，或没搜到。先跑 `uv run --with numpy python index_all.py`）"}
    blocks = []
    for h in hits:
        d = datetime.datetime.fromtimestamp(h["start_ts"]).strftime("%Y-%m-%d") if h.get("start_ts") else "?"
        who = ("群:" if h["is_group"] else "") + h["name"]
        blocks.append(f"【和 {who}，{d}】\n{h['text']}")
    user = "# 检索到的聊天片段\n" + "\n\n".join(blocks) + f"\n\n# 我的问题\n{question}"
    answer = core.call_qwen(env, SEARCH_SYSTEM, user, temperature=0.3)
    return {"answer": answer, "system": SEARCH_SYSTEM, "user": user, "n_msgs": len(hits)}


PAGE = r"""<!doctype html>
<html lang="zh-CN"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>聊天记录问答</title>
<style>
:root{--bg:#f7f8fa;--card:#fff;--line:#dfe3e8;--txt:#1a1d23;--muted:#5b6472;--acc:#2563eb}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--txt);font-family:-apple-system,"PingFang SC",Arial,sans-serif;font-size:17px;line-height:1.7}
.wrap{max-width:780px;margin:0 auto;padding:26px 18px 70px}
h1{font-size:28px;margin:0 0 4px}
.sub{color:var(--muted);font-size:15px;margin:0 0 20px}
label{display:block;font-size:15px;color:var(--muted);margin:18px 0 6px}
select,input,textarea{width:100%;background:#fff;color:var(--txt);border:1px solid var(--line);border-radius:10px;padding:13px 14px;font-size:17px;font-family:inherit}
select:focus,input:focus,textarea:focus{outline:none;border-color:var(--acc)}
textarea{resize:vertical;min-height:70px}
.btn{margin-top:22px;width:100%;background:var(--acc);color:#fff;border:0;border-radius:12px;padding:16px;font-size:19px;font-weight:700;cursor:pointer}
.btn:disabled{opacity:.55}
.out{margin-top:26px;background:var(--card);border:1px solid var(--line);border-radius:14px;padding:8px 20px 18px;display:none;box-shadow:0 1px 3px rgba(0,0,0,.06);font-size:17px}
.out h3{color:var(--acc);font-size:20px;margin:18px 0 8px}
.out b{color:#000}.out code{background:#eef0f3;border:1px solid var(--line);border-radius:5px;padding:1px 6px;font-size:15px}
.out ul{margin:8px 0;padding-left:22px}.out hr{border:0;border-top:1px solid var(--line);margin:16px 0}
.out p{margin:8px 0}
.hint{color:var(--muted);font-size:14px;margin-top:4px}
.err{color:#dc2626}
.eg{color:var(--muted);font-size:14px;margin-top:8px;line-height:1.9}
.eg span{background:#eef0f3;border-radius:6px;padding:2px 8px;margin-right:6px;cursor:pointer}
.seg{display:flex;gap:8px;margin:6px 0 8px}
.seg button{flex:1;padding:11px;border:1px solid var(--line);background:#fff;border-radius:10px;font-size:16px;cursor:pointer;color:var(--muted)}
.seg button.on{background:var(--acc);color:#fff;border-color:var(--acc)}
.dbg{margin-top:16px;border-top:1px dashed var(--line);padding-top:10px}
.dbg summary{cursor:pointer;color:var(--muted);font-size:14px}
.dbglabel{font-size:13px;color:var(--muted);margin:10px 0 4px;font-weight:700}
.dbg pre{background:#0f172a;color:#e2e8f0;border-radius:8px;padding:12px;font-size:13px;line-height:1.55;white-space:pre-wrap;word-break:break-word;max-height:340px;overflow:auto;margin:0}
</style></head><body><div class="wrap">
<h1>💬 聊天记录问答</h1>
<p class="sub">问某个人：读 TA 的记录答；全局搜索：不知道是谁，在所有人/群里搜。都标哪天说的。</p>

<div class="seg" style="margin-bottom:14px">
  <button type="button" class="on" id="modePerson" onclick="setMode('person')">💬 问某个人</button>
  <button type="button" id="modeGlobal" onclick="setMode('global')">🔍 全局搜索</button>
</div>

<div id="personBox">
<label>问谁（按最近聊天排序）</label>
<div class="seg"><button type="button" class="on" id="tabP" onclick="sw('p')">👤 个人</button><button type="button" id="tabG" onclick="sw('g')">👥 群组</button></div>
<select id="talker"><option value="">— 选一个 —</option></select>
</div>

<label>你想问什么</label>
<textarea id="question" placeholder="例：跟他那事解决了吗？ / 这个老师给了我什么建议？ / 我俩上次约的是哪天？"></textarea>
<div class="eg">
  试试：<span onclick="fill(this)">他给了我什么建议？</span>
  <span onclick="fill(this)">那事解决了吗？</span>
  <span onclick="fill(this)">我俩聊过哪些事？</span>
</div>

<button class="btn" id="go" onclick="go()">问</button>
<div class="out" id="out"></div>

<script>
var PERSONS={{PERSONS}}, GROUPS={{GROUPS}};
function fillSel(list){
  var s=document.getElementById('talker');
  s.innerHTML='<option value="">— 选一个（共'+list.length+'）—</option>';
  for(var i=0;i<list.length;i++){var o=document.createElement('option');o.value=list[i].v;o.textContent=list[i].n;s.appendChild(o);}
}
function sw(k){
  document.getElementById('tabP').className=k==='p'?'on':'';
  document.getElementById('tabG').className=k==='g'?'on':'';
  fillSel(k==='p'?PERSONS:GROUPS);
}
function fill(el){document.getElementById('question').value=el.textContent;}
var MODE='person';
function setMode(m){
  MODE=m;
  document.getElementById('modePerson').className=m==='person'?'on':'';
  document.getElementById('modeGlobal').className=m==='global'?'on':'';
  document.getElementById('personBox').style.display=m==='person'?'block':'none';
  document.getElementById('question').placeholder=m==='person'
    ?'例：跟他那事解决了吗？ / 这个老师给了我什么建议？'
    :'例：谁给我推荐过装修？ / 谁吐槽过我穿衣？ / 我跟谁聊过买房？（在所有人和群里搜）';
}
function md(t){
  t=t.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
  var L=t.split('\n'),h='',inList=false;
  for(var i=0;i<L.length;i++){
    var raw=L[i],l=raw;
    l=l.replace(/\*\*(.+?)\*\*/g,'<b>$1</b>').replace(/`(.+?)`/g,'<code>$1</code>');
    if(/^#{1,6}\s/.test(l)){if(inList){h+='</ul>';inList=false;}h+='<h3>'+l.replace(/^#{1,6}\s/,'')+'</h3>';continue;}
    if(/^\s*[-*]\s/.test(raw)){if(!inList){h+='<ul>';inList=true;}h+='<li>'+l.replace(/^\s*[-*]\s/,'')+'</li>';continue;}
    if(raw.trim()==='---'){if(inList){h+='</ul>';inList=false;}h+='<hr>';continue;}
    if(raw.trim()===''){if(inList){h+='</ul>';inList=false;}continue;}
    if(inList){h+='</ul>';inList=false;}
    h+='<p>'+l+'</p>';
  }
  if(inList)h+='</ul>';
  return h;
}
async function go(){
  var btn=document.getElementById('go'),out=document.getElementById('out');
  var q=document.getElementById('question').value.trim();
  if(!q){alert('先输入问题');return;}
  var url, body;
  if(MODE==='global'){ url='/api/search'; body={question:q}; }
  else{
    var sel=document.getElementById('talker');
    if(!sel.value){alert('先选一个人');return;}
    url='/api/ask'; body={talker:sel.value, name:sel.options[sel.selectedIndex].text, question:q};
  }
  btn.disabled=true;btn.textContent=MODE==='global'?'全局检索中…':'读记录中…';
  out.style.display='block';out.innerHTML='<p class="hint">'+(MODE==='global'?'向量检索全部记录':'读微信记录')+' + 调模型中，稍等…</p>';
  try{
    var r=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    var j=await r.json();
    if(!j.ok){out.innerHTML='<p class="err">出错：'+j.error+'</p>';}
    else{
      var h=md(j.answer);
      if(j.user){
        var esc=function(s){return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');};
        h+='<details class="dbg"><summary>🔍 看发给模型的原文（'+(j.n_msgs||0)+' 条记录 · 纯文本/Markdown，非 JSON）</summary>'
          +'<div class="dbglabel">① System（角色与规则）</div><pre>'+esc(j.system||'')+'</pre>'
          +'<div class="dbglabel">② User（聊天记录 + 你的问题）</div><pre>'+esc(j.user)+'</pre></details>';
      }
      out.innerHTML=h;
    }
  }catch(e){out.innerHTML='<p class="err">请求失败：'+e+'</p>';}
  btn.disabled=false;btn.textContent='问';
  out.scrollIntoView({behavior:'smooth'});
}
sw('p');
</script>
</div></body></html>"""


class H(http.server.BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="text/html; charset=utf-8"):
        b = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            env = core.load_env()
            p, g = sessions_data(env)
            self._send(200, PAGE.replace("{{PERSONS}}", p).replace("{{GROUPS}}", g))
        else:
            self._send(404, "not found")

    def do_POST(self):
        if self.path not in ("/api/ask", "/api/search"):
            self._send(404, "{}", "application/json")
            return
        n = int(self.headers.get("Content-Length", 0))
        try:
            data = json.loads(self.rfile.read(n) or "{}")
            out = run_search(data) if self.path == "/api/search" else run_qa(data)
            out["ok"] = True
            self._send(200, json.dumps(out, ensure_ascii=False), "application/json; charset=utf-8")
        except SystemExit as e:
            self._send(200, json.dumps({"ok": False, "error": str(e)}), "application/json; charset=utf-8")
        except Exception as e:
            self._send(200, json.dumps({"ok": False, "error": repr(e)}), "application/json; charset=utf-8")

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("127.0.0.1", PORT), H) as httpd:
        print(f"聊天记录问答已启动 → http://localhost:{PORT}")
        httpd.serve_forever()
