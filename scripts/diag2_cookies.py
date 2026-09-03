"""诊断2：junction 指向真实 User Data + headless/visible 对比 + 导航触发。
只打印 Cookie 名与统计，不打印值。"""
import json
import logging
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
from app.auth import edge_extract as ee

import requests
import websocket

edge = ee._find_edge()
REAL = Path(r"C:\Users\user\AppData\Local\Microsoft\Edge\User Data")
JUNC = Path(tempfile.mkdtemp(prefix="bp_junc_")) / "UD"

was = ee._edge_running()
if was:
    print("关闭 Edge…")
    ee._close_edge()

def cdp_get(port, timeout_s=30):
    ws_url = None
    for _ in range(int(timeout_s * 2)):
        try:
            ws_url = requests.get(f"http://127.0.0.1:{port}/json/version", timeout=2).json()["webSocketDebuggerUrl"]
            break
        except Exception:
            time.sleep(0.5)
    if not ws_url:
        return None, "CDP端口未就绪"
    ws = websocket.create_connection(ws_url.replace("localhost", "127.0.0.1"), timeout=20, suppress_origin=True)
    ws.send(json.dumps({"id": 1, "method": "Storage.getCookies"}))
    cookies = None
    for _ in range(60):
        msg = json.loads(ws.recv())
        if msg.get("id") == 1:
            cookies = msg["result"]["cookies"]
            break
    ws.close()
    return cookies, None

def summarize(tag, cookies):
    if cookies is None:
        print(f"[{tag}] 失败")
        return
    bili = [c["name"] for c in cookies if "bilibili" in c.get("domain", "")]
    print(f"[{tag}] 总数={len(cookies)}, bilibili={len(bili)}: {sorted(set(bili))[:20]}")

port = ee._free_port()
try:
    # A. 库内直接确认 SESSDATA 是否存在（只看名字）
    dburi = "file:" + str(REAL / "Default/Network/Cookies").replace("\\", "/") + "?mode=ro&immutable=1"
    con = sqlite3.connect(dburi, uri=True)
    names = [r[0] for r in con.execute("SELECT name FROM cookies WHERE host_key LIKE '%bilibili%'")]
    con.close()
    print("[DB] bilibili Cookie 名单:", names)

    # B. junction → 真实目录
    subprocess.run(["cmd", "/c", "mklink", "/J", str(JUNC), str(REAL)], capture_output=True)
    print("[Junction] created:", JUNC.exists())

    # C. headless + junction
    proc = subprocess.Popen([edge, "--headless=new", f"--remote-debugging-port={port}",
                             f"--user-data-dir={JUNC}", "--no-first-run", "--disable-gpu",
                             "about:blank"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(4)
    cookies, err = cdp_get(port)
    summarize("headless+junction", cookies)
    subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)
    time.sleep(2)

    # D. visible + junction
    if cookies is not None and len(cookies) == 0:
        port = ee._free_port()
        proc = subprocess.Popen([edge, f"--remote-debugging-port={port}",
                                 f"--user-data-dir={JUNC}", "--no-first-run", "--window-size=800,600",
                                 "about:blank"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(6)
        cookies, err = cdp_get(port)
        summarize("visible+junction", cookies)
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)
        time.sleep(2)
finally:
    try:
        subprocess.run(["cmd", "/c", "rmdir", str(JUNC)], capture_output=True)
    except Exception:
        pass
    if was:
        print("恢复 Edge…")
        ee._relaunch_edge()
