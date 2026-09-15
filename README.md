# BilibiliPlayer — B 站 / 网易云双源音频播放器

把 bilibili 视频和网易云音乐的音频当作音乐来听的桌面播放器（个人使用）。

## 功能
- **GUI**（PySide6）：歌单侧栏 / 曲目表 / 播放控制条 / 每曲音效面板
- **双源播放**：
  - bilibili 收藏夹导入（多分P自动展开），64K~192K DASH 音质
  - 网易云音乐：应用内扫码登录 → 导入创建/收藏的歌单与**播客订阅**；
    音质 128K/192K/320K/无损；**VIP/版权受限曲目自动置灰**（导入时按账号
    vipType 与曲目 fee/st 判定，无权限的曲目显示 [VIP]/[无版权]/[数字专辑] 且拒绝播放）
  - 本地 NCM：**内置 NCM 解密器**（纯 Python，AES-128-ECB + RC4 表变换），
    把 .ncm 转成 mp3/flac 并入库播放
- **别名**：任意曲目（B站/网易云/本地）可起别名，列表优先显示别名（悬停看原名）
- **进度条**：拖动 seek；**倍速** 0.5~2.0（atempo 保音高，运行中切换）
- **播放模式**：顺序 / 循环 / 随机（Fisher-Yates 整轮重洗）
- **每曲独立音效**：响度 ±20dB、10 段 EQ、卷积混响、反馈延迟；即时生效、按曲目落库
- **缓存**：音频磁盘缓存 + 元数据分级缓存；`--wav` 输出后端（静音测试）

## 运行
```powershell
python -m pip install -r requirements.txt
python -m app.main            # 正常启动（扬声器输出）
python -m app.main --wav out.wav   # 输出到 WAV（测试）
```

## 登录
两种账号各自独立，Cookie 分别存于 `data/cookies.json`（B站）与 `data/ne_cookies.json`（网易云）：
- **B站**：应用内"扫码登录"（passport QR），或 `scripts/fetch_cookies.py` 从 Edge 提取，或手动放置
- **网易云**：应用内"网易云登录"（weapi unikey QR 流程），成功后自动保存 MUSIC_U；
  VIP 权限自动检测（vipType 10/11），决定 VIP 曲目可播还是置灰
- 未登录时 B 站公开收藏夹与网易云免费曲目的匿名播放仍可用（部分功能受限）

**注意**：网易 VIP 曲目若无 VIP 会置灰；灰色曲目双击播放会被明确拒绝——
本程序不做任何权限绕过，可播与不可播完全由账号权益决定。

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
