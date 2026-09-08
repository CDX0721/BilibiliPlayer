"""服务层：把 BiliClient / AudioCache / 数据库 / PlayerEngine 组装起来。"""
import logging

from . import db
from .bilibili.client import BiliClient
from .cache import AudioCache
from .player.engine import PlayerEngine

log = logging.getLogger(__name__)


class Service:
    def __init__(self, engine_backend=None):
        self.client = BiliClient()
        self.logged_in = self.client.load_cookies()
        if self.logged_in:
            try:
                self.client.ensure_buvid()
            except Exception as e:
                log.warning("buvid 获取失败: %s", e)
        self.cache = AudioCache(self.client)
        self.engine = PlayerEngine(backend=engine_backend)

    # ---------- 播放 ----------
    def resolve_track(self, track: dict, quality_pref: int):
        """补全 cid、缓存音频，返回 (path, duration, qid)。"""
        bvid = track["bvid"]
        cid = track.get("cid")
        dur = track.get("duration") or 0
        if not cid:
            info = self.client.view(bvid)
            cid = info["cid"]
            dur = info.get("duration") or dur
            _ex_update_track(track["id"], cid, dur)
        path, qid = self.cache.ensure(bvid, cid, quality_pref)
        return path, float(dur or 0), qid

    def play_track(self, track: dict, quality_pref: int | None = None):
        s = db.get_settings(track["id"])
        q = quality_pref or s.get("quality") or 30280
        path, dur, qid = self.resolve_track(track, q)
        self.engine.set_dsp(gain_db=s["gain_db"], eq_gains=s["eq_gains"],
                            reverb=s["reverb"], delay=s["delay"])
        self.engine.set_master(1.0)
        self.engine.load(str(path), dur, speed=s.get("speed") or 1.0)
        return qid

    def available_qualities(self, bvid: str, page: int = 1) -> list[tuple[int, str]]:
        if not bvid:
            return []
        track = _find_track(bvid, page)
        if not track:
            return []
        cid = track.get("cid")
        try:
            if not cid:
                info = self.client.view(bvid)
                cid = info["cid"]
            pu = self.client.playurl(bvid, cid)
        except Exception as e:
            log.warning("音质列表获取失败: %s", e)
            return []
        seen = {}
        for a in pu.get("audio", []):
            seen[a["id"]] = a["label"]
        return sorted(seen.items(), key=lambda kv: -kv[0])

    # ---------- 收藏夹 ----------
    def _expand_pages(self, item: dict) -> list[dict]:
        """把收藏条目按分P展开为曲目列表；单P保持原样，多P每P一条（标题带 P序号·分P名，
        时长用该P自己的时长，cid 直接落到行上）。"""
        bvid = item["bvid"]
        try:
            info = self.client.view(bvid)
        except Exception as e:
            log.warning("view 失败 %s: %s", bvid, e)
            return [{**item, "page": 1}]
        pages = info.get("pages") or []
        if len(pages) <= 1:
            it = dict(item)
            it.setdefault("page", 1)
            it.setdefault("cid", info.get("cid"))
            if not it.get("duration"):
                it["duration"] = info.get("duration")
            return [it]
        out = []
        for i, p in enumerate(pages, 1):
            part = (p.get("part") or "").strip()
            out.append({"bvid": bvid, "page": p.get("page") or i, "part": part,
                        "cid": p.get("cid"),
                        "title": f"{item.get('title', '')} P{i}" + (f"·{part}" if part else ""),
                        "upper": item.get("upper"),
                        "duration": p.get("duration") or item.get("duration"),
                        "cover": item.get("cover")})
        return out

    def import_fav(self, media_id: int, title: str) -> int:
        pid = db.create_playlist(title, kind="fav", media_id=media_id)
        self.refresh_fav(pid, media_id)
        return pid

    def refresh_fav(self, pid: int, media_id: int):
        for v in self.client.fav_videos(media_id):
            for t in self._expand_pages(v):
                db.add_track_to_playlist(pid, t)

    # ---------- 退出 ----------
    def shutdown(self):
        self.engine.stop()


def _ex_update_track(track_id, cid, dur):
    c = db.conn()
    with db._lock:
        c.execute("UPDATE tracks SET cid=?, duration=? WHERE id=?", (cid, dur, track_id))
        c.commit()


def _find_track(bvid, page=1):
    rows = db._q("SELECT * FROM tracks WHERE bvid=? AND page=?", (bvid, int(page or 1)))
    return dict(rows[0]) if rows else None
