"""最小 VNC 抓屏客户端（QEMU -vnc 无密码模式）→ PNG。"""
import socket
import struct
import sys

import numpy as np


def recv_n(s, n):
    buf = b""
    while len(buf) < n:
        chunk = s.recv(n - len(buf))
        if not chunk:
            raise RuntimeError("连接关闭")
        buf += chunk
    return buf


def capture(host, port, out_png):
    s = socket.create_connection((host, port), timeout=20)
    s.settimeout(30)
    ver = recv_n(s, 12)
    s.sendall(b"RFB 003.008\n")
    n = recv_n(s, 1)[0]
    types = recv_n(n)
    assert 1 in types, f"无 None 认证: {types}"
    s.sendall(b"\x01")
    if n > 1 or b"\x01" == types:
        res = recv_n(s, 4)
        if res != b"\x00\x00\x00\x00":
            raise RuntimeError(f"认证失败: {res}")
    s.sendall(b"\x01")  # ClientInit shared
    w, h = struct.unpack(">HH", recv_n(s, 4))
    pf = recv_n(s, 16)
    name_len = struct.unpack(">I", recv_n(s, 4))[0]
    recv_n(s, name_len)
    # SetPixelFormat: 32bpp, little-endian, true-color
    s.sendall(b"\x00" + b"\x00" * 3 + struct.pack(">HHBBHHHBBBBBBB",
              32, 96, 0, 1, 0, 255, 255, 255, 16, 8, 0, 0, 0) + b"\x00" * 3)
    # SetEncodings: Raw
    s.sendall(struct.pack(">BxH", 2, 1) + struct.pack(">i", 0))
    # FramebufferUpdateRequest
    s.sendall(struct.pack(">BBHHHH", 3, 0, 0, 0, w, h))
    msg = recv_n(s, 1)
    assert msg == b"\x00", f"意外消息类型 {msg[0]}"
    recv_n(s, 1)
    nrects = struct.unpack(">H", recv_n(s, 2))[0]
    total = 0
    img = np.zeros((h, w, 3), np.uint8)
    for _ in range(nrects):
        rx, ry, rw, rh = struct.unpack(">HHHH", recv_n(s, 8))
        enc = struct.unpack(">i", recv_n(s, 4))[0]
        assert enc == 0, f"非 Raw 编码: {enc}"
        raw = recv_n(s, rw * rh * 4)
        arr = np.frombuffer(raw, np.uint8).reshape(rh, rw, 4)
        img[ry:ry + rh, rx:rx + rw] = arr[:, :, :3]
        total += 1
    try:
        from PIL import Image
        Image.fromarray(img[:, :, ::-1]).save(out_png)
    except ImportError:
        import wave
        np.save(out_png + ".npy", img)
        print("saved npy (PIL 缺失)")
        return
    print(f"saved {out_png} {w}x{h} rects={total}")


if __name__ == "__main__":
    capture("127.0.0.1", 5901, sys.argv[1] if len(sys.argv) > 1 else "data/vnc.png")
