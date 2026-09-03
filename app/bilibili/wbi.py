"""WBI 签名（bilibili-API-collect: docs/misc/sign/wbi.md）。"""
import functools
import hashlib
import time
from urllib.parse import urlencode

MIXIN_KEY_ENC_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5, 49,
    33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13, 37, 48, 7, 16, 24, 55, 40,
    61, 26, 17, 0, 1, 60, 51, 30, 4, 22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11,
    36, 20, 34, 44, 52,
]


def get_mixin_key(img_key: str, sub_key: str) -> str:
    raw = img_key + sub_key
    return "".join(raw[i] for i in MIXIN_KEY_ENC_TAB)[:32]


def sign_params(params: dict, mixin_key: str) -> dict:
    p = dict(params)
    p["wts"] = round(time.time())
    p = {k: "".join(ch for ch in str(v) if ch not in "!'()*") for k, v in sorted(p.items())}
    p = {k: v for k, v in sorted(p.items())}
    query = urlencode(p)
    p["w_rid"] = hashlib.md5((query + mixin_key).encode()).hexdigest()
    return p
