# 调研2：B 站第三方登录 & Edge Cookie 提取（subagent 结论，已核验出处）

> 仓库 bilibili-API-collect 原仓库已下线，维护中的续作：github.com/pskdje/bilibili-API-collect (2026-01)

## 所需 Cookie
- 登录态：SESSDATA（HttpOnly）、bili_jct（CSRF，POST 用 csrf 字段）、DedeUserID(+__ckMd5)。
- 风控指纹：buvid3、buvid4、b_nut、bili_ticket。ac_time_value = refresh_token（localStorage）。
- SESSDATA 寿命无官方值（社区报 1~数月）；敏感接口会标记 refresh（passport.bilibili.com/x/passport-login/web/cookie/info 的 data.refresh）。

## QR 登录兜底
- GET passport.bilibili.com/x/passport-login/web/qrcode/generate → data.url（做二维码）+ qrcode_key(180s)。
- 轮询 .../web/qrcode/poll?qrcode_key=：0 成功 / 86101 未扫 / 86090 已扫未确认 / 86038 过期。
- 成功时 set-cookie 带 SESSDATA 等，JSON 里返回 refresh_token（保存）。

## Edge Cookie 提取（本机 Edge 152，Cookie 全为 v20 app-bound）
- 库文件：%LOCALAPPDATA%\Microsoft\Edge\User Data\Default\Network\Cookies（连 -wal/-shm 一起拷）。
- v10：AES-256-GCM，key=DPAPI 解 Local State os_crypt.encrypted_key。v20（app-bound）：key 由 SYSTEM elevation_service.exe 经 COM IElevator 包装，Chrome 127(2024-07) 起，Edge 同步实现，**Chrome/Edge 140 起新 Cookie 全为 v20**。
- 关键：ABE 校验的是**浏览器 exe 路径**而非配置目录 → 真 msedge.exe 打开"复制的配置目录"可正常解密；profile 拷贝到临时目录不影响解密。
- browser-cookie3 仅 v10，已停止维护，v20 静默失败；rookiepy 支持 v20 但 Windows v130+ 需管理员。
- IElevator 直调：不可行（elevator 校验调用方 exe 路径，python.exe 被拒）。

## CDP 提取（推荐方案）
- Chromium/Edge 136 起 --remote-debugging-port 对**默认 user-data-dir 无效** → 必须用复制出的非默认目录。
- 步骤：
  1. 复制 User Data（至少 Local State + Default/Network/Cookies[-wal/-shm]）到 %TEMP%\edgecopy。
  2. `msedge.exe --headless=new --remote-debugging-port=9222 --user-data-dir=<copy> --no-first-run`
  3. GET http://127.0.0.1:9222/json/version → webSocketDebuggerUrl
  4. WS 发 {"id":1,"method":"Storage.getCookies"}（browser target）→ 过滤 domain endswith bilibili.com，取 SESSDATA/bili_jct/DedeUserID/buvid3/buvid4。
- 不打扰运行中的 Edge、无需管理员、v20 全兼容。

## WBI 签名
- GET api.bilibili.com/x/web-interface/nav（-101 也带 wbi_img）→ img_url/sub_url 的文件名即 img_key/sub_key（轮换，缓存 ~1h）。
- mixin_key = (img_key+sub_key) 按 64 位 MIXIN_KEY_ENC_TAB 重排取前 32。
- 签名：参数加 wts=unix秒 → 值去掉 !"*'()* → 按 key 升序、URL 编码（%20，大写 hex）拼 query + mixin_key → MD5 = w_rid；请求带 w_rid+wts。

## 风控
- -352 风控校验失败（wbi 过期/缺 dm_img_* 等）；-412 请求被拦截（缺 buvid3/UA 或频率过高，HTTP 412 同理）；-101 未登录。
- buvid 获取：GET api.bilibili.com/x/frontend/finger/spi → data.b_3/b_4 存为 buvid3/buvid4。
- 实践：真实浏览器 UA、Referer: https://www.bilibili.com、全程同一套 Cookie+UA、≥1s 限速。

## 结论（Edge 152）
首选「复制配置目录 → 无头 Edge CDP Storage.getCookies」；兜底：rookiepy(需管理员) → v10 直读(152 不可能) → QR 登录。
