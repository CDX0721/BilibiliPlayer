"""输出后端：speaker（sounddevice 实时）与 wav（写文件，测试用）。

统一接口：write(block) —— speaker 阻塞以实时消费；wav 立即写盘。
"""
import logging
import threading
import wave

import numpy as np

log = logging.getLogger(__name__)


class WavBackend:
    """把"播放"的 PCM 写成 WAV（测试/QEMU 内分析用），消费速度=解码速度。"""

    def __init__(self, path, fs=48000, channels=2):
        self.fs, self.ch = fs, channels
        self.path = path
        self._wf = None
        self.frames_written = 0

    def open(self):
        self._wf = wave.open(str(self.path), "wb")
        self._wf.setnchannels(self.ch)
        self._wf.setsampwidth(2)
        self._wf.setframerate(self.fs)
        return self

    def write(self, block: np.ndarray):
        pcm = np.clip(block, -1, 1)
        self._wf.writeframes((pcm * 32767).astype("<i2").tobytes())
        self.frames_written += block.shape[0]

    def buffered(self) -> int:
        return 0

    def close(self):
        if self._wf:
            self._wf.close()
            self._wf = None


class SpeakerBackend:
    """sounddevice 实时输出；内部环形缓冲，回调取数。"""

    def __init__(self, fs=48000, channels=2, ring_s=1.0, device=None):
        import sounddevice as sd
        self.sd = sd
        self.fs, self.ch = fs, channels
        self.device = device
        n = int(fs * ring_s)
        self.ring = np.zeros((n, channels), dtype=np.float32)
        self.w = self.r = 0          # 写/读指针（样本数）
        self.used = 0
        self.cond = threading.Condition()
        self.stream = None

    def open(self):
        self.stream = self.sd.OutputStream(
            samplerate=self.fs, channels=self.ch, dtype="float32",
            blocksize=1024, device=self.device, callback=self._cb)
        self.stream.start()
        return self

    def _cb(self, outdata, frames, time_info, status):
        with self.cond:
            take = min(frames, self.used)
            if take > 0:
                idx = (np.arange(self.r, self.r + take) % self.ring.shape[0])
                outdata[:take] = self.ring[idx]
                self.r = int((self.r + take) % self.ring.shape[0])
                self.used -= take
                self.cond.notify_all()
            if take < frames:
                outdata[take:] = 0  # underrun
        if status:
            log.debug("audio status: %s", status)

    def write(self, block: np.ndarray):
        n = block.shape[0]
        with self.cond:
            while self.used + n > self.ring.shape[0]:
                self.cond.wait(timeout=0.5)
            idx = (np.arange(self.w, self.w + n) % self.ring.shape[0])
            self.ring[idx] = block
            self.w = int((self.w + n) % self.ring.shape[0])
            self.used += n

    def buffered(self) -> int:
        with self.cond:
            return self.used

    def close(self):
        try:
            if self.stream:
                self.stream.stop()
                self.stream.close()
        except Exception as e:
            log.warning("关闭输出失败: %s", e)
