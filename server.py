#!/usr/bin/env python3
"""军师 Demo 网页版 —— 自动从 WeFlow 读微信对话 + 你填她的画像 → 出回复建议。

启动: python3 server.py  →  浏览器 http://localhost:8765
依赖: WeFlow API 在 5031 跑着；.env 里 DASHSCOPE_API_KEY / WEFLOW_*。
"""
import datetime
import html
import http.server
import json
import socketserver

import core
import weflow

PORT = 8765


def real_sessions(env):
    out = []
    try:
        for s in weflow.sessions(env, 80):
            u = s.get("username", "")
            if not u or u.startswith("gh_") or "@" in u or "gelivable" in u:
                continue
            out.append((u, s.get("displayName") or u))
    except Exception as e:
        print("[real_sessions] 读会话失败:", e)
    return out[:60]


def run_coach(data):
    env = core.load_env()
    talker = (data.get("talker") or "").strip()
    name = (data.get("name") or talker).strip()
    profile = (data.get("profile") or "").strip()
    intent = (data.get("intent") or "").strip()
    if not talker:
        return "（请先在上面选一个对象）"
    msgs = weflow.messages(env, talker, 40)
    if not msgs:
        return "（这个人在**本机微信库**里没读到消息——多半你主要在手机上跟 TA 聊。换个最近在这台 Mac 上聊过的人试试。）"
    trans = weflow.transcript(msgs, 40)
    last = msgs[-1].get("createTime", 0)
    ld = datetime.datetime.fromtimestamp(last).strftime("%Y-%m-%d %H:%M") if last else "?"
    time_ctx = f"今天 {datetime.date.today()}。最近一条消息 {ld}。"
    user = core.build_user(name, profile, intent, trans, "", time_ctx)
    return core.call_qwen(env, core.build_system(), user)


PAGE = r"""<!doctype html>
<html lang="zh-CN"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>恋爱军师 Demo</title>
<style>
:root{--bg:#f7f8fa;--card:#fff;--line:#dfe3e8;--txt:#1a1d23;--muted:#5b6472;--acc:#10b981;--acc2:#2563eb}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--txt);font-family:-apple-system,"PingFang SC",Arial,sans-serif;font-size:17px;line-height:1.7}
.wrap{max-width:780px;margin:0 auto;padding:26px 18px 70px}
h1{font-size:28px;margin:0 0 4px}
.sub{color:var(--muted);font-size:15px;margin:0 0 20px}
label{display:block;font-size:15px;color:var(--muted);margin:18px 0 6px}
select,input,textarea{width:100%;background:#fff;color:var(--txt);border:1px solid var(--line);border-radius:10px;padding:13px 14px;font-size:17px;font-family:inherit}
select:focus,input:focus,textarea:focus{outline:none;border-color:var(--acc)}
textarea{resize:vertical;min-height:76px}
.btn{margin-top:22px;width:100%;background:var(--acc);color:#fff;border:0;border-radius:12px;padding:16px;font-size:19px;font-weight:700;cursor:pointer}
.btn:disabled{opacity:.55}
.out{margin-top:26px;background:var(--card);border:1px solid var(--line);border-radius:14px;padding:8px 20px 18px;display:none;box-shadow:0 1px 3px rgba(0,0,0,.06);font-size:17px}
.out h3{color:var(--acc2);font-size:20px;margin:18px 0 8px}
.out b{color:#000}.out code{background:#eef0f3;border:1px solid var(--line);border-radius:5px;padding:1px 6px;font-size:15px}
.out blockquote{border-left:3px solid var(--acc);margin:10px 0;padding:4px 0 4px 14px;color:#374151}
.out ul{margin:8px 0;padding-left:22px}.out hr{border:0;border-top:1px solid var(--line);margin:16px 0}
.out p{margin:8px 0}
.hint{color:var(--muted);font-size:14px;margin-top:4px}
.err{color:#dc2626}
</style></head><body><div class="wrap">
<h1>🎯 恋爱军师 · Demo</h1>
<p class="sub">选个对象 → 自动读你俩的微信对话 → 给你分析 + 草稿。挑一条、改成你的话、自己去微信发。</p>

<label>对象（选了自动读 TA 最近的微信对话）</label>
<select id="talker"><option value="">— 选一个 —</option>{{OPTIONS}}</select>

<label>对方画像 / 基本信息（可选，但越详细判断越准）</label>
<textarea id="profile" placeholder="例：28岁，老师，朋友介绍认识，性格慢热但聊得来，喜欢旅游/猫；我的目标是约她周末出来吃饭"></textarea>

<label>我的意图 / 这轮想干啥（可选）</label>
<textarea id="intent" placeholder="例：感觉她最近有点冷，想升温 / 想约她周末划船 / 不知道怎么接她那句"></textarea>

<button class="btn" id="go" onclick="go()">出主意</button>
<div class="out" id="out"></div>

<script>
function md(t){
  t=t.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
  var L=t.split('\n'),h='',inList=false;
  for(var i=0;i<L.length;i++){
    var raw=L[i],l=raw;
    l=l.replace(/\*\*(.+?)\*\*/g,'<b>$1</b>').replace(/`(.+?)`/g,'<code>$1</code>');
    if(/^#{1,6}\s/.test(l)){if(inList){h+='</ul>';inList=false;}h+='<h3>'+l.replace(/^#{1,6}\s/,'')+'</h3>';continue;}
    if(/^\s*[-*]\s/.test(raw)){if(!inList){h+='<ul>';inList=true;}h+='<li>'+l.replace(/^\s*[-*]\s/,'')+'</li>';continue;}
    if(/^&gt;\s?/.test(l)){if(inList){h+='</ul>';inList=false;}h+='<blockquote>'+l.replace(/^&gt;\s?/,'')+'</blockquote>';continue;}
    if(raw.trim()==='---'){if(inList){h+='</ul>';inList=false;}h+='<hr>';continue;}
    if(raw.trim()===''){if(inList){h+='</ul>';inList=false;}continue;}
    if(inList){h+='</ul>';inList=false;}
    h+='<p>'+l+'</p>';
  }
  if(inList)h+='</ul>';
  return h;
}
async function go(){
  var btn=document.getElementById('go'),out=document.getElementById('out'),sel=document.getElementById('talker');
  if(!sel.value){alert('先选一个对象');return;}
  var body={talker:sel.value, name:sel.options[sel.selectedIndex].text, profile:profile.value, intent:intent.value};
  btn.disabled=true;btn.textContent='军师思考中…';
  out.style.display='block';out.innerHTML='<p class="hint">读微信 + 调模型中，稍等…</p>';
  try{
    var r=await fetch('/api/coach',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    var j=await r.json();
    out.innerHTML = j.ok ? md(j.result) : '<p class="err">出错：'+j.error+'</p>';
  }catch(e){out.innerHTML='<p class="err">请求失败：'+e+'</p>';}
  btn.disabled=false;btn.textContent='出主意';
  out.scrollIntoView({behavior:'smooth'});
}
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
            opts = "".join(f'<option value="{html.escape(u)}">{html.escape(n)}</option>'
                            for u, n in real_sessions(env))
            self._send(200, PAGE.replace("{{OPTIONS}}", opts))
        else:
            self._send(404, "not found")

    def do_POST(self):
        if self.path != "/api/coach":
            self._send(404, "{}", "application/json")
            return
        n = int(self.headers.get("Content-Length", 0))
        try:
            data = json.loads(self.rfile.read(n) or "{}")
            out = run_coach(data)
            self._send(200, json.dumps({"ok": True, "result": out}), "application/json; charset=utf-8")
        except SystemExit as e:
            self._send(200, json.dumps({"ok": False, "error": str(e)}), "application/json; charset=utf-8")
        except Exception as e:
            self._send(200, json.dumps({"ok": False, "error": repr(e)}), "application/json; charset=utf-8")

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("127.0.0.1", PORT), H) as httpd:
        print(f"恋爱军师 Demo 已启动 → http://localhost:{PORT}")
        httpd.serve_forever()
