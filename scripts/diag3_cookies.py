"""诊断3：等价路径变体（绕过 CDP 默认目录限制，同时保持 app-bound 解密）。
只打印 Cookie 名，不打印值。"""
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.auth import edge_extract as ee

import requests
import websocket

edge = ee._find_edge()
REAL = Path.home() / "AppData/Local/Microsoft/Edge/User Data"

was = ee._edge_running()
if was:
    print("关闭 Edge…")
    ee._close_edge()

# 取 8.3 短路径
r = subprocess.run(["cmd", "/c", f'for %I in ("{REAL}") do @echo %~sI'], capture_output=True)
short = r.stdout.decode("gbk", "ignore").strip().splitlines()
short = short[-1].strip() if short else None

variants = [
    ("trailing-slash", str(REAL) + "\\"),
    ("dot-suffix", str(REAL) + "\\."),
    ("short-path", short),
    ("unc-drive", "\\\\?\\" + str(REAL)),
]
print("short path:", short)

def cdp_get(port):
    for _ in range(60):
        try:
            ws_url = requests.get(f"http://127.0.0.1:{port}/json/version", timeout=2).json()["webSocketDebuggerUrl"]
            break
        except Exception:
            time.sleep(0.5)
    else:
        return None
    ws = websocket.create_connection(ws_url.replace("localhost", "127.0.0.1"), timeout=20, suppress_origin=True)
    ws.send(json.dumps({"id": 1, "method": "Storage.getCookies"}))
    cookies = None
    for _ in range(60):
        msg = json.loads(ws.recv())
        if msg.get("id") == 1:
            cookies = msg["result"]["cookies"]
            break
    ws.close()
    return cookies

try:
    for tag, udd in variants:
        if not udd:
            continue
        port = ee._free_port()
        proc = subprocess.Popen([edge, "--headless=new", f"--remote-debugging-port={port}",
                                 f"--user-data-dir={udd}", "--no-first-run", "--disable-gpu",
                                 "about:blank"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(3)
        cookies = cdp_get(port)
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)
        time.sleep(1.5)
        if cookies is None:
            print(f"[{tag}] CDP 失败")
            continue
        bili = [c["name"] for c in cookies if "bilibili" in c.get("domain", "")]
        sess = "SESSDATA" in bili
        print(f"[{tag}] 总数={len(cookies)}, bilibili={len(bili)}, 有SESSDATA={sess}")
        if sess:
            names = sorted(set(bili))
            print(f"[{tag}] 成功! Cookie 名: {names}")
            break
finally:
    if was:
        print("恢复 Edge…")
        ee._relaunch_edge()
