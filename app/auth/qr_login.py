"""B 站扫码登录（passport QR 流程）。

generate() → (二维码内容 url, qrcode_key)
poll(key)   → 一次轮询结果：
    code 86101 未扫码 / 86090 已扫码待确认 / 86038 二维码过期 / 0 登录成功
成功时从会话 Cookie 提取 SESSDATA 等，refresh_token 单独落盘（后续刷新 Cookie 用）。
"""
import logging
from pathlib import Path

import requests

from .. import config

log = logging.getLogger(__name__)

PASSPORT = "https://passport.bilibili.com"
WANTED = ["SESSDATA", "bili_jct", "DedeUserID", "DedeUserID__ckMd5",
          "buvid3", "buvid4", "b_nut", "sid"]

# 轮询状态码
QR_WAIT_SCAN = 86101
QR_WAIT_CONFIRM = 86090
QR_EXPIRED = 86038
QR_OK = 0


class QRLogin:
    def __init__(self):
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": config.USER_AGENT,
                               "Referer": "https://www.bilibili.com/"})

    def generate(self) -> tuple[str, str]:
        r = self.s.get(f"{PASSPORT}/x/passport-login/web/qrcode/generate", timeout=10).json()
        if r.get("code") != 0:
            raise RuntimeError(f"二维码生成失败: [{r.get('code')}] {r.get('message')}")
        return r["data"]["url"], r["data"]["qrcode_key"]

    def poll(self, key: str) -> dict:
        """返回 {code, message, cookies, refresh_token}；仅 code==0 时后两者非空。"""
        r = self.s.get(f"{PASSPORT}/x/passport-login/web/qrcode/poll",
                       params={"qrcode_key": key}, timeout=10).json()
        if r.get("code") != 0:
            return {"code": -1, "message": r.get("message", "接口错误"),
                    "cookies": None, "refresh_token": None}
        d = r["data"]
        code = d["code"]
        if code != QR_OK:
            msgs = {QR_WAIT_SCAN: "等待扫码", QR_WAIT_CONFIRM: "已扫码，请在手机上确认",
                    QR_EXPIRED: "二维码已过期"}
            return {"code": code, "message": msgs.get(code, f"状态 {code}"),
                    "cookies": None, "refresh_token": None}
        cookies = {c.name: c.value for c in self.s.cookies}
        picked = {k: v for k, v in cookies.items() if k in WANTED}
        missing = [k for k in ("SESSDATA", "bili_jct", "DedeUserID") if k not in picked]
        if missing:
            raise RuntimeError(f"登录响应缺少 Cookie: {missing}")
        refresh = d.get("refresh_token")
        if refresh:
            (config.DATA_DIR / "refresh_token.txt").write_text(refresh, encoding="utf-8")
        log.info("扫码登录成功，获得 %d 条 Cookie", len(picked))
        return {"code": QR_OK, "message": "登录成功", "cookies": picked,
                "refresh_token": refresh}


def save_login(cookies: dict):
    """把登录 Cookie 写入 data/cookies.json（保留已有 buvid 等字段）。"""
    path = config.COOKIES_PATH
    jar = {}
    if path.exists():
        jar = __import__("json").loads(path.read_text(encoding="utf-8"))
    jar.update(cookies)
    path.write_text(__import__("json").dumps(jar), encoding="utf-8")
