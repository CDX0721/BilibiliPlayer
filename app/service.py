"""服务层：B 站 + 网易云 + 本地 NCM 的统一服务组装。"""
import logging
import re
import subprocess
from pathlib import Path

from . import db
from .bilibili.client import BiliClient
from .cache import AudioCache
from .netease.client import NE_QUALITIES, NeteaseClient, NeError
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
        # 网易云
        self.ne = NeteaseClient()
        self.ne_logged_in = False
        try:
            if self.ne.load_cookies():
                self.ne_logged_in = bool(self.ne.login_status())
        except Exception as e:
            log.warning("网易云登录态检查失败: %s", e)

    # ---------- 网易云 ----------
    def ne_status_text(self) -> str:
        if not self.ne_logged_in:
            return "网易云: 未登录"
        p = self.ne.profile or {}
        vip = "SVIP" if p.get("vipType") == 11 else ("VIP" if self.ne.is_vip() else "")
        return f"网易云: {p.get('nickname', '?')}{'[' + vip + ']' if vip else ''}"

    def ne_import_options(self) -> dict:
        """创建/收藏歌单 + 播客订阅 → [{kind:'pl'|'dj', id, name, count}]。"""
        uid = (self.ne.profile or {}).get("userId")
        if not uid:
            raise NeError(301, "网易云未登录")
        pls = self.ne.user_playlists(uid)
        items = [{"kind": "pl", "id": p["id"], "name": p["name"], "count": p["count"]}
                 for p in pls["created"] + pls["subscribed"]]
        try:
            djs = self.ne.podcast_subscribed()
            items += [{"kind": "dj", "id": r["id"], "name": r["name"], "count": r["count"]}
                      for r in djs]
        except Exception as e:
            log.warning("播客订阅获取失败: %s", e)
        return {"items": items}

    @staticmethod
    def _ne_note(t: dict, vip: bool) -> str | None:
        if t.get("dead"):
            return "无版权"
        if t.get("fee") == 1 and not vip:
            return "VIP"
        if t.get("fee") == 4 and not vip:
            return "数字专辑"
        return None

    def import_ne_items(self, options: list[dict]) -> list[int]:
        """导入选中的网易云歌单/播客，各建一个歌单。"""
        vip = self.ne.is_vip()
        pids = []
        for opt in options:
            if opt["kind"] == "pl":
                tracks = self.ne.playlist_tracks(opt["id"])
            else:
                tracks = self.ne.podcast_programs(opt["id"])
            name = ("[网易云] " if opt["kind"] == "pl" else "[播客] ") + opt["name"]
            pid = db.create_playlist(name, kind="ne")
            for t in tracks:
                note = self._ne_note(t, vip)
                db.add_track_to_playlist(pid, {
                    "bvid": t["ne_id"], "source": "ne", "title": t["title"],
                    "upper": t.get("artist", ""), "duration": t.get("duration", 0),
                    "accessible": 0 if note else 1, "note": note})
            pids.append(pid)
        return pids

    def import_ncm(self, paths: list[str], pid: int) -> list[dict]:
        """NCM 解密转码并作为本地曲目加入歌单。"""
        from .netease.ncm import convert
        out_dir = Path(__file__).resolve().parent.parent / "data" / "ncm_out"
        out_dir.mkdir(parents=True, exist_ok=True)
        results = []
        for p in paths:
            try:
                out = convert(p, out_dir)
                db.add_track_to_playlist(pid, {
                    "bvid": str(out), "source": "local",
                    "title": out.stem, "duration": _probe_duration(out) or 0})
                results.append({"name": out.name, "ok": True, "err": ""})
            except Exception as e:
                results.append({"name": Path(p).name, "ok": False, "err": str(e)})
        return results

    # ---------- 播放 ----------
    def resolve_track(self, track: dict, quality_pref):
        source = track.get("source") or "bili"
        if source == "local":
            return track["bvid"], track.get("duration") or 0, None
        if source == "ne":
            return self._resolve_ne(track, quality_pref)
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

    def _resolve_ne(self, track: dict, quality_pref):
        if not track.get("accessible", 1):
            raise NeError(-1, f"该曲目不可访问（{track.get('note') or '版权限制'}），已置灰")
        s = db.get_settings(track["id"])
        br = int(quality_pref or s.get("quality") or 320000)
        cache_dir = Path(__file__).resolve().parent.parent / "data" / "cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        hits = list(cache_dir.glob(f"ne_{track['bvid']}_{br}.*"))
        if hits:
            return str(hits[0]), float(track.get("duration") or 0), br
        url = self.ne.song_url(track["bvid"], br)
        ext = url.get("type") or "mp3"
        dest = cache_dir / f"ne_{track['bvid']}_{br}.{ext}"
        if not self.ne.download(url["url"], dest):
            raise NeError(-110, "音频下载失败")
        return str(dest), float(track.get("duration") or 0), br

    def play_track(self, track: dict, quality_pref=None):
        s = db.get_settings(track["id"])
        source = track.get("source") or "bili"
        q = quality_pref or s.get("quality")
        if source == "ne":
            q = q or 320000
        elif source == "local":
            q = None
        else:
            q = q or 30280
        path, dur, qid = self.resolve_track(track, q)
        self.engine.set_dsp(gain_db=s["gain_db"], eq_gains=s["eq_gains"],
                            reverb=s["reverb"], delay=s["delay"])
        self.engine.set_master(1.0)
        self.engine.load(str(path), dur, speed=s.get("speed") or 1.0)
        return qid

    def available_qualities(self, bvid: str, page: int = 1, source: str = "bili") -> list[tuple[int, str]]:
        if source == "ne":
            return list(NE_QUALITIES)
        if source != "bili":
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

    # ---------- bilibili 收藏夹 ----------
    def _expand_pages(self, item: dict) -> list[dict]:
        """收藏条目按分P展开；单P保持原样，多P每P一条。"""
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


def _probe_duration(path) -> float | None:
    """ffmpeg -i 解析时长（秒）。"""
    from . import config
    try:
        r = subprocess.run([config.ffmpeg_exe(), "-hide_banner", "-i", str(path)],
                           capture_output=True, timeout=30)
        m = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)",
                      r.stderr.decode("utf-8", "ignore"))
        if m:
            h, mi, s = m.groups()
            return int(h) * 3600 + int(mi) * 60 + float(s)
    except Exception:
        pass
    return None
