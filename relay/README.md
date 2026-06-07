# relay —— 微信消息镜像转发

把 `data/contacts.json` 里盯的人（董丽琼、小号）的**双向**消息，原样转发到
一台专用腾讯云 Windows 机上**当前打开的那个微信会话**（群）。

```
Mac 微信库 ──WeFlow只读API──> relay_watch.py ──WinRM写队列──> 服务器 C:\relay\queue\
                                                                      │
                          relay_server.py(交互式会话) 轮询队列 ──剪贴板粘贴+回车──> 微信当前会话
```

源端读消息在 Mac（WeFlow 读本机库）；发消息在服务器。服务器只发到“当前打开的会话”，
**不切换会话**——所以发哪个群，取决于你在那台机器微信里打开的是哪个群。

## 机器 / 网络

- 服务器：腾讯云 Windows Server，`101.42.94.17`，user `administrator`。微信 4.x（Qt 窗口，标题“微信”）。
- 直连北京被 GFW/中间盒挡（22 端口被拒、非 443 不通），所以**全程走反向隧道**：
  - Mac 本地代理 `127.0.0.1:28080`（reverse-http-tunnel 项目，先把它跑起来）
  - 跳板机 `49.233.60.29`（ubuntu），到服务器内网 1.5ms
  - `tunnel.sh` 建 `127.0.0.1:15985 -> 101.42.94.17:5985`(WinRM) 的转发
- 没动腾讯云安全组；不需要开任何新入站端口（WinRM 只经隧道访问）。

## 文件

服务器端（部署在 `C:\relay\`）：
- `relay_server.py` —— 队列轮询 + 粘贴发送的常驻代理
- `install_task.ps1` —— 把上面注册成计划任务 `WeChatRelay`（开机/登录自启、崩溃自重启、交互式会话）
- `keep_unlocked.ps1` —— tscon 把会话拉回 console 解锁 + 关屏保/超时（GUI 自动化必须未锁屏）
- `set_autologon.ps1` —— 开机自动登录 administrator（重启后才有交互桌面；**密码明文进注册表**，谨慎）
- `run_interactive.ps1` —— 在交互式会话里跑一次性脚本的辅助（调试用）

Mac 端（在本目录跑）：
- `relay_watch.py` —— 监听器（复用 `../core.py`、`../weflow.py`）
- `queue_push.py` —— 把一条消息经 WinRM 写进服务器队列
- `tunnel.sh` —— 建 WinRM 端口转发
- `pconnect.py` —— 经本地代理做 HTTP CONNECT 的 ProxyCommand
- `winrm.py` —— WinRM 管理小工具（putfile/getfile/ps/cmd），调试/部署用
- `.env` —— WINHOST/WINPORT/WINUSER/WINPASS（**已 gitignore**）

## 日常启动（Mac）

```bash
# 0) 确保 reverse-http-tunnel 本地代理(28080)在跑
# 1) 建 WinRM 隧道
./tunnel.sh
# 2) 起监听（首次只记位置不补历史；之后双向新消息自动转发）
uv run --with pypsrp python relay_watch.py
```

发哪个群：去那台 Windows 机微信里，把**目标群**点开置于当前会话即可。

## 🔴 必须发到「群」，别发到「人」（防回环）

服务器那台微信登的是**小号**，而小号也在被监听的 `contacts.json` 里。
如果服务器当前打开的是**大号的私聊**（小号眼里大号可能叫别的备注名），那么：
转发出去的「小号: xxx」会作为新消息回到大号库 → 又被读到 → 再转成
「小号: 小号: xxx」…… 无限套娃。

所以**目标必须是一个群**（群消息不在被监听的私聊里，不会回环）。
代码里还加了一层兜底 `is_echo()`：凡是「名字: …」开头的消息一律不再转。

## 测试

**前提**：那台 Windows 上已把目标群点开为当前会话。

- **测法 A（只验证"发得进去"）**
  ```bash
  uv run --with pypsrp python relay_watch.py --send "测试一下"
  ```
  几秒后出现在那台微信当前会话里。

- **测法 B（验证完整"监控→转发"）**
  起 `relay_watch.py`，然后用大号给小号发一条（或反过来）→ 群里出现 `我: …` / `小号: …`。
  首次启动只记位置、不补历史，只有**启动后**的新消息才转。

**延迟**：监听是轮询的，`POLL=8` 秒一次，所以最多约 8 秒才会出现，正常。想更快把
`relay_watch.py` 里的 `POLL` 调小（越小越频繁、负载越高）。

**格式**：文字消息转成「发送者名: 内容」——对方发的是「董丽琼: …」，你发的是「我: …」；
图片/语音等非文字转占位符 `[图片]/[语音]/…`。

## 服务器端部署（已完成，重装时照做）

经 `winrm.py` 把文件 putfile 到 `C:\relay\`，然后：
```
# 解锁桌面（每次 RDP 断开/重启后）
& C:\relay\keep_unlocked.ps1
# 装并启动常驻代理
& C:\relay\install_task.ps1 -StartNow
# 重启也能自起（可选，密码明文入注册表）
& C:\relay\set_autologon.ps1 -Pass '<administrator密码>'
```
环境：`C:\relay\.venv`（uv + py3.12 + pyautogui/pyperclip/pywinauto/pillow）。

## 已知坑

- **锁屏即失效**：WinRM/服务在 session 0，GUI 必须在交互式 session。RDP 断开会锁屏 →
  `keep_unlocked.ps1` 用 `tscon <id> /dest:console` 解。重启后需 `set_autologon.ps1`。
- **粘贴前先 Ctrl+A**：否则输入框残留文本会和新消息拼到一起发出去（已在 `relay_server.py` 处理）。
- **.ps1 别写中文注释**：Windows PowerShell 5.1 按 GBK 读无 BOM 的 .ps1，中文会乱码吞行。Python 文件 UTF-8 没事。
- **表情/图片**：非文字消息只转占位符（[图片]/[语音]/[表情]…），不还原内容。
- 隧道断了 relay_watch 会报错并自动重连下一轮；隧道本身需 `tunnel.sh` 重新拉起。
