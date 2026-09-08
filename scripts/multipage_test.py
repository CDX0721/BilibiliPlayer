"""多分P支持测试：
[1] 旧库自动迁移（tracks: bvid UNIQUE → (bvid,page)，旧行视为 P1）
[2] _expand_pages 离线逻辑（多P展开/单P保持）
[3] 真实数据：在收藏夹中寻找多P视频，展开入库并按对应分P的 cid 下载播放（WAV 捕获，不出声）
"""
import os
import sqlite3
import sys
import tempfile
import time
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np


def rms(x):
    return float(np.sqrt(np.mean(x ** 2))) if len(x) else 0.0


def test_migration():
    tmp = Path(tempfile.mkdtemp(prefix="bp_mig_"))
    dbp = tmp / "player.db"
    old = sqlite3.connect(str(dbp))
    old.executescript("""
      CREATE TABLE tracks(id INTEGER PRIMARY KEY AUTOINCREMENT, bvid TEXT UNIQUE,
        cid INTEGER, title TEXT, upper TEXT, duration REAL, cover TEXT, added_at REAL);
      CREATE TABLE playlists(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE,
        kind TEXT DEFAULT 'local', media_id INTEGER, updated_at REAL);
      CREATE TABLE playlist_tracks(playlist_id INTEGER, track_id INTEGER, position INTEGER,
        PRIMARY KEY(playlist_id, track_id));
      INSERT INTO tracks(bvid,cid,title,duration,added_at) VALUES('BV_oldA',111,'旧A',100,1);
      INSERT INTO tracks(bvid,cid,title,duration,added_at) VALUES('BV_oldB',222,'旧B',200,2);
      INSERT INTO playlists(name) VALUES('旧歌单');
      INSERT INTO playlist_tracks VALUES(1,1,0);
    """)
    old.commit()
    old.close()

    os.environ["BP_DATA_DIR"] = str(tmp)
    import importlib
    import app.config as config
    importlib.reload(config)
    import app.db as db
    importlib.reload(db)
    rows = db._q("SELECT bvid, page, title FROM tracks ORDER BY bvid")
    cols = {r[1] for r in db._q("PRAGMA table_info(tracks)")}
    assert {"page", "part"} <= cols, f"迁移后缺列: {cols}"
    assert [dict(r) for r in rows] == [
        {"bvid": "BV_oldA", "page": 1, "title": "旧A"},
        {"bvid": "BV_oldB", "page": 1, "title": "旧B"}], rows
    # 同 bvid 现在允许第二个分P
    db.add_track_to_playlist(1, {"bvid": "BV_oldA", "page": 2, "title": "旧A P2", "cid": 333})
    n = db._q("SELECT COUNT(*) c FROM tracks")[0]["c"]
    assert n == 3, f"应有三行，得 {n}"
    print("[1] 旧库迁移 PASS ✔（旧行→page=1，同bvid可加新分P）")


def test_expand_offline():
    from app.service import Service

    class FakeClient:
        def view(self, bvid):
            if bvid == "BV_multi":
                return {"cid": 1, "duration": 600, "pages": [
                    {"cid": 11, "part": "开场", "duration": 100},
                    {"cid": 12, "part": "正片", "duration": 300},
                    {"cid": 13, "part": "番外", "duration": 200}]}
            return {"cid": 9, "duration": 60, "pages": [{"cid": 9, "part": "默认", "duration": 60}]}

    svc = Service.__new__(Service)
    svc.client = FakeClient()
    multi = svc._expand_pages({"bvid": "BV_multi", "title": "测试合集", "upper": "UP"})
    assert len(multi) == 3
    assert multi[1]["title"] == "测试合集 P2·正片" and multi[1]["cid"] == 12
    assert multi[2]["duration"] == 200
    single = svc._expand_pages({"bvid": "BV_single", "title": "单P", "upper": "UP"})
    assert len(single) == 1 and single[0]["title"] == "单P" and single[0]["page"] == 1
    print("[2] 展开逻辑 PASS ✔（多P→3曲目各自cid/时长，单P不变）")
    return svc


def _folders(client):
    d = client._get("/x/v3/fav/folder/created/list-all", {"up_mid": 0, "type": 2})
    return [{"media_id": x["id"], "title": x["title"]} for x in (d.get("list") or [])
            if not x.get("attr", 0) & 1]


def _videos(client, media_id):
    try:
        return client.fav_videos(media_id)
    except Exception:
        return []


def test_live(svc):
    from app.bilibili.client import BiliClient
    os.environ["BP_DATA_DIR"] = str(ROOT / "data_test")
    import importlib
    import app.config as config
    importlib.reload(config)
    import app.db as db
    importlib.reload(db)
    client = BiliClient()
    client.load_cookies()
    client.ensure_buvid()
    svc.client = client
    from app.cache import AudioCache
    svc.cache = AudioCache(client)

    target = None
    views = 0
    for f in _folders(client):
        for v in _videos(client, f["media_id"]):
            if views >= 25:
                break
            views += 1
            try:
                pages = client.view(v["bvid"]).get("pages") or []
            except Exception:
                continue
            if len(pages) > 1:
                target = (f, v, pages)
                break
        if target or views >= 25:
            break
    print(f"[3] 扫描 {views} 个视频", end="")
    if not target:
        print(" — 收藏夹未发现多P视频，跳过实播（[1][2]已覆盖逻辑）")
        return
    f, v, pages = target
    print(f" — 发现多P: {v['title'][:24]} 共{len(pages)}P（收藏夹「{f['title']}」）")
    pid = db.create_playlist("分P测试（可删）")
    for t in svc._expand_pages(v):
        db.add_track_to_playlist(pid, t)
    rows = db.playlist_tracks(pid)
    for r in rows:
        print(f"    行: P{r['page']} cid={r['cid']} 时长={r['duration']}s {r['title'][:36]}")
    assert len(rows) == len(pages)

    # 按第二P的 cid 真实下载并播放 8 秒（WAV 捕获）
    p2 = dict(rows[1])
    path, dur, qid = svc.resolve_track(p2, 30232)
    print(f"    P2 音频已缓存: {Path(path).name} 时长={dur}s")
    from app.player.engine import PlayerEngine
    eng = PlayerEngine(backend={"type": "wav", "path": str(ROOT / "data_test/p2_capture.wav")})
    eng.load(str(path), dur)
    t0 = time.time()
    while time.time() - t0 < 8 and eng.playing:
        time.sleep(0.3)
    eng.stop()
    with wave.open(str(ROOT / "data_test/p2_capture.wav"), "rb") as w:
        raw = w.readframes(w.getnframes())
    x = np.frombuffer(raw, "<i2").astype(np.float32) / 32767.0
    print(f"    播放8s捕获 RMS={rms(x):.4f}")
    assert rms(x) > 0.005, "P2 播放为静音"
    print("[3] 真实多P端到端 PASS ✔（按分P入库、按该P cid 下载、播放出真实音频）")


if __name__ == "__main__":
    test_migration()
    svc = test_expand_offline()
    test_live(svc)
    print("MULTI-P ALL PASSED ✔")
