# 调研1：播放器通用功能实现（subagent 结论，已核验出处）

## 播放管线
- `ffmpeg -ss <seek> -i file -f f32le -ar 48000 -ac 2 -` 子进程读 stdout → 环形缓冲(1-5s) → 输出回调。
- `-ss` 在 `-i` 前（输入级 seek）快且精确（FFmpeg 2.1+ 解码丢弃到精确点）。
- 进度：`position = start_offset + frames_played/sample_rate`。时长用 `ffprobe format=duration`。
- sounddevice 回调内禁止分配内存/阻塞/IO → 环形缓冲。

## 倍速
- ffmpeg `atempo` 合法域 **[0.5, 100]**；>2 质量差建议链式 `atempo=2,atempo=2`；0.25x → `atempo=0.5,atempo=0.5`。
- atempo 输出时间轴=源时间轴 → 媒体位置 = start_offset + out_frames/sr × tempo。
- 替代：WSOLA / phase vocoder（librosa）、pedalboard(RubberBand/Signalsmith)。流式首选 atempo。
- 出处: https://ayosec.github.io/ffmpeg-filters-docs/8.0/Filters/Audio/atempo.html , FFmpeg wiki Seeking

## EQ
- 10 段 ISO 1/1 倍频程：31.25, 62.5, 125, 250, 500, 1000, 2000, 4000, 8000, 16000 Hz。
- RBJ peaking biquad: A=10^(dB/40), ω0=2πf0/Fs, α=sin(ω0)/(2Q);
  b0=1+αA, b1=-2cos(ω0), b2=1-αA, a0=1+α/A, a1=-2cos(ω0), a2=1-α/A（除以 a0）。
- Q≈1.0~1.4（1.41=1 倍频程带宽，对称增益单位增益合成）。
- 级联用 SOS（scipy sosfilt/lfilter 带 zi 状态），滑块变只重算该段系数。
- 出处: https://webaudio.github.io/Audio-EQ-Cookbook/audio-eq-cookbook.html

## Reverb (Freeverb 常数，用于参数化卷积混响)
- 每声道 8 并联低通反馈梳状 comb：L={1116,1188,1277,1356,1422,1491,1557,1617} @44.1k，R=L+23；feedback=roomsize*0.28+0.7 (默认0.84)；阻尼 damp*0.4 一阶低通。
- 4 串联 allpass：{556,441,341,225} (+23)，g=0.5。
- 48kHz 按 floor(fs/44100) 缩放延迟。fixedGain 0.015，wet=3、dry=2、width 交叉混合。
- 本项目用合成 IR + fftconvolve 实现等效混响（roomsize→IR 长度/衰减，damp→低通）。
- 出处: https://github.com/thestk/stk/blob/master/src/FreeVerb.cpp

## Delay
- y[n]=x[n]+fb·LP(y[n-D])，D=ms·sr/1000，|fb|<1（≤0.9），wet/dry 线性混合，反馈回路可加一阶低通。
- 出处: https://ccrma.stanford.edu/~jos/pasp/Lowpass_Feedback_Comb_Filter.html

## 播放模式
- 顺序：到底停；循环：i=(i+1)%n；乱序：Fisher-Yates，播完整轮重洗 + 避免相邻重复（Spotify 亦如此）。
- 出处: https://en.wikipedia.org/wiki/Fisher%E2%80%93Yates_shuffle

## SQLite
```sql
tracks(...); playlists(...); playlist_tracks(playlist_id, track_id, position, PK(playlist_id,track_id)) + idx(track_id);
每曲效果设置用 JSON 列（NULL=默认），需排序/过滤的（gain_db）单独列。
```

## PySide6 线程
- 引擎 QObject worker + moveToThread，跨线程 Signal 自动排队；GUI 绝不从音频线程碰 UI。
- 进度条：sliderPressed 置 scrubbing、sliderMoved 只更时间标签、sliderReleased 发 seek。位置 QTimer 10-20Hz 刷新。
