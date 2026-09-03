"""全局路径与常量。"""
import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("BP_DATA_DIR", BASE_DIR / "data"))
CACHE_DIR = DATA_DIR / "cache"
COOKIES_PATH = DATA_DIR / "cookies.json"
DB_PATH = DATA_DIR / "player.db"

for _d in (DATA_DIR, CACHE_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# 统一 UA：API 与 CDN 必须一致（风控）
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36 Edg/126.0.0.0")
REFERER = "https://www.bilibili.com/"

# 音质 id -> (标签, 等级)
QUALITY_MAP = {
    30216: ("64K", 1),
    30232: ("132K", 2),
    30280: ("192K", 3),
    30250: ("杜比全景声", 4),
    30251: ("Hi-Res无损", 5),
}
DEFAULT_QUALITY = 30280

SAMPLE_RATE = 48000
CHANNELS = 2

# 输出后端：speaker（真实播放）/ wav（写文件，测试用，宿主机不出声）
AUDIO_BACKEND = os.environ.get("BP_AUDIO_BACKEND", "speaker")
WAV_BACKEND_PATH = os.environ.get("BP_WAV_PATH", str(DATA_DIR / "capture.wav"))


def ffmpeg_exe() -> str:
    import imageio_ffmpeg
    return os.environ.get("BP_FFMPEG") or imageio_ffmpeg.get_ffmpeg_exe()
