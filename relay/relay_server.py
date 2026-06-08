#!/usr/bin/env python3
"""盯着 C:\\relay\\queue\\，粘贴到当前窗口并回车，挪进 done。别的都不管。"""
import io, os, time, glob, shutil, traceback, ctypes

_con = ctypes.windll.kernel32.GetConsoleWindow()
if _con:
    ctypes.windll.user32.ShowWindow(_con, 0)

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


def main():
    log("start pid", os.getpid())
    while True:
        try:
            for fp in sorted(glob.glob(os.path.join(QUEUE, "*"))):
                name = os.path.basename(fp)
                try:
                    with open(fp, "rb") as f:
                        raw = f.read()
                except Exception as e:
                    log("read err", name, e)
                    continue
                if not raw:
                    log("SKIP empty", name)
                elif name.endswith(".img"):
                    import win32clipboard
                    from PIL import Image
                    try:
                        img = Image.open(io.BytesIO(raw))
                        out = io.BytesIO()
                        img.convert("RGB").save(out, "BMP")
                        dib = out.getvalue()[14:]
                        out.close()
                        win32clipboard.OpenClipboard()
                        win32clipboard.EmptyClipboard()
                        win32clipboard.SetClipboardData(win32clipboard.CF_DIB, dib)
                        win32clipboard.CloseClipboard()
                    except Exception as e:
                        log("img decode err", name, e)
                        continue
                    time.sleep(0.15)
                    pyautogui.hotkey("ctrl", "v")
                    time.sleep(0.4)
                    pyautogui.press("enter")
                    time.sleep(0.2)
                    log("SENT img", name, f"{len(raw)}b")
                else:
                    text = raw.decode("utf-8").rstrip("\n")
                    # 确保剪贴板有内容
                    for _ in range(3):
                        pyperclip.copy(text)
                        time.sleep(0.2)
                        if pyperclip.paste() == text:
                            break
                        log("clipboard retry", name)
                    time.sleep(0.3)
                    pyautogui.hotkey("ctrl", "v")
                    time.sleep(0.5)
                    pyautogui.press("enter")
                    time.sleep(1.5)  # 等发完
                    log("SENT", name, repr(text[:80]))
                try:
                    shutil.move(fp, os.path.join(DONE, name))
                except Exception:
                    try:
                        os.remove(fp)
                    except Exception:
                        pass
                time.sleep(0.5)
        except Exception:
            log("LOOP\n" + traceback.format_exc())
        time.sleep(1.5)


if __name__ == "__main__":
    main()
