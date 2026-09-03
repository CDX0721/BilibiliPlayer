"""播放引擎：ffmpeg 解码 → DSP → 输出后端。

- seek：重启解码器（-ss 输入级 seek）；
- 倍速：ffmpeg atempo（链式，保音高），改变时在当前媒体位置重启；
- 进度：start_offset + samples_played/sr × speed；
- 输出后端 speaker=实时播放 / wav=写文件（测试，宿主机不出声）。
"""
import logging
import queue
import subprocess
import threading
import time

import numpy as np

from .. import config
from .dsp import DspChain
from .output import SpeakerBackend, WavBackend

log = logging.getLogger(__name__)
BLOCK_FRAMES = 8192


def _atempo_chain(speed: float) -> str:
    speed = float(np.clip(speed, 0.25, 4.0))
    factors = []
    s = speed
    while s > 2.0:
        factors.append(2.0)
        s /= 2.0
    while s < 0.5:
        factors.append(0.5)
        s = s / 0.5
    factors.append(round(s, 4))
    return ",".join(f"atempo={f}" for f in factors)


class PlayerEngine:
    """纯 Python 引擎（无 GUI 依赖），回调通知 GUI。"""

    def __init__(self, backend=None, fs=config.SAMPLE_RATE, ch=config.CHANNELS):
        self.fs, self.ch = fs, ch
        self.chain = DspChain(fs)
        self.backend = backend  # 延迟创建
        self._backend_spec = backend

        self.proc = None
        self.dec_q: queue.Queue = queue.Queue(maxsize=256)
        self._stop = threading.Event()
        self._pause = threading.Event()
        self._dec_thread = None
        self._play_thread = None
        self._state_lock = threading.Lock()

        self.media_dur = 0.0
        self.speed = 1.0
        self._start_offset = 0.0
        self._samples_played = 0
        self.playing = False
        self.source = None        # 本地文件路径或 URL
        self.is_url = False

        # 回调（GUI 桥接设置）
        self.on_position = None   # fn(media_pos)
        self.on_state = None      # fn(state: playing/paused/stopped/ended)
        self.on_error = None      # fn(msg)

    # ---------- 后端 ----------
    def _make_backend(self):
        spec = self._backend_spec
        if spec is None:
            spec = {"type": config.AUDIO_BACKEND}
        t = spec.get("type", "speaker")
        if t == "wav":
            return WavBackend(spec.get("path", config.WAV_BACKEND_PATH), self.fs, self.ch)
        return SpeakerBackend(self.fs, self.ch, device=spec.get("device"))

    def _emit(self, cb, *a):
        try:
            if cb:
                cb(*a)
        except Exception:
            log.exception("回调异常")

    # ---------- 加载/控制 ----------
    def load(self, source: str, duration: float, speed: float = 1.0, start: float = 0.0):
        """source: 本地音频文件路径或 http(s) URL。"""
        self.stop()
        self.source = source
        self.is_url = source.startswith("http")
        self.media_dur = duration
        self.speed = speed
        self._start_offset = start
        self._samples_played = 0
        self._start_threads()

    def play(self):
        if not self.playing and self.source:
            self._pause.clear()
            self.playing = True
            self._emit(self.on_state, "playing")
            if self.backend:
                pass

    def pause(self):
        if self.playing:
            self._pause.set()
            self.playing = False
            self._emit(self.on_state, "paused")

    def toggle(self):
        self.pause() if self.playing else self.play()

    def seek(self, sec: float):
        sec = float(np.clip(sec, 0, max(self.media_dur - 0.5, 0)))
        if not self.source:
            return
        log.info("seek -> %.1fs", sec)
        self._restart(start=sec, keep_speed=True)

    def set_speed(self, speed: float):
        speed = float(np.clip(speed, 0.25, 4.0))
        if abs(speed - self.speed) < 1e-3:
            return
        pos = self.position()
        self.speed = speed
        if self.source and self.playing:
            self._restart(start=pos, keep_speed=False)

    def set_dsp(self, **kw):
        self.chain.set_params(**kw)

    def set_master(self, v: float):
        self.chain.set_params(master=float(np.clip(v, 0.0, 2.0)))

    def position(self) -> float:
        return self._start_offset + (self._samples_played / self.fs) * self.speed

    # ---------- 内部 ----------
    def _ffmpeg_cmd(self, start: float) -> list[str]:
        ff = config.ffmpeg_exe()
        cmd = [ff, "-hide_banner", "-loglevel", "error"]
        if self.is_url:
            cmd += ["-user_agent", config.USER_AGENT,
                    "-headers", f"Referer: {config.REFERER}\r\n"]
        cmd += ["-ss", f"{max(start, 0):.3f}", "-i", self.source,
                "-vn", "-af", _atempo_chain(self.speed),
                "-f", "f32le", "-acodec", "pcm_f32le", "-ac", str(self.ch),
                "-ar", str(self.fs), "pipe:1"]
        return cmd

    def _restart(self, start: float, keep_speed: bool):
        self._kill_decoder()
        if self._play_thread and self._play_thread.is_alive():
            self._stop.set()
            self._play_thread.join(timeout=5)
        if self.backend:
            self.backend.close()
            self.backend = None
        self._start_offset = start
        self._samples_played = 0
        self.chain.reset()
        self._stop.clear()
        self._pause.clear()
        self._start_threads()

    def _start_threads(self):
        if not self.source:
            return
        self._stop.clear()
        self.playing = True
        self._dec_thread = threading.Thread(target=self._decode_loop, daemon=True)
        self._play_thread = threading.Thread(target=self._play_loop, daemon=True)
        self._dec_thread.start()
        self._play_thread.start()
        self._emit(self.on_state, "playing")

    def _kill_decoder(self):
        if self.proc:
            try:
                self.proc.kill()
            except Exception:
                pass
            self.proc = None

    def _decode_loop(self):
        try:
            self.proc = subprocess.Popen(self._ffmpeg_cmd(self._start_offset),
                                         stdout=subprocess.PIPE,
                                         stderr=subprocess.PIPE)
            nbytes = BLOCK_FRAMES * self.ch * 4
            while not self._stop.is_set():
                raw = self.proc.stdout.read(nbytes)
                if not raw:
                    break
                block = np.frombuffer(raw, dtype=np.float32).reshape(-1, self.ch)
                while not self._stop.is_set():
                    try:
                        self.dec_q.put(block, timeout=0.2)
                        break
                    except queue.Full:
                        continue
            # EOF 哨兵
            while not self._stop.is_set():
                try:
                    self.dec_q.put(None, timeout=0.2)
                    break
                except queue.Full:
                    continue
            err = b""
            try:
                self.proc.wait(timeout=3)
                if self.proc.stderr:
                    err = self.proc.stderr.read()[:2000]
            except Exception:
                pass
            if err and b"error" in err.lower():
                log.error("ffmpeg: %s", err.decode("utf-8", "ignore"))
                self._emit(self.on_error, err.decode("utf-8", "ignore")[-300:])
        except Exception as e:
            log.exception("解码线程崩溃")
            self._emit(self.on_error, str(e))

    def _play_loop(self):
        self.backend = self.backend or self._make_backend()
        if not getattr(self.backend, "_opened", False) and hasattr(self.backend, "open"):
            self.backend.open()
            self.backend._opened = True
        try:
            while not self._stop.is_set():
                if self._pause.is_set():
                    time.sleep(0.05)
                    continue
                try:
                    item = self.dec_q.get(timeout=0.1)
                except queue.Empty:
                    if self._dec_thread and not self._dec_thread.is_alive():
                        # 解码已结束且队列空
                        if self.dec_q.empty():
                            break
                    continue
                if item is None:
                    break
                out = self.chain.process(item)
                self.backend.write(out)
                self._samples_played += out.shape[0]
                self._emit(self.on_position, self.position())
            # 等输出缓冲放完
            t0 = time.time()
            while self.backend and self.backend.buffered() > 0 and time.time() - t0 < 10:
                time.sleep(0.05)
            if not self._stop.is_set():
                self.playing = False
                self._emit(self.on_state, "ended")
        except Exception as e:
            log.exception("播放线程崩溃")
            self._emit(self.on_error, str(e))
        finally:
            self._kill_decoder()

    def stop(self):
        self._stop.set()
        self._kill_decoder()
        if self._play_thread and self._play_thread.is_alive():
            self._play_thread.join(timeout=5)
        if self._dec_thread and self._dec_thread.is_alive():
            self._dec_thread.join(timeout=5)
        if self.backend:
            try:
                self.backend.close()
            except Exception:
                pass
            self.backend = None
        with self._state_lock:
            try:
                while True:
                    self.dec_q.get_nowait()
            except queue.Empty:
                pass
        self.playing = False
        self.source = None
        self._emit(self.on_state, "stopped")
