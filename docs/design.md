# BilibiliPlayer 设计文档

## 目标
将 B 站视频的音频作为音乐播放的桌面播放器：GUI；读取 B 站收藏夹作为特殊歌单；进度条拖动；倍速；独立歌单创建；循环/顺序/乱序；音质切换；每个曲目独立的响度增益、EQ、Reverb、Delay。

## 技术选型（理由）
| 项 | 选择 | 理由 |
|---|---|---|
| 语言 | Python 3.13 | 开发快；音频/DSP 生态（numpy/scipy/sounddevice）成熟；GUI 有 PySide6 |
| GUI | PySide6 (Qt6) | 原生桌面控件、QSlider/QDockWidget 满足进度条与效果面板；信号槽与后台线程桥接成熟 |
| 音频解码 | ffmpeg 子进程（imageio-ffmpeg 自带静态 ffmpeg.exe） | 直接解 B 站 DASH m4s；`-ss` 精确 seek；`atempo` 做变速（0.5~2 链式，保音高） |
| DSP | numpy + scipy.signal（自研） | 增益=线性乘；EQ=10 段 RBJ peaking biquad 级联；Reverb=合成 IR 卷积（Freeverb 参数化）；Delay=反馈延迟线；全部可实时应用于解码后的 PCM 流 |
| 输出 | sounddevice(PortAudio)，后端可插拔 | `speaker`（真实播放，QEMU 内由 QEMU 截取）与 `wav`（写入 WAV，测试用，**宿主机测试一律不出声**） |
| 数据库 | SQLite（stdlib sqlite3） | 歌单/曲目/每曲效果设置；零部署 |
| 缓存 | 磁盘音频缓存 data/cache/{bvid}_{qid}.m4s + SQLite 元数据缓存（bvid→cid 永久、fav 列表 5min、wbi key 1h、playurl 30min 且 URL 自带 120min 有效期） | 减少 API 调用与风控压力 |
| 登录 | 从本机 Edge 提取用户自己的 Cookie（复制配置目录→无头 Edge CDP `Storage.getCookies`），另有 QR 登录兜底（GUI 内） | Edge 152 Cookie 为 v20 app-bound 加密，仅浏览器进程自身可解密；CDP 方案不动正在运行的 Edge、无需管理员 |

## 架构
```
┌────────────────────── PySide6 GUI ──────────────────────┐
│ 歌单侧栏 │ 曲目表 │ 底部控制(进度/模式/倍速/音质) │ 效果面板 │
└───────▲──────────────────────────────────────┬──────────┘
        │ Qt Signal（跨线程队列）                │ 调用
┌───────┴───────────────────── PlayerEngine ───┴──────────┐
│ 解码线程: ffmpeg(-ss, -af atempo) → PCM(f32le/48k/2ch)   │
│ DSP 链: Gain → EQ(10×biquad) → Reverb(卷积) → Delay      │
│ 输出后端: SpeakerBackend(sounddevice) / WavBackend(测试) │
└───────┬─────────────────────────────────────────────────┘
        │ HTTP
┌───────┴──────────── BiliClient ─────────────────────────┐
│ Cookie(data/cookies.json) + WBI 签名 + 风控对策(UA/Referer)│
│ nav / fav list-all / fav resource list / view / playurl  │
└───────┬─────────────────────────────────────────────────┘
        │ 下载(DiskCache, 带 Referer)
   data/cache/*.m4s ── ffmpeg 读取 ──► 引擎
```

## 关键实现要点（来自调研，含出处 docs/research/）
- **倍速**：`atempo` 合法域 [0.5,100]，>2 建议 `atempo=2,atempo=2` 链式；变速后媒体位置 = start_offset + 已播放输出样本/sr × tempo；改变倍速=在当前位置重启解码器（~200ms 静音过渡）。
- **EQ**：10 段 31.25/62.5/125/250/500/1k/2k/4k/8k/16k Hz，RBJ peaking，Q≈1.0，增益 ±12dB；scipy.signal.lfilter 携带 zi 状态，滑块变化即时生效不重启。
- **Reverb**：合成脉冲响应（噪声×指数衰减，长度/衰减由 room size 决定，damp 决定低通），`scipy.signal.fftconvolve` + 尾部状态 = 卷积混响，参数化对应 Freeverb 的 roomsize/damp/wet。
- **Delay**：y[n]=x[n]+fb·LP(y[n-D])，D=20~2000ms，fb≤0.9，wet/dry 混合；按 D 分块向量化。
- **音质**：playurl `fnval=16`(或 4048)；`dash.audio[]` id：30216=64K，30232=132K，30280=192K，30250=Dolby(VIP)，30251=Hi-Res FLAC(登录+VIP)；取 ≤偏好的最高 id；CDN 需 `Referer: https://www.bilibili.com/` + 非空 UA，不需 Cookie；URL 120min 有效。
- **收藏夹**：`/x/v3/fav/folder/created/list-all?up_mid=`（id 即 media_id）→ `/x/v3/fav/resource/list?media_id=&pn=&ps=20`（条目**无 cid**，需 `/x/web-interface/view?bvid=` 换 cid 并永久缓存；过滤 type=2 视频、attr 删除位）。
- **WBI**：nav 取 img/sub key → MIXIN_KEY_ENC_TAB 重排前 32 位 → 参数+`wts` 排序拼 mixin_key 取 MD5=`w_rid`。
- **风控**：统一 UA+Referer+完整 Cookie（含 buvid3/4，`/x/frontend/finger/spi` 获取）；调用间隔 ≥1s；-352=风控、-412=拦截、-101=未登录。
- **倍速/进度**：进度条 sliderPressed 抑制刷新、sliderReleased 才 seek（`-ss` 输入级快速 seek）。

## 数据库
```sql
playlists(id PK, name UNIQUE, kind 'local'|'fav', media_id, updated_at)
tracks(id PK, bvid UNIQUE, cid, title, upper, duration, cover, added_at)
playlist_tracks(playlist_id, track_id, position, PK(playlist_id, track_id))
track_settings(track_id PK, gain_db, eq_gains JSON, reverb JSON, delay JSON, quality TEXT, speed REAL)
```

## 测试方案（音频绝不在宿主机出声）
1. 宿主机：`WavBackend` 捕获播放输出到 WAV —— 功能断言（时长、RMS、EQ/混响/延迟改变频谱、变速倍率、seek 位置）。
2. 宿主机 GUI：以 wav 后端启动 GUI，用 computer-use 截图验证界面与进度条。
3. QEMU（用户要求）：QEMU + Debian 云镜像（genericcloud qcow2 + cloud-init 种子 ISO，ssh 2222→22），虚拟机内以 **speaker 后端**真实出声，QEMU `-audiodev wav` 把声卡输出截取为宿主机上的 WAV（不经过宿主机声卡）；镜像内跑 `scripts/guest_test.py` 全功能脚本 + Xvfb 下 GUI 冒烟；宿主机分析 WAV（非静音、时长、频谱差异）。

## 风险与对策
- Edge app-bound：CDP 复制目录方案（已验证思路）；失败则 QR 兜底（用户离线，预期用不上）。
- -352/-412：wbi 签名 + buvid + 限速 + 缓存。
- QEMU 无硬件加速时慢：Debian 云镜像免安装、优先测核心链路，GUI 冒烟次之；时间不够则如实报告。
