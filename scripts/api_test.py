"""B 站 API 全链路实测（匿名模式）：
buvid 获取 → nav → 通过 mid 匿名读公开收藏夹 → fav list → view → playurl → 下载 → 引擎播放(WAV捕获)。
输出 data/api_test_capture.wav，宿主机不出声。
"""
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("api_test")

import numpy as np

from app.bilibili.client import BiliClient, BiliError

# 匿名读取公开收藏夹需要目标 mid；运行时用环境变量 BP_TEST_MID 提供自己的用户 ID
MID = int(os.environ.get("BP_TEST_MID", "0"))


def rms(x):
    return float(np.sqrt(np.mean(x ** 2)))


def main():
    c = BiliClient()
    c.ensure_buvid()
    print(f"[1] buvid3 已就绪: {'buvid3' in dict(c.s.cookies)}")

    nav = c.nav()
    print(f"[2] nav: isLogin={nav.get('isLogin')}（预期 False，匿名模式）")

    try:
        if not MID:
            print("[3] 未设置 BP_TEST_MID（自己的 bilibili 用户 ID），跳过收藏夹实测")
            return
        d = c._get("/x/v3/fav/folder/created/list-all", {"up_mid": MID, "type": 2})
        folders = [{"media_id": f["id"], "title": f["title"], "media_count": f["media_count"]}
                   for f in (d.get("list") or []) if not f.get("attr", 0) & 1]
    except BiliError as e:
        print(f"[3] 收藏夹列举失败: {e}")
        folders = []
    print(f"[3] 公开收藏夹 {len(folders)} 个: {[f['title'] for f in folders[:5]]}")
    assert folders, "无公开收藏夹可测"

    vids = c.fav_videos(folders[0]["media_id"])
    print(f"[4] 「{folders[0]['title']}」视频 {len(vids)} 个: {[v['title'][:20] for v in vids[:3]]}")
    assert vids, "收藏夹为空"

    target = vids[0]
    info = c.view(target["bvid"])
    print(f"[5] view: cid={info['cid']}, 标题={info['title'][:30]}, 时长={info['duration']}s")

    pu = c.playurl(target["bvid"], info["cid"])
    print(f"[6] playurl(匿名): 可用音质 {[(a['id'], a['label']) for a in pu['audio']]}")

    from app.cache import AudioCache
    cache = AudioCache(c)
    path, qid = cache.ensure(target["bvid"], info["cid"], 30280)
    print(f"[7] 缓存: {path.name} ({path.stat().st_size/1e6:.1f} MB) 实际音质={qid}")

    from app.player.engine import PlayerEngine
    eng = PlayerEngine(backend={"type": "wav", "path": "data/api_test_capture.wav"})
    eng.set_speed(1.0)
    eng.load(str(path), info["duration"])
    eng._play_thread.join(timeout=180)
    print(f"[8] 倍速测试: 1.5x 重启于当前位置")
    eng.set_speed(1.5)
    eng._play_thread.join(timeout=180)
    eng.stop()

    import wave
    with wave.open("data/api_test_capture.wav", "rb") as w:
        sr, nf = w.getframerate(), w.getnframes()
        raw = w.readframes(nf)
    x = np.frombuffer(raw, "<i2").astype(np.float32) / 32767.0
    x = x.reshape(-1, w.getnchannels())
    print(f"[9] 播放捕获: {nf/sr:.1f}s, RMS={rms(x):.4f}")
    assert rms(x) > 0.01, "捕获为静音！"
    print("API-TEST PASSED ✔ (capture=data/api_test_capture.wav)")


if __name__ == "__main__":
    main()
