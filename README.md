# BilibiliPlayer — B 站音频播放器

把 bilibili 视频的音频当作音乐来听的桌面播放器（个人使用）。

## 功能
- **GUI**（PySide6）：歌单侧栏 / 曲目表 / 播放控制条 / 每曲音效面板
- **收藏夹导入**：读取 B 站账号收藏夹作为特殊歌单（可刷新），未登录也可读公开收藏夹
- **进度条**：拖动 seek（sliderReleased 触发，输入级快速 seek）
- **倍速**：0.5×~2.0×，ffmpeg `atempo` 保音高，运行中切换（当前位置无缝重启）
- **独立歌单**：本地歌单创建/删除，与 B 站收藏夹互不干扰
- **播放模式**：顺序播放 / 循环播放 / 随机播放（Fisher-Yates，整轮重洗、避免立即重复）
- **音质切换**：DASH 音轨 64K/132K/192K（登录后支持 Hi-Res/杜比，若账号有权限）
- **每曲独立音效**：响度增益 ±20dB、10 段 EQ（31Hz~16kHz，RBJ peaking biquad）、
  Reverb（合成 IR 卷积混响：湿声/房间/阻尼）、Delay（20~2000ms 反馈延迟线，带阻尼低通）；
  全部运行中即时生效，自动按曲目保存到 SQLite
- **音频缓存**：DASH m4s 磁盘缓存（LRU 2GB），元数据分级缓存（bvid→cid 永久、fav 5min、wbi 1h、playurl 30min）
- **测试友好**：`--wav 文件` 输出后端（把"播放"写为 WAV，宿主机零出声）

## 运行
```powershell
python -m pip install -r requirements.txt
python -m app.main            # 正常启动（扬声器输出）
python -m app.main --wav out.wav   # 输出到 WAV（测试）
```

## 登录
程序从 `data/cookies.json` 读取 Cookie（SESSDATA/bili_jct/DedeUserID/buvid3…）。
`python scripts/fetch_cookies.py` 可从本机 Edge 提取（复制配置目录 + 无头 CDP，不动运行中的浏览器）。
**注意**：本机验证时 Edge 中的 B 站登录态已在服务端失效（打开首页即被清除），
匿名模式下全部功能可用，仅私有收藏夹与 Hi-Res/杜比音质不可用。

## 测试
```powershell
python scripts/smoke_test.py   # 引擎+DSP 断言（合成音源，WAV 捕获，不出声）
python scripts/api_test.py     # B站 API 全链路（匿名，WAV 捕获）
```
QEMU 虚拟机内端到端测试见 `docs/design.md` 测试方案一节；
虚拟机内音频由 QEMU `-audiodev wav` 截取为宿主机 WAV，不在宿主机播放。

## 结构
```
app/
  bilibili/  client.py(限速+WBI+风控对策) wbi.py
  auth/      edge_extract.py(Edge Cookie 提取)
  player/    engine.py(ffmpeg解码/seek/倍速) dsp.py(增益/EQ/混响/延迟) output.py(speaker/wav后端)
  gui/       main_window.py bridge.py
  cache.py   db.py(歌单/曲目/每曲设置) service.py
scripts/     smoke_test.py api_test.py fetch_cookies.py guest_test.py make_seed.py vnc_capture.py
docs/        design.md research/
```
