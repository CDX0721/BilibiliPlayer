"""实时 DSP 链：增益 → 10 段 EQ → Reverb(卷积) → Delay(反馈延迟线)。

输入/输出均为 float32/float64 数组，形状 (n, 2)，采样率 48k。
所有参数可运行中修改（EQ/混响/延迟即时生效，不重启解码）。
"""
import numpy as np
from scipy.signal import fftconvolve, lfilter

EQ_FREQS = [31.25, 62.5, 125.0, 250.0, 500.0, 1000.0, 2000.0, 4000.0, 8000.0, 16000.0]
MAX_DELAY_S = 2.0


def _peaking_biquad(f0, gain_db, q, fs):
    """RBJ Audio EQ Cookbook peaking EQ 系数。"""
    A = 10.0 ** (gain_db / 40.0)
    w0 = 2.0 * np.pi * f0 / fs
    alpha = np.sin(w0) / (2.0 * q)
    b0 = 1 + alpha * A
    b1 = -2 * np.cos(w0)
    b2 = 1 - alpha * A
    a0 = 1 + alpha / A
    a1 = -2 * np.cos(w0)
    a2 = 1 - alpha / A
    return np.array([b0, b1, b2]) / a0, np.array([1.0, a1 / a0, a2 / a0])


def _make_ir(room: float, damp: float, fs: int) -> np.ndarray:
    """合成混响脉冲响应（stereo）：噪声 × 指数衰减 × 渐进低通。"""
    room = min(max(room, 0.0), 1.0)
    damp = min(max(damp, 0.0), 1.0)
    length = int(fs * (0.25 + 2.0 * room))
    t = np.arange(length) / fs
    decay = np.exp(-6.0 * t / (0.05 + 0.55 * room))  # 衰减由 room 决定
    rng = np.random.default_rng(12345)
    ir = rng.standard_normal((length, 2)) * decay[:, None]
    # damp 越大高频衰减越快：一阶低通截止 2k~10kHz
    cutoff = 2000 + (1 - damp) * 8000
    k = np.exp(-2 * np.pi * cutoff / fs)
    b = [1 - k]
    a = [1.0, -k]
    ir = lfilter(b, a, ir, axis=0)
    peak = np.max(np.abs(ir)) + 1e-9
    return (ir / peak * 0.9).astype(np.float64)


class _Delay:
    """反馈延迟线 y[n] = (1-mix)*x[n] + mix*u[n], u[n] = x[n] + fb*LP(u[n-D])。"""

    def __init__(self, fs, time_ms=250.0, feedback=0.3, mix=0.0):
        self.fs = fs
        self.maxd = int(MAX_DELAY_S * fs)
        self.buf = np.zeros(self.maxd)
        self.w = 0
        self.lp_z = np.zeros(1)
        self.set_params(time_ms, feedback, mix)

    def set_params(self, time_ms, feedback, mix):
        self.D = max(1, int(min(max(time_ms, 0.0), MAX_DELAY_S * 1000) * self.fs / 1000))
        self.fb = min(max(feedback, 0.0), 0.9)
        self.mix = min(max(mix, 0.0), 1.0)

    def process(self, x: np.ndarray) -> np.ndarray:
        n = x.shape[0]
        if self.mix <= 0.0 or n == 0:
            return x
        mono = x.mean(axis=1)
        u = np.empty(n)
        pos = 0
        while pos < n:
            seg = min(self.D, n - pos)
            idx = (self.w + np.arange(seg)) % self.maxd
            didx = (self.w - self.D + np.arange(seg)) % self.maxd
            delayed = self.buf[didx]
            # 反馈回路一阶低通（携带状态）
            lp, self.lp_z = lfilter([1.0], [1.0, -0.35], delayed, zi=self.lp_z)
            u[pos:pos + seg] = mono[pos:pos + seg] + self.fb * lp
            self.buf[idx] = u[pos:pos + seg]
            self.w = int((self.w + seg) % self.maxd)
            pos += seg
        wet = np.stack([u, u], axis=1)
        return (1 - self.mix) * x + self.mix * wet


class DspChain:
    """可实时调参的效果链。"""

    def __init__(self, fs=48000):
        self.fs = fs
        self.gain_db = 0.0
        self.eq_gains = [0.0] * 10
        self._eq_b = []
        self._eq_a = []
        self._eq_zi = None
        self._reverb_mix = 0.0
        self._ir = None
        self._ir_tail = None
        self._delay = _Delay(fs)
        self.master = 1.0
        self._rebuild_eq()

    # ---------- 参数 ----------
    def _rebuild_eq(self):
        self._eq_b, self._eq_a = [], []
        for f, g in zip(EQ_FREQS, self.eq_gains):
            if abs(g) > 0.05:
                b, a = _peaking_biquad(f, g, 1.0, self.fs)
                self._eq_b.append(b)
                self._eq_a.append(a)
        self._eq_zi = [np.zeros((2, 2)) for _ in self._eq_b]  # 每段 2 个状态 × 2 声道

    def set_params(self, gain_db=None, eq_gains=None, reverb=None, delay=None, master=None):
        if gain_db is not None:
            self.gain_db = float(gain_db)
        if eq_gains is not None and list(eq_gains) != list(self.eq_gains):
            self.eq_gains = [float(g) for g in eq_gains]
            self._rebuild_eq()
        if reverb is not None:
            mix = min(max(float(reverb.get("mix", 0)), 0), 1)
            if mix != self._reverb_mix or self._ir is None or \
               reverb.get("_gen_tag") != getattr(self, "_ir_tag", None):
                self._reverb_mix = mix
                tag = (round(reverb.get("room", 0.5), 2), round(reverb.get("damp", 0.5), 2))
                if tag != getattr(self, "_ir_tag", None) or self._ir is None:
                    self._ir_tag = tag
                    self._ir = _make_ir(tag[0], tag[1], self.fs)
                    tail_len = len(self._ir) - 1
                    old = self._ir_tail
                    self._ir_tail = np.zeros((tail_len, 2))
                    if old is not None:
                        k = min(len(old), tail_len)
                        self._ir_tail[:k] = old[-k:]
            else:
                self._reverb_mix = mix
        if delay is not None:
            self._delay.set_params(delay.get("time_ms", 250), delay.get("feedback", 0.3),
                                   delay.get("mix", 0))
        if master is not None:
            self.master = float(master)

    def reset(self):
        self._eq_zi = [np.zeros((2, 2)) for _ in self._eq_b]
        self._delay = _Delay(self.fs, self._delay.D * 1000 / self.fs, self._delay.fb, self._delay.mix)
        if self._ir is not None:
            self._ir_tail = np.zeros((len(self._ir) - 1, 2))

    # ---------- 处理 ----------
    def process(self, block: np.ndarray) -> np.ndarray:
        x = block.astype(np.float64, copy=True)
        # 1) 增益（dB，正=增幅）
        if self.gain_db:
            x *= 10.0 ** (self.gain_db / 20.0)
        # 2) EQ 级联（带状态，跨块连续）
        for i, (b, a) in enumerate(zip(self._eq_b, self._eq_a)):
            x, self._eq_zi[i] = lfilter(b, a, x, axis=0, zi=self._eq_zi[i])
        # 3) Reverb（FFT 卷积 + 尾部状态）
        if self._reverb_mix > 0 and self._ir is not None:
            tail = self._ir_tail
            conv = fftconvolve(np.vstack([tail, x]), self._ir, axes=0)
            wet = conv[:x.shape[0]]
            self._ir_tail = conv[x.shape[0]:]
            x = (1 - self._reverb_mix) * x + self._reverb_mix * wet
        # 4) Delay
        x = self._delay.process(x)
        # 5) 主音量
        x *= self.master
        return np.clip(x, -1.0, 1.0).astype(np.float32)
