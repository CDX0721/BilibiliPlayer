"""在 QEMU 虚拟机内运行的全功能测试。
音频走 guest 的 ALSA → QEMU intel-hda → 宿主机 data/guest_audio.wav（QEMU wav audiodev 截取）。
在 guest 内执行: python3 guest_test.py <输出json>
"""
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, "/opt/player")
import numpy as np  # noqa: E402

RESULTS = {}


def step(name, fn):
    try:
        RESULTS[name] = fn() if not hasattr(fn, "__wrapped__") else fn()
        print(f"[PASS] {name}: {RESULTS[name]}", flush=True)
        return True
    except Exception as e:
        RESULTS[name] = f"FAIL: {e}"
        print(f"[FAIL] {name}: {e}", flush=True)
        return False


def main():
    from app.bilibili.client import BiliClient
    from app.cache import AudioCache

    c = BiliClient()

    def t_buvid():
        c.ensure_buvid()
        return "buvid3-set"
    step("buvid", t_buvid)

    def t_nav():
        nav = c.nav()
        return f"isLogin={nav.get('isLogin')}"
    step("nav", t_nav)

    def t_view():
        info = c.view("BV1K4421w7zP")
        RESULTS["view_title"] = info["title"][:24]
        return f"cid={info['cid']}"
    step("view", t_view)

    cid = c.view("BV1K4421w7zP")["cid"]

    def t_playurl():
        pu = c.playurl("BV1K4421w7zP", cid)
        return str([a["label"] for a in pu["audio"]])
    step("playurl", t_playurl)

    def t_download():
        cache = AudioCache(c)
        path, qid = cache.ensure("BV1K4421w7zP", cid, 30280)
        RESULTS["cached_qid"] = qid
        return f"{path} {path.stat().st_size//1024}KB"
    step("download", t_download)

    from app.player.engine import PlayerEngine  # noqa: E402

    def t_play_alsa():
        """speaker 后端（ALSA→QEMU→宿主机WAV）。播 25 秒真实音频。"""
        eng = PlayerEngine(backend={"type": "speaker"})
        eng.set_dsp(gain_db=2)
        path = "/opt/player/data/cache/BV1K4421w7zP_30280.m4s"
        eng.load(path, 3051.0, start=100.0)
        time.sleep(25)
        pos = eng.position()
        eng.set_speed(1.5)          # 运行中倍速
        time.sleep(6)
        pos2 = eng.position()
        eng.set_dsp(eq_gains=[8, 0, 0, 0, 0, 0, 0, 0, 0, 0])  # 运行中EQ
        time.sleep(4)
        eng.stop()
        RESULTS["pos_start_25s"] = round(pos, 1)
        RESULTS["pos_after_speed"] = round(pos2, 1)
        return f"pos={pos:.1f}s → {pos2:.1f}s (1.5x)"
    step("playback_alsa", t_play_alsa)

    Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/guest_results.json").write_text(
        json.dumps(RESULTS, ensure_ascii=False, indent=1))
    print(json.dumps(RESULTS, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
