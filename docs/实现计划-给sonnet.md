# 实现计划（给执行模型照做）

> 本文是**可直接照做的实现规格**。两个功能：① 恋爱军师记忆 ② 聊天记录问答。
> 架构判断已拍板，**不要引入任何框架（langgraph/mem0/langchain）**，照本文写普通 Python。
> 高层背景见同目录 `记忆系统计划.md`。

## 0. 项目约定（必读，照此风格）

- 运行：`cd ~/workspace/tools/wechat-copilot && uv run python xxx.py`。要临时依赖用 `uv run --with numpy python ...`。**不新增 requirements，能用标准库就用标准库。**
- 读配置：一律 `core.load_env()`，返回 dict，键有 `DASHSCOPE_API_KEY` `QWEN_MODEL`(默认 qwen3-max) `WEFLOW_API` `WEFLOW_ACCESS_TOKEN`。
- 读微信消息：`weflow.messages(env, wxid, limit)` → list[dict]，按时间升序。字段：`createTime`(秒) `isSend`(1=我) `localType`(1=文字) `content` `localId`。
- 调模型：`core.call_qwen(env, system, user, temperature=0.75)` → str。
- 联系人：`data/contacts.json`，`{wxid: {"name":..., "profile":...}}`。第一个真实目标 **董丽琼 = `wxid_493f2glh530d22`**。
- 代码风格：薄、无框架、注释少（只在不显然处写一句中文）。不写多余文档。
- 每完成一个文件，跑一次它的自测命令，确认无报错再继续。

---

# 功能一：恋爱军师记忆

## 1.1 数据格式

每个联系人目录 `data/<名字>/` 下新增 `memory.md`，固定四块、标题一字不差（解析靠标题）：

```markdown
## 她是谁
<慢变事实：工作/家乡/家人/性格/喜好>

## 关系状态
阶段：<破冰/日常/暧昧/约线下/确定> | 温度：<在升温/平/转凉> | 上次进展：<一句>

## 可回扣的细节
- <她说过的具体事，每条一行，给草稿做 callback>

## 雷区
- <试过失败的招 / 她明显不爱听的>
```

## 1.2 新文件 `memory.py`

纯读写 + 拼装，不调模型。函数签名：

```python
from pathlib import Path
HERE = Path(__file__).resolve().parent
DATA = HERE / "data"

SECTIONS = ["她是谁", "关系状态", "可回扣的细节", "雷区"]

def mem_path(name: str) -> Path:
    return DATA / name / "memory.md"

def load_memory(name: str) -> str:
    """返回整篇 memory.md 文本；没有返回 ''。"""

def save_memory(name: str, text: str) -> None:
    """整篇写入（覆盖）。先用 validate_memory 校验，不合格抛 ValueError。"""

def validate_memory(text: str) -> bool:
    """四个 '## 标题' 必须都在，否则 False（防模型输出残缺把记忆写坏）。"""

def init_from_profile(name: str, profile: str) -> str:
    """没有 memory.md 时，用 contacts.json 里的 profile 生成初版骨架文本（不调模型，
    profile 整段塞进『她是谁』，其余块留空占位）。返回文本，不落盘。"""
```

自测：`uv run python -c "import memory; print(memory.validate_memory(open('docs/实现计划-给sonnet.md').read()))"` 应为 False（因为没有那四个标题）。

## 1.3 新文件 `update_memory.py`（产生/更新记忆）

CLI：`uv run python update_memory.py 董丽琼 [--n 200]`
流程：
1. `env = core.load_env()`；从 `contacts.json` 按名字找 wxid。
2. `msgs = weflow.messages(env, wxid, n)`；`trans = weflow.transcript(msgs, n)`。
3. `old = memory.load_memory(name)`；空则 `old = memory.init_from_profile(name, profile)`。
4. 调模型**整篇重写**（prompt 见下），输出新 memory.md 全文。
5. `memory.validate_memory(新文本)`：通过则 `memory.save_memory`；不通过则打印警告、不覆盖旧的。
6. 打印 diff 提示（旧/新各几行即可），让用户能瞄一眼。

**更新用的 system prompt（原文，照抄）：**
```
你在维护一份「相亲对象档案」。我给你她的旧档案和最近的真实聊天记录，
你输出更新后的**完整**档案。规则：
- 严格保留这四个二级标题，顺序不变：## 她是谁 / ## 关系状态 / ## 可回扣的细节 / ## 雷区
- 「她是谁」：把聊天里新暴露的稳定事实补进去；不确定的别写。
- 「关系状态」：根据最近对话重新判断阶段、温度趋势、上次进展，覆盖旧的。
- 「可回扣的细节」：提炼她提过的具体的事（喜好/烦恼/计划/经历），每条一行，
  这些是之后给她回消息时「显得我记得」的料。最多 12 条，旧的过时就删。
- 「雷区」：发现哪类话题/玩笑她明显冷淡，记一条。
- 只输出 Markdown 档案本身，不要解释、不要前后缀。短，别凑字。
```
user 内容：`f"# 对象：{name}\n\n## 旧档案\n{old}\n\n## 最近聊天\n{trans}"`，temperature 用 0.4（要稳）。

自测：`uv run python update_memory.py 董丽琼 --n 200`，看 `data/董丽琼/memory.md` 生成且四块齐全。

## 1.4 改 `core.py`（让出草稿用上记忆）

`build_system()`：把当前**全文注入《一秒心动》**改成精简版——不再把 `BOOK` 整本塞进 persona。改法：
- 删掉 persona 里 `{book}` 整段，换成下面这段「精简打法」常量（直接内联）：
```
打法要点（参考，不照抄）：幽默接话别查户口；接话要延伸不终结；夸具体不夸"好看"；
适度推拉制造"够得到的难度"；关心要落到行动、先共情站队再说事；约线下要顺着她的喜好包装。
```
- 在 `draft_rules` 末尾**新增两条规则**（这是治"乱开玩笑/没温度"的核心）：
```
- **对齐她的频道**：先看她最近怎么说话——她正经你就正经，她皮你才皮。别不管她什么状态都强行抖机灵/推拉。认真的问题先好好回答，幽默是调味不是主菜。
- **风险意识**：无聊但安全的回复代价小，尴尬冒犯的回复可能直接结束。没明确信号别开过火的玩笑、别上肢体暗示。拿不准就走稳的。
```

`build_user()`：`profile` 参数改为优先用记忆——调用方传 `memory.load_memory(name) or profile`。即在 `coach.py / server.py / watch.py / feishu_chat.py` 里，把原来传 `profile`/`info.get("profile")` 的地方，改成 `memory.load_memory(name) or <原profile>`。**只改取值来源，build_user 签名不动。**

## 1.5 跑通董丽琼 + 验收

```
uv run python update_memory.py 董丽琼 --n 300      # 生成记忆
uv run python coach.py 董丽琼 --her "今天又加班到现在"  # 出草稿，应能引用记忆里的细节
uv run python backtest.py 董丽琼 --points 3          # 改前改后各跑一次，肉眼对照草稿
```
验收：草稿能用上 memory.md 里的具体细节（如"加班/旅游"），且不再对一句普通的话强行抖机灵。

---

# 功能二：聊天记录问答（网页，任意联系人，单人）

> 例：「跟房东那事解决了吗」「李老师上次给我啥建议」。
> **已拍板的形态**：网页选一个人 → 问 → 把**那个人**的聊天记录喂给模型答（带出处）。
> **不用向量、不建索引、不装 numpy。** 因为是单人，模型直接通读他的记录即可，比向量检索更准（不会漏掉"后来解决了"那段）。任何联系人都能问，不限相亲对象。

## 2.1 取数据

- 复用 `weflow.sessions(env)` 列出所有最近聊过的人（参考 `server.py:real_sessions` 的过滤：去掉公众号 `gh_`、群 `@chatroom` 等）。
- 选定某人后 `msgs = weflow.messages(env, wxid, 600)` 拉他最近 ~600 条。
- 渲染成带时间+说话人的文本：复用/仿照 `weflow.transcript`，但**每行带日期**（`%Y-%m-%d %H:%M`），因为答案要标"哪天说的"。把对方名字用真实姓名而非"她"。

> 600 条覆盖不到很老的事时，再说加"全量+按关键词粗筛"。v1 先 600。

## 2.2 一次模型调用出答案

`core.call_qwen(env, system, user, temperature=0.3)`。

**system prompt（原文，照抄）：**
```
你根据我和某人的微信聊天记录，回答我的问题。规则：
- 只用记录里的信息回答，**别编**。记录里没有就直说「记录里没提到」。
- 「那事解决了吗」这类，要顺着时间看：先找事情提出，再看后面有没有跟进/解决，给出结论。
- 答完用一句话标出处，格式：（依据 X月X日的对话）。
- 简洁，口语，直接给结论，别复述记录。
```
**user 内容：** `f"# 和 {name} 的聊天记录\n{带日期的transcript}\n\n# 我的问题\n{question}"`

## 2.3 网页 `qa_server.py`（仿 `server.py` 写，端口 8766）

- 复用 `server.py` 的 HTML 骨架与 `md()` 渲染、`real_sessions` 选人下拉。
- 表单：**选人下拉 + 一个问题输入框 + 提问按钮**（去掉军师那两个 profile/intent 框）。
- `POST /api/ask` body `{talker, name, question}`：按 2.1 取数据 → 2.2 调模型 → 返回 `{ok, answer}`。
- 启动：`uv run python qa_server.py` → `http://localhost:8766`。

自测：浏览器打开，选董丽琼，问"她做什么工作"，应给出来自记录、带日期出处的答案；问个记录里没有的，应答"记录里没提到"。

---

# 执行顺序

两个功能**互相独立**，可并行。建议先一(天天用、见效快)后二。
功能一内部顺序：`memory.py` → `update_memory.py` → 改 `core.py` → 跑董丽琼。
功能二：就一个 `qa_server.py`（仿 server.py），无依赖、无索引。

每写完一个文件按其「自测」命令验证。全部 `.py` 放项目根目录，与现有源码平铺。不改 `relay/`。
