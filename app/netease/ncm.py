"""NCM（网易云音乐加密缓存/下载）解析器 — 纯 Python 实现。

格式规范对照开源实现 taurusxin/ncmdump (C++) 与 nondanee/ncmdump (Python)：
  - 魔数 CTENFDAM；密钥段逐字节 XOR 0x64，元数据段 XOR 0x63
  - 密钥体 AES-128-ECB(CORE_KEY) 去填充，去掉 17 字节前缀得 RC4 key
  - 元数据体去掉 22 字节 "163 key(Don't modify):" → base64 → AES-128-ECB(META_KEY) → 去 "music:" 前缀
  - 音频用 RC4 KSA 建 S 盒后按位置取表：out[p] = audio[p] ^ T[(p+1)&0xFF]，
    T[i] = S[(S[i] + S[(i+S[i])&0xFF]) & 0xFF]
"""
import base64
import json
from pathlib import Path

import numpy as np
from Crypto.Cipher import AES

MAGIC = b"CTENFDAM"
CORE_KEY = bytes.fromhex("687A4852416D736F356B496E62617857")  # hzHRAmso5kInbaxW
META_KEY = bytes.fromhex("2331346C6A6B5F215C5D2630553C2728")  # #14ljk_!\]&0U<'


def _aes_ecb_decrypt(key: bytes, data: bytes) -> bytes:
    if len(data) % 16:
        data = data[: len(data) // 16 * 16]
    return AES.new(key, AES.MODE_ECB).decrypt(data)


def _unpad(data: bytes) -> bytes:
    return data[: -data[-1]] if data and 0 < data[-1] <= 16 else data


def _keybox(rc4_key: bytes) -> bytes:
    S = list(range(256))
    j = 0
    for i in range(256):
        j = (j + S[i] + rc4_key[i % len(rc4_key)]) & 0xFF
        S[i], S[j] = S[j], S[i]
    return bytes(S[(S[i] + S[(i + S[i]) & 0xFF]) & 0xFF] for i in range(256))


def _decrypt_audio(audio: bytes, box: bytes) -> bytes:
    arr = np.frombuffer(audio, dtype=np.uint8)
    ks = np.frombuffer(box, dtype=np.uint8)
    tile = np.resize(ks, len(arr) + 256)  # 周期 256
    tile = np.roll(tile, -1)[: len(arr)]  # 位置偏移：第 p 字节用 T[(p+1)&0xFF]
    return (arr ^ tile).tobytes()


def parse(path) -> dict:
    """解析 .ncm：返回 {meta, image, audio(bytes), format}。"""
    raw = Path(path).read_bytes()
    if raw[:8] != MAGIC:
        raise ValueError(f"不是 NCM 文件（魔数不符）: {raw[:8]!r}")
    pos = 10  # 跳过魔数 + 2 字节填充
    # 密钥段
    n = int.from_bytes(raw[pos:pos + 4], "little")
    pos += 4
    key_data = bytes(b ^ 0x64 for b in raw[pos:pos + n])
    pos += n
    rc4_key = _unpad(_aes_ecb_decrypt(CORE_KEY, key_data))[17:]
    # 元数据段
    m = int.from_bytes(raw[pos:pos + 4], "little")
    pos += 4
    meta = {}
    if m > 0:
        meta_data = bytes(b ^ 0x63 for b in raw[pos:pos + m])
        pos += m
        blob = base64.b64decode(bytes(meta_data[22:]))
        meta = json.loads(_unpad(_aes_ecb_decrypt(META_KEY, blob))[6:].decode("utf-8", "ignore"))
    # CRC32(4) + 保留(1)
    pos += 5
    # 封面帧
    frame_len = int.from_bytes(raw[pos:pos + 4], "little")
    img_size = int.from_bytes(raw[pos + 4:pos + 8], "little")
    image = raw[pos + 8:pos + 8 + img_size] if img_size else b""
    pos += 8 + frame_len
    audio = _decrypt_audio(raw[pos:], _keybox(rc4_key))
    fmt = (meta.get("format") or ("flac" if audio[:4] == b"fLaC" else "mp3")).lower()
    return {"meta": meta, "image": image, "audio": audio, "format": fmt}


def convert(path, out_dir) -> Path:
    """解密 .ncm 并写出 mp3/flac，文件名取自元数据。返回输出路径。"""
    info = parse(path)
    meta = info["meta"]
    artist = "/".join(a[0] for a in meta.get("artist", [])) or "未知"
    name = meta.get("musicName") or Path(path).stem
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{artist} - {name}.{info['format']}"
    out.write_bytes(info["audio"])
    if info["image"]:
        out.with_suffix(".jpg").write_bytes(info["image"])
    return out


def sniff_format(audio: bytes) -> str:
    if audio[:4] == b"fLaC":
        return "flac"
    if audio[:3] == b"ID3" or (len(audio) > 2 and audio[0] == 0xFF and (audio[1] & 0xE0) == 0xE0):
        return "mp3"
    return "bin"
