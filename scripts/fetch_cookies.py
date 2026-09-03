"""提取 Edge 中的 bilibili Cookie → data/cookies.json（只打印 Cookie 名，不打印值）。"""
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

from app.auth import edge_extract

if __name__ == "__main__":
    jar = edge_extract.extract_bilibili_cookies()
    edge_extract.save_cookies(jar)
    wanted = ["SESSDATA", "bili_jct", "DedeUserID", "buvid3", "buvid4"]
    print("OK — 提取的 Cookie 字段:", sorted(jar.keys()))
    print("关键登录字段齐全:", all(k in jar for k in ["SESSDATA", "bili_jct", "DedeUserID"]))
    print("共", len(jar), "条，已保存到 data/cookies.json（值不打印）")
