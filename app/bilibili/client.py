"""B 站 Web API 客户端：Cookie、WBI 签名、限速、缓存。"""
import json
import logging
import threading
import time

import requests

from .. import config, db
from . import wbi as wbi_mod

log = logging.getLogger(__name__)

API = "https://api.bilibili.com"


class BiliError(Exception):
    def __init__(self, code, message):
        super().__init__(f"[{code}] {message}")
        self.code = code


class BiliClient:
    def __init__(self):
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": config.USER_AGENT,
                               "Referer": config.REFERER,
                               "Origin": "https://www.bilibili.com"})
        self._wbi_key = None       # (img_key, sub_key, mixin_key)
        self._last_call = 0.0
        self._rate_lock = threading.Lock()

    # ---------- 基础 ----------
    def load_cookies(self, path=None):
        path = path or config.COOKIES_PATH
        if not path.exists():
            return False
        jar = json.loads(path.read_text(encoding="utf-8"))
        for k, v in jar.items():
            self.s.cookies.set(k, v, domain=".bilibili.com")
        return bool(jar.get("SESSDATA"))

    def _throttle(self):
        with self._rate_lock:
            wait = 1.0 - (time.time() - self._last_call)
            if wait > 0:
                time.sleep(wait)
            self._last_call = time.time()

    def _get(self, path, params=None, signed=False, retry=2, ok_codes=()):
        self._throttle()
        params = dict(params or {})
        if signed:
            params = wbi_mod.sign_params(params, self.mixin_key())
        r = self.s.get(API + path, params=params, timeout=15)
        try:
            data = r.json()
        except Exception:
            raise BiliError(-1, f"HTTP {r.status_code} 非 JSON 响应")
        code = data.get("code", -1)
        if code == 0 or code in ok_codes:
            return data.get("data") or {}
        if code in (-412, -352) and retry > 0:
            log.warning("风控 %s，%ds 后重试: %s", code, 5 * (3 - retry), path)
            time.sleep(5 * (3 - retry))
            return self._get(path, params, signed=False, retry=retry - 1)
        raise BiliError(code, data.get("message", "未知错误"))

    # ---------- WBI ----------
    def mixin_key(self) -> str:
        if self._wbi_key:
            return self._wbi_key[2]
        cached = db.cache_get("wbi_key")
        if cached:
            self._wbi_key = tuple(cached)
            return cached[2]
        d = self._get("/x/web-interface/nav", ok_codes=(-101,))
        img = d["wbi_img"]["img_url"].rsplit("/", 1)[1].split(".")[0]
        sub = d["wbi_img"]["sub_url"].rsplit("/", 1)[1].split(".")[0]
        mk = wbi_mod.get_mixin_key(img, sub)
        self._wbi_key = (img, sub, mk)
        db.cache_put("wbi_key", [img, sub, mk], ttl=3600)
        return mk

    # ---------- 接口 ----------
    def nav(self):
        return self._get("/x/web-interface/nav", ok_codes=(-101,))

    def ensure_buvid(self):
        """无 buvid 时申请并写入 Cookie（降低 -412 概率）。"""
        if self.s.cookies.get("buvid3"):
            return
        d = self._get("/x/frontend/finger/spi")
        self.s.cookies.set("buvid3", d["b_3"], domain=".bilibili.com")
        self.s.cookies.set("buvid4", d["b_4"], domain=".bilibili.com")
        self._persist_buvid(d["b_3"], d["b_4"])

    def _persist_buvid(self, b3, b4):
        path = config.COOKIES_PATH
        jar = json.loads(path.read_text("utf-8")) if path.exists() else {}
        jar.update({"buvid3": b3, "buvid4": b4})
        path.write_text(json.dumps(jar), encoding="utf-8")

    def my_fav_folders(self) -> list[dict]:
        nav = self.nav()
        if not nav.get("isLogin"):
            raise BiliError(-101, "未登录，无法读取收藏夹")
        d = self._get("/x/v3/fav/folder/created/list-all", {"up_mid": nav["mid"], "type": 2})
        return [{"media_id": f["id"], "title": f["title"], "media_count": f["media_count"]}
                for f in (d.get("list") or [])]

    def fav_videos(self, media_id: int) -> list[dict]:
        """收藏夹内全部视频（过滤失效与非视频）。缓存 5 分钟。"""
        ck = f"fav:{media_id}"
        cached = db.cache_get(ck)
        if cached:
            return cached
        out, pn = [], 1
        while True:
            d = self._get("/x/v3/fav/resource/list", {
                "media_id": media_id, "pn": pn, "ps": 20, "order": "mtime",
                "type": 0, "tid": 0, "platform": "web", "web_location": "1550101"})
            for m in (d.get("medias") or []):
                if m.get("type") != 2 or m.get("attr", 0) & 1:
                    continue
                out.append({"bvid": m.get("bv_id") or m.get("bvid"),
                            "title": m.get("title"), "upper": (m.get("upper") or {}).get("name", ""),
                            "duration": m.get("duration"), "cover": m.get("cover")})
            if not d.get("has_more"):
                break
            pn += 1
        db.cache_put(ck, out, ttl=300)
        return out

    def view(self, bvid: str) -> dict:
        cached = db.cache_get(f"view:{bvid}")
        if cached:
            return cached
        d = self._get("/x/web-interface/view", {"bvid": bvid})
        info = {"cid": d["cid"], "title": d["title"], "upper": d["owner"]["name"],
                "duration": d.get("duration", 0), "pages": [
                    {"cid": p["cid"], "part": p["part"], "duration": p.get("duration")}
                    for p in d.get("pages", [])]}
        db.cache_put(f"view:{bvid}", info, ttl=None)
        return info

    def playurl(self, bvid: str, cid: int) -> dict:
        """返回 {timelength, audio: [{id, label, base_url, backup_url, codecs, size}]}。缓存 30min。"""
        ck = f"playurl:{bvid}:{cid}"
        cached = db.cache_get(ck)
        if cached:
            return cached
        d = self._get("/x/player/wbi/playurl", {
            "bvid": bvid, "cid": cid, "qn": 0, "fnval": 4048, "fnver": 0,
            "fourk": 1, "platform": "web"}, signed=True)
        dash = d.get("dash") or {}
        audios = list(dash.get("audio") or [])
        if (dash.get("flac") or {}).get("audio"):
            audios += dash["flac"]["audio"]
        if (dash.get("dolby") or {}).get("audio"):
            audios += dash["dolby"]["audio"]
        out = []
        for a in audios:
            qid = a["id"]
            label = config.QUALITY_MAP.get(qid, (f"id{qid}", 0))[0]
            out.append({"id": qid, "label": label,
                        "base_url": a.get("baseUrl") or a.get("base_url"),
                        "backup_url": (a.get("backupUrl") or a.get("backup_url") or [None])[0],
                        "codecs": a.get("codecs", ""), "bandwidth": a.get("bandwidth", 0)})
        res = {"timelength": d.get("timelength", 0), "audio": out}
        db.cache_put(ck, res, ttl=1800)
        return res

    # ---------- 音频下载（CDN 需 Referer+UA，不需 Cookie） ----------
    def download_audio(self, url: str, dest, timeout=300) -> bool:
        import os
        tmp = str(dest) + ".part"
        with self.s.get(url, stream=True, timeout=timeout,
                        headers={"Referer": config.REFERER, "User-Agent": config.USER_AGENT}) as r:
            r.raise_for_status()
            total = int(r.headers.get("Content-Length") or 0)
            done = 0
            with open(tmp, "wb") as f:
                for chunk in r.iter_content(1 << 18):
                    f.write(chunk)
                    done += len(chunk)
        if total and done < total * 0.98:
            os.remove(tmp)
            return False
        os.replace(tmp, dest)
        return True
