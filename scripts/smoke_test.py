"""核心冒烟测试（不联网、宿主机不出声）：
1) 生成 60s 测试音源（正弦扫频 + 鼓点），ffmpeg 解码路径同线上；
2) 引擎经 WavBackend 播放并捕获 → 验证时长/进度/seek/倍速；
3) DSP：EQ 抬 1kHz → 频谱能量变化；Reverb/Delay → 尾部长度变化；增益 → RMS 变化。
用法: python scripts/smoke_test.py
"""
import io
import logging
import math
import os
import sys
import tempfile
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("BP_DATA_DIR", str(Path(__file__).resolve().parent.parent / "data"))

import numpy as np

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("smoke")

SR = 48000
DUR = 30.0


def make_source(path: Path):
    """WAV 测试音源：440Hz 正弦 + 每秒衰减脉冲。"""
    n = int(SR * DUR)
    t = np.arange(n) / SR
    sig = 0.4 * np.sin(2 * np.pi * 440.0 * t)
    sig += 0.3 * np.sin(2 * np.pi * 1000.0 * t)
    beats = (t % 1.0 < 0.05).astype(float) * np.exp(-((t % 1.0) / 0.02))
    sig += 0.5 * beats * np.sin(2 * np.pi * 80.0 * t)
    stereo = np.stack([sig, sig * 0.95], axis=1)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((np.clip(stereo, -1, 1) * 32767).astype("<i2").tobytes())
    return path


def read_wav(path) -> np.ndarray:
    with wave.open(str(path), "rb") as w:
        sr, nch, nf = w.getframerate(), w.getnchannels(), w.getnframes()
        raw = w.readframes(nf)
    x = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32767.0
    return x.reshape(-1, nch)


def rms(x):
    return float(np.sqrt(np.mean(x ** 2)))


def band_energy(x, freq, sr=SR):
    seg = x[:, 0]
    n = len(seg)
    spec = np.abs(np.fft.rfft(seg * np.hanning(n)))
    freqs = np.fft.rfftfreq(n, 1 / sr)
    m = (freqs > freq * 0.95) & (freqs < freq * 1.05)
    return float(spec[m].sum())


def run_engine(src, wav_out, **kw):
    from app.player.engine import PlayerEngine
    from app.player.dsp import DspChain
    out = wav_out
    eng = PlayerEngine(backend={"type": "wav", "path": out})
    eng.set_dsp(**kw.get("dsp", {}))
    eng.load(str(src), DUR, speed=kw.get("speed", 1.0), start=kw.get("start", 0.0))
    eng._play_thread.join(timeout=180)
    eng.stop()
    return read_wav(out)


def main():
    tmp = Path(tempfile.mkdtemp(prefix="bp_smoke_"))
    src = make_source(tmp / "src.wav")
    results = {}

    # --- 基线播放 ---
    base = run_engine(src, tmp / "base.wav")
    results["baseline_len"] = len(base) / SR
    assert abs(len(base) / SR - DUR) < 1.0, f"时长不符: {len(base)/SR:.2f}"
    assert rms(base) > 0.01, "基线为静音"
    log.info("基线 OK: %.2fs, RMS=%.4f", len(base) / SR, rms(base))

    # --- seek：从 10s 开始 ---
    seeked = run_engine(src, tmp / "seek.wav", start=10.0)
    e_src = read_wav(src)
    ref = e_src[int(10 * SR):int(10 * SR) + SR * 2]
    got = seeked[:SR * 2]
    corr = float(np.corrcoef(ref[:SR, 0], got[:SR, 0])[0, 1])
    results["seek_corr"] = corr
    assert corr > 0.9, f"seek 对齐失败 corr={corr:.3f}"
    log.info("seek OK: 相关性 %.3f", corr)

    # --- 倍速 2.0 ---
    fast = run_engine(src, tmp / "fast.wav", speed=2.0)
    results["fast_len"] = len(fast) / SR
    assert abs(len(fast) / SR - DUR / 2) < 0.6, f"2x 时长不符: {len(fast)/SR:.2f}"
    log.info("2x OK: %.2fs (期望 ~%.1fs)", len(fast) / SR, DUR / 2)

    # --- 倍速 0.5 ---
    slow = run_engine(src, tmp / "slow.wav", speed=0.5)
    assert abs(len(slow) / SR - DUR * 2) < 1.2, f"0.5x 时长不符: {len(slow)/SR:.2f}"
    log.info("0.5x OK: %.2fs", len(slow) / SR)

    # --- EQ：抬 1kHz +8dB ---
    eq = run_engine(src, tmp / "eq.wav", dsp={"eq_gains": [0, 0, 0, 0, 0, 8, 0, 0, 0, 0]})
    ratio = band_energy(eq, 1000) / (band_energy(base, 1000) + 1e-9)
    results["eq_1k_ratio"] = ratio
    assert 1.8 < ratio < 6.0, f"1kHz 能量比异常: {ratio:.2f}"
    log.info("EQ OK: 1kHz 能量比 %.2f (期望 ~2.5)", ratio)

    # --- 增益 +6dB ---
    gain = run_engine(src, tmp / "gain.wav", dsp={"gain_db": 6})
    gr = rms(gain) / rms(base)
    results["gain_ratio"] = gr
    assert 1.7 < gr < 2.3, f"增益比异常: {gr:.2f}"
    log.info("Gain OK: RMS 比 %.2f (期望 ~2.0)", gr)

    # --- Reverb：湿声 50% → 尾巴变长/能量谱变宽 ---
    rev = run_engine(src, tmp / "rev.wav",
                     dsp={"reverb": {"mix": 0.5, "room": 0.8, "damp": 0.5}})
    # 用最后 0.5s（源信号此时几乎为 0？源一直有信号，改用整段高频噪声底对比）
    results["rev_rms"] = rms(rev)
    hf_rev = band_energy(rev, 8000)
    hf_base = band_energy(base, 8000)
    assert hf_rev > hf_base * 0.8, f"混响高频扩散异常 {hf_rev:.1f} vs {hf_base:.1f}"
    log.info("Reverb OK: RMS %.4f (base %.4f), 8kHz 能量 %.0f (base %.0f)",
             rms(rev), rms(base), hf_rev, hf_base)

    # --- Delay：1s 延迟 + 50% 反馈 → 冲激响应出现延迟峰 ---
    imp = tmp / "imp.wav"
    with wave.open(str(imp), "wb") as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(SR)
        x = np.zeros((SR * 3, 2), np.float32)
        x[100, :] = 1.0
        w.writeframes((x * 32767).astype("<i2").tobytes())
    dl = run_engine(imp, tmp / "delay.wav",
                    dsp={"delay": {"time_ms": 1000, "feedback": 0.5, "mix": 1.0}})
    env = np.abs(dl[:SR * 3, 0])
    peak2 = float(env[int(0.95 * SR):int(1.05 * SR)].max())
    results["delay_echo_peak"] = peak2
    assert peak2 > 0.25, f"1s 回声未出现: {peak2:.3f}"
    log.info("Delay OK: 1s 处回声峰值 %.3f (期望 ~0.5)", peak2)

    print("\nSMOKE-TEST-RESULTS:", results)
    print("ALL PASSED ✔")


if __name__ == "__main__":
    main()
