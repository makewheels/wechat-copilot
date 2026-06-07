#!/usr/bin/env python3
"""服务器端（腾讯云 Windows 101.42.94.17）转发代理。

只干一件事：盯着 C:\\relay\\queue\\*.txt，按文件名顺序把每条消息
粘贴到"当前打开的微信会话"输入框并回车，然后挪进 C:\\relay\\done\\。

必须跑在交互式桌面（已登录、未锁屏）的会话里，否则
SetForegroundWindow/截屏会失败。用 install_task.ps1 注册成开机自启的
计划任务（LogonType Interactive）。锁屏问题用 keep_unlocked.ps1 处理。

它不切换会话——发到哪个群，取决于微信里当前打开的是哪个会话。
"""
import os, sys, time, glob, shutil, traceback
import ctypes

# uv 的 venv pythonw 实为 trampoline，会拉起带控制台的 base python.exe，
# 屏幕上会冒一个黑窗口。这里启动即把自己的控制台窗口藏掉。
_con = ctypes.windll.kernel32.GetConsoleWindow()
if _con:
    ctypes.windll.user32.ShowWindow(_con, 0)  # SW_HIDE

import win32gui, win32con
import pyperclip, pyautogui

pyautogui.FAILSAFE = False

ROOT = r"C:\relay"
QUEUE = os.path.join(ROOT, "queue")
DONE = os.path.join(ROOT, "done")
LOGDIR = os.path.join(ROOT, "logs")
for d in (QUEUE, DONE, LOGDIR):
    os.makedirs(d, exist_ok=True)
LOGFILE = os.path.join(LOGDIR, "relay_server.log")


def log(*a):
    line = time.strftime("%Y-%m-%d %H:%M:%S ") + " ".join(str(x) for x in a)
    try:
        with open(LOGFILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def find_wechat():
    found = []
    def enum(h, _):
        if not win32gui.IsWindowVisible(h):
            return
        if "Qt" in win32gui.GetClassName(h) and win32gui.GetWindowText(h) == "微信":
            found.append(h)
    win32gui.EnumWindows(enum, None)
    return found[0] if found else None


def focus(hwnd):
    if win32gui.IsIconic(hwnd):
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        time.sleep(0.3)
    win32gui.ShowWindow(hwnd, win32con.SW_SHOW)
    try:
        pyautogui.keyDown("alt"); pyautogui.keyUp("alt")
        win32gui.SetForegroundWindow(hwnd)
    except Exception as e:
        log("focus warn", e)
    time.sleep(0.4)
    return win32gui.GetForegroundWindow() == hwnd


def send_one(text):
    hwnd = find_wechat()
    if not hwnd:
        log("ERROR 找不到微信窗口，跳过")
        return False
    if not focus(hwnd):
        time.sleep(0.5)
        focus(hwnd)  # 再试一次
    l, t, r, b = win32gui.GetWindowRect(hwnd)
    cx = l + int((r - l) * 0.55)
    cy = b - 70  # 输入框区域（窗口底部上方）
    pyautogui.click(cx, cy)
    time.sleep(0.2)
    pyautogui.hotkey("ctrl", "a")  # 选中输入框里的残留内容，粘贴时整体替换
    time.sleep(0.1)
    pyperclip.copy(text)
    time.sleep(0.15)
    if pyperclip.paste() != text:
        log("WARN 剪贴板回读不一致")
    pyautogui.hotkey("ctrl", "v")
    time.sleep(0.35)
    pyautogui.press("enter")
    time.sleep(0.3)
    return True


def main():
    log("relay_server 启动 pid", os.getpid())
    while True:
        try:
            for fp in sorted(glob.glob(os.path.join(QUEUE, "*.txt"))):
                name = os.path.basename(fp)
                try:
                    with open(fp, "r", encoding="utf-8") as f:
                        text = f.read().rstrip("\n")
                except Exception as e:
                    log("读文件失败", name, e)
                    continue
                if text:
                    ok = send_one(text)
                    log("SENT" if ok else "FAIL", name, repr(text[:80]))
                else:
                    log("SKIP 空", name)
                try:
                    shutil.move(fp, os.path.join(DONE, name))
                except Exception:
                    try:
                        os.remove(fp)
                    except Exception:
                        pass
                time.sleep(0.5)
        except Exception:
            log("LOOP EXC\n" + traceback.format_exc())
        time.sleep(1.0)


if __name__ == "__main__":
    main()
