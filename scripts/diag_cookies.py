"""诊断 Cookie 提取：关闭Edge→复制→检查库→CDP→统计→恢复。不打印任何 Cookie 值。"""
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

edge = ee._find_edge()
was = ee._edge_running()
if was:
    print("Edge 运行中，先关闭…")
    ee._close_edge()

port = ee._free_port()
try:
    tmp = Path(tempfile.mkdtemp(prefix="bp_diag_"))
    ee._copy_profile(tmp)
    dbp = tmp / "Default" / "Network" / "Cookies"
    con = sqlite3.connect(str(dbp))
    rows = con.execute("SELECT host_key, name, length(encrypted_value), substr(encrypted_value,1,3) FROM cookies").fetchall()
    con.close()
    print(f"[DB] 复制的库共 {len(rows)} 条 Cookie，加密前缀分布: {dict(Counter(r[3] for r in rows))}")
    bili = [r for r in rows if "bilibili" in r[0]]
    print(f"[DB] bilibili 条目 {len(bili)} 条: {[(r[0], r[1]) for r in bili[:15]]}")

    # 启动无头 Edge + CDP
    proc = subprocess.Popen([edge, "--headless=new", f"--remote-debugging-port={port}",
                             f"--user-data-dir={tmp}", "--no-first-run", "--disable-gpu",
                             "about:blank"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    import requests, websocket
    ws_url = None
    for _ in range(60):
        try:
            ws_url = requests.get(f"http://127.0.0.1:{port}/json/version", timeout=2).json()["webSocketDebuggerUrl"]
            break
        except Exception:
            time.sleep(0.5)
    print("[CDP] 就绪:", bool(ws_url))
    ws = websocket.create_connection(ws_url.replace("localhost", "127.0.0.1"), timeout=20, suppress_origin=True)
    ws.send(json.dumps({"id": 1, "method": "Storage.getCookies"}))
    cookies = None
    for _ in range(60):
        msg = json.loads(ws.recv())
        if msg.get("id") == 1:
            cookies = msg["result"]["cookies"]
            break
    subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)
    print(f"[CDP] 返回 Cookie 总数: {len(cookies or [])}")
    doms = Counter(c["domain"] for c in cookies or [])
    print("[CDP] 域名分布(前15):", doms.most_common(15))
finally:
    if was:
        ee._relaunch_edge()
