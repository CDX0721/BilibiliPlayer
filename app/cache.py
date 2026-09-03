"""磁盘音频缓存：{bvid}_{qid}.m4s + 简单 LRU 清理。"""
import logging
import os
import time
from pathlib import Path

from . import config

log = logging.getLogger(__name__)
MAX_CACHE_MB = 2048


class AudioCache:
    def __init__(self, client):
        self.client = client

    def path_for(self, bvid: str, qid: int) -> Path:
        return config.CACHE_DIR / f"{bvid}_{qid}.m4s"

    def ensure(self, bvid: str, cid: int, qid: int, prefer_label: str | None = None) -> tuple[Path, int]:
        """返回 (本地路径, 实际音质 id)。缓存命中直接返回。"""
        pu = self.client.playurl(bvid, cid)
        audios = pu.get("audio") or []
        if not audios:
            raise RuntimeError("该视频没有可用音轨")
        chosen = self._choose(audios, qid)
        for cand in [chosen] + [a for a in audios if a is not chosen]:
            dest = self.path_for(bvid, cand["id"])
            if dest.exists() and dest.stat().st_size > 4096:
                return dest, cand["id"]
        for cand in [chosen] + [a for a in audios if a is not chosen]:
            url = cand["base_url"] or cand["backup_url"]
            dest = self.path_for(bvid, cand["id"])
            try:
                ok = self.client.download_audio(url, dest)
                if not ok and cand["backup_url"]:
                    ok = self.client.download_audio(cand["backup_url"], dest)
                if ok:
                    log.info("已缓存 %s @%s", bvid, cand["label"])
                    self._cleanup()
                    return dest, cand["id"]
            except Exception as e:
                log.warning("下载 %s @%s 失败: %s", bvid, cand["label"], e)
        raise RuntimeError("音频下载失败")

    def _choose(self, audios, qid):
        """选 ≤偏好 的最高音质；同 id 偏好 mp4a.40.2。"""
        rank = {a["id"]: config.QUALITY_MAP.get(a["id"], ("", 0))[1] for a in audios}
        pref_rank = config.QUALITY_MAP.get(qid, ("", 3))[1]
        eligible = [a for a in audios if rank[a["id"]] <= pref_rank] or audios
        best = max(eligible, key=lambda a: (rank[a["id"]], a["id"], "mp4a.40.2" in a["codecs"]))
        return best

    def _cleanup(self):
        files = list(config.CACHE_DIR.glob("*.m4s"))
        total = sum(f.stat().st_size for f in files)
        if total <= MAX_CACHE_MB << 20:
            return
        for f in sorted(files, key=lambda f: f.stat().st_mtime):
            f.unlink()
            total -= f.stat().st_size
            if total <= MAX_CACHE_MB << 20 * 0.8:
                break
