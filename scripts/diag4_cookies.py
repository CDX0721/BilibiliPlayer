"""诊断4：聚焦两个路径变体（反斜杠点 与 尾斜杠），严格超时，实时输出。"""
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
REAL = Path(r"C:\Users\user\AppData\Local\Microsoft\Edge\User Data")

print("关闭 Edge…", flush=True)
ee._close_edge()

def cdp_get(port, timeout_s=25):
    ws_url = None
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        try:
            ws_url = requests.get(f"http://127.0.0.1:{port}/json/version", timeout=1.5).json()["webSocketDebuggerUrl"]
            break
        except Exception:
            time.sleep(0.4)
    if not ws_url:
        return None, "no-cdp"
    try:
        ws = websocket.create_connection(ws_url.replace("localhost", "127.0.0.1"), timeout=8, suppress_origin=True)
        ws.send(json.dumps({"id": 1, "method": "Storage.getCookies"}))
        ws.settimeout(8)
        for _ in range(3):
            msg = json.loads(ws.recv())
            if msg.get("id") == 1:
                return msg["result"]["cookies"], None
        return None, "no-reply"
    except Exception as e:
        return None, str(e)

try:
    for tag, udd in [("dot", str(REAL) + "\\."), ("slash", str(REAL) + "\\")]:
        port = ee._free_port()
        proc = subprocess.Popen([edge, "--headless=new", f"--remote-debugging-port={port}",
                                 f"--user-data-dir={udd}", "--no-first-run", "--disable-gpu",
                                 "about:blank"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(2)
        cookies, err = cdp_get(port)
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)
        time.sleep(1)
        if cookies is None:
            print(f"[{tag}] 失败: {err}", flush=True)
            continue
        bili = [c["name"] for c in cookies if "bilibili" in c.get("domain", "")]
        print(f"[{tag}] 总数={len(cookies)}, bilibili={len(bili)}, SESSDATA={'SESSDATA' in bili}", flush=True)
        if "SESSDATA" in bili:
            jar = {c["name"]: c["value"] for c in cookies if "bilibili" in c.get("domain", "")}
            ee.save_cookies(jar)
            print("SAVED-OK", flush=True)
            break
finally:
    print("完成", flush=True)
