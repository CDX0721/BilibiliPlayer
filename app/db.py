"""SQLite 数据访问层：歌单 / 曲目 / 每曲效果设置。"""
import json
import sqlite3
import threading
import time
from pathlib import Path

from . import config

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS playlists(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT UNIQUE NOT NULL,
  kind TEXT NOT NULL DEFAULT 'local',      -- local | fav
  media_id INTEGER,                        -- 收藏夹 mlid
  updated_at REAL
);
CREATE TABLE IF NOT EXISTS tracks(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  bvid TEXT NOT NULL,
  page INTEGER NOT NULL DEFAULT 1,         -- 分P页码（1=P1）
  part TEXT,                               -- 分P标题（view.pages.part）
  cid INTEGER,
  title TEXT,
  upper TEXT,
  duration REAL,
  cover TEXT,
  added_at REAL,
  UNIQUE(bvid, page)
);
CREATE TABLE IF NOT EXISTS playlist_tracks(
  playlist_id INTEGER NOT NULL REFERENCES playlists(id) ON DELETE CASCADE,
  track_id INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
  position INTEGER NOT NULL,
  PRIMARY KEY (playlist_id, track_id)
);
CREATE INDEX IF NOT EXISTS idx_pt_track ON playlist_tracks(track_id);
CREATE TABLE IF NOT EXISTS track_settings(
  track_id INTEGER PRIMARY KEY REFERENCES tracks(id) ON DELETE CASCADE,
  gain_db REAL DEFAULT 0,
  eq_gains TEXT,    -- JSON [10] floats, NULL=全 0
  reverb TEXT,      -- JSON {mix, room, damp}
  delay TEXT,       -- JSON {time_ms, feedback, mix}
  quality INTEGER,  -- 该曲偏好音质 id, NULL=跟随全局
  speed REAL DEFAULT 1.0
);
CREATE TABLE IF NOT EXISTS kv_cache(
  k TEXT PRIMARY KEY,
  v TEXT NOT NULL,
  expires REAL
);
"""


def conn() -> sqlite3.Connection:
    global _conn
    with _lock:
        if _conn is None:
            _conn = sqlite3.connect(config.DB_PATH, check_same_thread=False)
            _conn.row_factory = sqlite3.Row
            _conn.executescript(SCHEMA)
            _migrate(_conn)
            _conn.execute("PRAGMA foreign_keys=ON")
            _conn.commit()
        return _conn


def _migrate(c: sqlite3.Connection):
    """旧库迁移：tracks 增加 page/part 列与 (bvid,page) 唯一键，旧行视为 P1。"""
    cols = {r[1] for r in c.execute("PRAGMA table_info(tracks)")}
    if "page" in cols:
        return
    c.execute("PRAGMA foreign_keys=OFF")
    c.executescript("""
      CREATE TABLE tracks_new(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        bvid TEXT NOT NULL,
        page INTEGER NOT NULL DEFAULT 1,
        part TEXT,
        cid INTEGER,
        title TEXT,
        upper TEXT,
        duration REAL,
        cover TEXT,
        added_at REAL,
        UNIQUE(bvid, page)
      );
      INSERT INTO tracks_new(id,bvid,page,part,cid,title,upper,duration,cover,added_at)
        SELECT id,bvid,1,NULL,cid,title,upper,duration,cover,added_at FROM tracks;
      DROP TABLE tracks;
      ALTER TABLE tracks_new RENAME TO tracks;
    """)
    c.execute("PRAGMA foreign_keys=ON")
    c.commit()


def _ex(sql, args=()):
    c = conn()
    with _lock:
        cur = c.execute(sql, args)
        c.commit()
        return cur


def _q(sql, args=()):
    return _ex(sql, args).fetchall()


# ---------- 歌单 ----------
def create_playlist(name: str, kind: str = "local", media_id: int | None = None) -> int:
    cur = _ex("INSERT OR IGNORE INTO playlists(name,kind,media_id,updated_at) VALUES(?,?,?,?)",
              (name, kind, media_id, time.time()))
    if cur.lastrowid:
        return cur.lastrowid
    row = _q("SELECT id FROM playlists WHERE name=?", (name,))[0]
    return row["id"]


def delete_playlist(pid: int):
    _ex("DELETE FROM playlist_tracks WHERE playlist_id=?", (pid,))
    _ex("DELETE FROM playlists WHERE id=?", (pid,))


def list_playlists() -> list[dict]:
    return [dict(r) for r in _q("SELECT * FROM playlists ORDER BY kind, id")]


def playlist_tracks(pid: int) -> list[dict]:
    return [dict(r) for r in _q(
        """SELECT t.*, pt.position, s.gain_db, s.eq_gains, s.reverb, s.delay, s.quality, s.speed
           FROM playlist_tracks pt JOIN tracks t ON t.id=pt.track_id
           LEFT JOIN track_settings s ON s.track_id=t.id
           WHERE pt.playlist_id=? ORDER BY pt.position""", (pid,))]


def add_track_to_playlist(pid: int, track: dict) -> int:
    """track: {bvid, page?, cid, title, upper, duration, cover, part?}。
    以 (bvid, page) 去重，已存在则更新，返回 track_id。"""
    page = int(track.get("page") or 1)
    r = _q("SELECT id FROM tracks WHERE bvid=? AND page=?", (track["bvid"], page))
    if r:
        tid = r[0]["id"]
        _ex("UPDATE tracks SET cid=?,title=?,upper=?,duration=?,cover=?,part=? WHERE id=?",
            (track.get("cid"), track.get("title"), track.get("upper"),
             track.get("duration"), track.get("cover"), track.get("part"), tid))
    else:
        tid = _ex("INSERT INTO tracks(bvid,page,part,cid,title,upper,duration,cover,added_at) "
                  "VALUES(?,?,?,?,?,?,?,?,?)",
                  (track["bvid"], page, track.get("part"), track.get("cid"),
                   track.get("title"), track.get("upper"), track.get("duration"),
                   track.get("cover"), time.time())).lastrowid
    pos = _q("SELECT COALESCE(MAX(position),-1)+1 AS p FROM playlist_tracks WHERE playlist_id=?", (pid,))[0]["p"]
    _ex("INSERT OR IGNORE INTO playlist_tracks(playlist_id,track_id,position) VALUES(?,?,?)", (pid, tid, pos))
    return tid


def remove_track_from_playlist(pid: int, tid: int):
    _ex("DELETE FROM playlist_tracks WHERE playlist_id=? AND track_id=?", (pid, tid))


# ---------- 每曲设置 ----------
DEFAULT_SETTINGS = {
    "gain_db": 0.0,
    "eq_gains": [0.0] * 10,
    "reverb": {"mix": 0.0, "room": 0.5, "damp": 0.5},
    "delay": {"time_ms": 250.0, "feedback": 0.3, "mix": 0.0},
    "quality": None,
    "speed": 1.0,
}


def get_settings(tid: int) -> dict:
    s = dict(DEFAULT_SETTINGS)
    rows = _q("SELECT * FROM track_settings WHERE track_id=?", (tid,))
    if rows:
        r = dict(rows[0])
        for k in ("gain_db", "speed", "quality"):
            if r.get(k) is not None:
                s[k] = r[k]
        for k in ("eq_gains", "reverb", "delay"):
            if r.get(k):
                s[k] = json.loads(r[k])
    return s


def save_settings(tid: int, **kw):
    cur = _q("SELECT track_id FROM track_settings WHERE track_id=?", (tid,))
    if not cur:
        _ex("INSERT INTO track_settings(track_id) VALUES(?)", (tid,))
    for k in ("eq_gains", "reverb", "delay"):
        if k in kw and kw[k] is not None:
            kw[k] = json.dumps(kw[k], ensure_ascii=False)
    sets = ", ".join(f"{k}=?" for k in kw)
    _ex(f"UPDATE track_settings SET {sets} WHERE track_id=?", (*kw.values(), tid))


# ---------- 元数据缓存 ----------
def cache_get(k: str):
    rows = _q("SELECT v, expires FROM kv_cache WHERE k=?", (k,))
    if rows and (rows[0]["expires"] is None or rows[0]["expires"] > time.time()):
        return json.loads(rows[0]["v"])
    return None


def cache_put(k: str, v, ttl: float | None = None):
    _ex("INSERT OR REPLACE INTO kv_cache(k,v,expires) VALUES(?,?,?)",
        (k, json.dumps(v, ensure_ascii=False), time.time() + ttl if ttl else None))
