"""网易云音乐 Web API 客户端（weapi 加密）。

加密方案与端点对照开源项目 NeteaseCloudMusicApi（util/crypto.js 与 module/*）：
  - weapi: body 两层 AES-128-CBC（presetKey → 随机 secretKey），secretKey 反转后
    RSA 无填充加密得 encSecKey
  - 登录态 = Cookie MUSIC_U（+ __csrf 注入 body 的 csrf_token）
  - 灰色判定：song/enhance/player/url 返回 code==200 且 url 非空才可播放；
    元数据侧预判 privilege.st<0 无版权 / fee==1 需VIP / fee==4 需数字专辑
"""
import base64
import json
import logging
import os
import random
import string
import threading
import time
from pathlib import Path

import requests
from Crypto.Cipher import AES

log = logging.getLogger(__name__)

PRESET_KEY = b"0CoJUm6Qyw8W8jud"
IV = b"0102030405060708"
RSA_E = 0x010001
RSA_N = int(
    "00e0b509f6259df8642dbc35662901477df22677ec152b5ff68ace615bb7b725152b3ab17a876aea8a5aa76"
    "d2e417629ec4ee341f56135fccf695280104e0312ecbda92557c93870114af6c9d05c4f7f0c3685b7a46be"
    "e255932575cce10b424d813cfe4875d3e82047b97ddef52741d546b8e289dc6935b3ece0462db0a22b8e7", 16)

BASE = "https://music.163.com"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

QR_WAIT = 801
QR_SCANNED = 802
QR_EXPIRED = 800
QR_OK = 803

NE_QUALITIES = [(999000, "无损"), (320000, "320K"), (192000, "192K"), (128000, "128K")]


class NeError(Exception):
    def __init__(self, code, message):
        super().__init__(f"[{code}] {message}")
        self.code = code


def _pkcs7_pad(data: bytes) -> bytes:
    n = 16 - len(data) % 16
    return data + bytes([n]) * n


def _aes_cbc(data: bytes, key: bytes) -> bytes:
    return AES.new(key, AES.MODE_CBC, IV).encrypt(_pkcs7_pad(data))


def _random_secret() -> str:
    pool = string.ascii_letters + string.digits
    return "".join(random.choice(pool) for _ in range(16))


def weapi_encrypt(body: dict) -> tuple[str, str]:
    text = json.dumps(body)
    secret = _random_secret().encode()
    enc1 = base64.b64encode(_aes_cbc(text.encode(), PRESET_KEY)).decode()
    enc2 = base64.b64encode(_aes_cbc(enc1.encode(), secret)).decode()
    rev = secret[::-1]
    enc_sec = format(pow(int.from_bytes(rev, "big"), RSA_E, RSA_N), "x").rjust(256, "0")
    return enc2, enc_sec


class NeteaseClient:
    def __init__(self):
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": UA, "Referer": BASE + "/"})
        self.profile = None
        self._last = 0.0
        self._lock = threading.Lock()

    # ---------- Cookie ----------
    @property
    def cookies_path(self) -> Path:
        from .. import config
        return config.DATA_DIR / "ne_cookies.json"

    def load_cookies(self) -> bool:
        if not self.cookies_path.exists():
            return False
        jar = json.loads(self.cookies_path.read_text(encoding="utf-8"))
        for k, v in jar.items():
            self.s.cookies.set(k, v, domain="music.163.com")
        self.s.cookies.set("os", "pc", domain="music.163.com")
        self.s.cookies.set("appver", "3.0.0", domain="music.163.com")
        return bool(jar.get("MUSIC_U"))

    def _save_cookies(self):
        jar = {c.name: c.value for c in self.s.cookies if c.domain.endswith("music.163.com")}
        jar.setdefault("os", "pc")
        self.cookies_path.write_text(json.dumps(jar), encoding="utf-8")

    def logout(self):
        self.cookies_path.unlink(missing_ok=True)
        self.s.cookies.clear()
        self.profile = None

    # ---------- 请求 ----------
    def _throttle(self):
        with self._lock:
            wait = 1.2 - (time.time() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.time()

    def _weapi(self, path: str, body: dict | None = None, retry=2):
        self._throttle()
        body = dict(body or {})
        csrf = self.s.cookies.get("__csrf") or ""
        body["csrf_token"] = csrf
        params, enc_sec = weapi_encrypt(body)
        r = self.s.post(f"{BASE}/weapi{path}", data={"params": params, "encSecKey": enc_sec},
                        timeout=15)
        try:
            data = r.json()
        except Exception:
            raise NeError(-1, f"HTTP {r.status_code} 非 JSON 响应")
        code = data.get("code", -1)
        if code in (200, 801, 802, 803, 800, 805, 806, 807):
            return data
        if code in (-460, -431, 503) and retry > 0:
            time.sleep(3)
            return self._weapi(path, body, retry - 1)
        raise NeError(code, data.get("message") or data.get("msg") or "请求失败")

    # ---------- 登录 ----------
    def login_status(self) -> dict | None:
        try:
            d = self._weapi("/w/nuser/account/get")
        except NeError as e:
            if e.code == 301:
                self.profile = None
                return None
            raise
        self.profile = d.get("profile") or None
        return self.profile

    def is_vip(self) -> bool:
        return bool(self.profile and self.profile.get("vipType") in (10, 11))

    def qr_create(self) -> str:
        d = self._weapi("/login/qrcode/unikey", {"type": 3})
        return d["unikey"]

    def qr_poll(self, key: str) -> dict:
        """返回 {code, message}；803 时会话已带登录 Cookie 并落盘。"""
        r = self.s.post(f"{BASE}/weapi/login/qrcode/client/login", timeout=15,
                        data=(lambda p, k: {"params": p, "encSecKey": k})(
                            *weapi_encrypt({"key": key, "type": 3, "csrf_token": ""})))
        d = r.json()
        code = d.get("code")
        msgs = {QR_WAIT: "等待扫码", QR_SCANNED: "已扫码，请在手机上确认",
                QR_EXPIRED: "二维码已过期", QR_OK: "登录成功"}
        if code == QR_OK:
            self._save_cookies()
            self.login_status()
        return {"code": code, "message": msgs.get(code, f"状态 {code}")}

    # ---------- 歌单 ----------
    def user_playlists(self, uid: int) -> dict:
        """返回 {created: [...], subscribed: [...]}，元素 {id, name, count}。"""
        d = self._weapi("/user/playlist", {"uid": uid, "limit": 100, "offset": 0,
                                           "includeVideo": True})
        created, subscribed = [], []
        for p in d.get("playlist", []):
            item = {"id": p["id"], "name": p["name"], "count": p.get("count", 0)}
            if (p.get("creator") or {}).get("userId") == uid:
                created.append(item)
            else:
                subscribed.append(item)
        return {"created": created, "subscribed": subscribed}

    def playlist_tracks(self, pid: int) -> list[dict]:
        d = self._weapi("/v6/playlist/detail", {"id": pid, "n": 100000, "s": 8})
        ids = [t["id"] for t in d.get("playlist", {}).get("trackIds", [])]
        out = []
        for i in range(0, len(ids), 500):
            batch = ids[i:i + 500]
            d2 = self._weapi("/v3/song/detail", {"c": json.dumps([{"id": x} for x in batch])})
            for s in d2.get("songs", []):
                pr = s.get("privilege") or {}
                out.append(self._to_track(s, pr))
        return out

    def podcast_subscribed(self) -> list[dict]:
        d = self._weapi("/djradio/get/subed", {"limit": 50, "offset": 0, "total": True})
        return [{"id": r["id"], "name": r["name"], "count": r.get("programCount", 0)}
                for r in d.get("djRadios", [])]

    def podcast_programs(self, rid: int) -> list[dict]:
        out, offset = [], 0
        while True:
            d = self._weapi("/dj/program/byradio", {"radioId": rid, "limit": 100,
                                                    "offset": offset, "asc": False})
            progs = d.get("programs", [])
            for p in progs:
                s = p.get("mainSong") or {}
                if not s.get("id"):
                    continue
                t = self._to_track(s, s.get("privilege") or {})
                t["title"] = p.get("name") or t["title"]
                out.append(t)
            if len(progs) < 100:
                break
            offset += 100
        return out

    @staticmethod
    def _to_track(s: dict, pr: dict) -> dict:
        fee = pr.get("fee", s.get("fee", 0))
        st = pr.get("st", 0)
        artist = "/".join(a.get("name", "") for a in (s.get("ar") or s.get("artists") or []))
        album = (s.get("al") or s.get("album") or {}).get("name", "")
        return {"ne_id": str(s["id"]), "title": s.get("name", ""), "artist": artist,
                "album": album, "duration": (s.get("dt") or s.get("duration") or 0) / 1000.0,
                "fee": fee, "dead": st < 0, "maxbr": pr.get("maxbr", 0)}

    # ---------- 播放地址 ----------
    def song_url(self, song_id: str, br: int = 320000) -> dict:
        """返回 {url, br, type}；不可播时 raise NeError。"""
        d = self._weapi("/song/enhance/player/url", {"ids": f"[{song_id}]", "br": br})
        item = (d.get("data") or [{}])[0]
        if item.get("code") != 200 or not item.get("url"):
            raise NeError(item.get("code", -110), "无可用播放地址（版权/VIP 限制）")
        return {"url": item["url"], "br": item.get("br", 0),
                "type": item.get("type", "mp3"), "trial": bool(item.get("freeTrialInfo"))}

    # ---------- 下载 ----------
    def download(self, url: str, dest, timeout=300) -> bool:
        import os as _os
        tmp = str(dest) + ".part"
        with self.s.get(url, stream=True, timeout=timeout) as r:
            r.raise_for_status()
            total = int(r.headers.get("Content-Length") or 0)
            done = 0
            with open(tmp, "wb") as f:
                for chunk in r.iter_content(1 << 18):
                    f.write(chunk)
                    done += len(chunk)
        if total and done < total * 0.98:
            _os.remove(tmp)
            return False
        _os.replace(tmp, dest)
        return True
