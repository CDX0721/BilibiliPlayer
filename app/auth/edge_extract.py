"""从本机 Edge 提取用户自己的 bilibili Cookie。

Edge 127+ Cookie 为 v20 app-bound 加密，只有真 msedge.exe 能解密；
且 Chromium 136 起 CDP 调试端口对默认 user-data-dir 无效。
方案：复制 Local State + Default/Network/Cookies 到临时目录 →
无头 Edge + --remote-debugging-port → CDP Storage.getCookies。
不打扰正在运行的 Edge，无需管理员。
"""
import json
import logging
import shutil
import socket
import subprocess
import tempfile
import time
from pathlib import Path

log = logging.getLogger(__name__)

EDGE_CANDIDATES = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]
EDGE_PROFILE = Path.home() / "AppData/Local/Microsoft/Edge/User Data"

TARGET_DOMAIN = "bilibili.com"
WANTED = ["SESSDATA", "bili_jct", "DedeUserID", "DedeUserID__ckMd5", "buvid3", "buvid4", "b_nut"]


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _find_edge() -> str | None:
    for p in EDGE_CANDIDATES:
        if Path(p).exists():
            return p
    return None


def _copy_profile(tmp: Path):
    """只拷解密所需文件，避免整目录 GB 级拷贝。"""
    (tmp / "Default" / "Network").mkdir(parents=True, exist_ok=True)
    shutil.copy2(EDGE_PROFILE / "Local State", tmp / "Local State")
    src_dir = EDGE_PROFILE / "Default" / "Network"
    copied = 0
    for name in ("Cookies", "Cookies-wal", "Cookies-shm"):
        src = src_dir / name
        if src.exists():
            shutil.copy2(src, tmp / "Default" / "Network" / name)
            copied += 1
    if not copied:
        raise FileNotFoundError("未找到 Edge Cookies 数据库")


def _cdp_get_cookies(edge: str, timeout_s=60) -> list[dict]:
    port = _free_port()
    with tempfile.TemporaryDirectory(prefix="bp_edge_") as td:
        tmp = Path(td)
        _copy_profile(tmp)
        args = [edge, "--headless=new", f"--remote-debugging-port={port}",
                f"--user-data-dir={tmp}", "--no-first-run", "--no-default-browser-check",
                "--disable-gpu", "--disable-extensions", "about:blank"]
        proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            import requests
            ws_url = None
            deadline = time.time() + timeout_s
            while time.time() < deadline and ws_url is None:
                try:
                    r = requests.get(f"http://127.0.0.1:{port}/json/version", timeout=2)
                    ws_url = r.json()["webSocketDebuggerUrl"]
                except Exception:
                    time.sleep(0.5)
            if not ws_url:
                raise RuntimeError("CDP 端口未就绪")

            import websocket  # websocket-client
            ws = websocket.create_connection(ws_url.replace("localhost", "127.0.0.1"),
                                             timeout=20, suppress_origin=True)
            ws.send(json.dumps({"id": 1, "method": "Storage.getCookies"}))
            deadline = time.time() + 30
            while time.time() < deadline:
                msg = json.loads(ws.recv())
                if msg.get("id") == 1:
                    cookies = msg["result"]["cookies"]
                    ws.close()
                    return cookies
            raise RuntimeError("CDP 响应超时")
        finally:
            try:
                proc.terminate()
                proc.wait(timeout=10)
            except Exception:
                proc.kill()


def extract_bilibili_cookies() -> dict[str, str]:
    """返回 {name: value}；只记录 Cookie 名，绝不打印值。"""
    edge = _find_edge()
    if not edge:
        raise FileNotFoundError("未找到 msedge.exe")
    cookies = _cdp_get_cookies(edge)
    out = {}
    for c in cookies:
        d = c.get("domain", "")
        if d == TARGET_DOMAIN or d.endswith("." + TARGET_DOMAIN):
            out[c["name"]] = c["value"]
    have = [n for n in WANTED if n in out]
    log.info("提取到 %d 条 bilibili Cookie，含关键字段: %s", len(out), have)
    if not have:
        raise RuntimeError("Edge Cookie 中没有 bilibili 登录信息（SESSDATA 缺失）")
    return {n: out[n] for n in out}


def save_cookies(jar: dict[str, str]):
    config.COOKIES_PATH.write_text(json.dumps(jar), encoding="utf-8")
