"""分析 QEMU 截取的 guest 音频 WAV：非静音、时长、分段 RMS、频谱差异。"""
import sys
import wave

import numpy as np


def rms(x):
    return float(np.sqrt(np.mean(x ** 2))) if len(x) else 0.0


def band(x, lo, hi, sr=48000):
    seg = x[: min(len(x), sr * 20), 0]
    spec = np.abs(np.fft.rfft(seg * np.hanning(len(seg))))
    f = np.fft.rfftfreq(len(seg), 1 / sr)
    return float(spec[(f >= lo) & (f < hi)].sum())


def main(path):
    with wave.open(path, "rb") as w:
        sr, nch, nf = w.getframerate(), w.getnchannels(), w.getnframes()
        raw = w.readframes(nf)
    x = np.frombuffer(raw, "<i2").astype(np.float32) / 32767.0
    x = x.reshape(-1, nch)
    dur = nf / sr
    third = max(len(x) // 3, 1)
    segs = [rms(x[i * third:(i + 1) * third]) for i in range(3)]
    print(f"file={path} sr={sr} ch={nch} dur={dur:.1f}s")
    print(f"RMS总={rms(x):.4f} 三段RMS={['%.4f' % s for s in segs]}")
    print(f"低频(<300Hz)={band(x, 0, 300):.0f} 高频(>6kHz)={band(x, 6000, 20000):.0f}")
    ok = dur > 20 and rms(x) > 0.005 and len(set(round(s, 4) for s in segs)) > 1
    print("ANALYSIS:", "PASS ✔ (非静音、时长足、各段有变化)" if ok else "CHECK-NEEDED")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "data/guest_audio.wav")
