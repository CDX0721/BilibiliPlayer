# 调研3：B 站媒体 API 规格（subagent 结论，对照 pskdje/bilibili-API-collect 核验）

所有接口 GET https://api.bilibili.com，返回 {code, message, ttl, data}（code=0 OK）。每次调用带真实浏览器 UA + `Referer: https://www.bilibili.com/`。Cookie：SESSDATA（登录）、buvid3/buvid4（风控）。

## nav — GET /x/web-interface/nav
- data.isLogin / data.mid / vipStatus / vipType / data.wbi_img.img_url|sub_url。
- 未登录 code=-101 但 wbi_img 仍在。img/sub URL 是假 PNG，取文件名为 key；key 每日轮换，缓存 ~1h。

## 收藏夹
- GET /x/v3/fav/folder/created/list-all?up_mid=<mid>：data.list[]{id(=mlid，作 media_id), title, media_count, attr}；attr bit0=1 私有（需 SESSDATA）。
- GET /x/v3/fav/resource/list：params media_id(必), ps(必, 1~20), pn, order=mtime|view|pubtime, type=0(本夹)/1(全部), tid=0, keyword, platform=web。
  - data.info.media_count/title/upper；data.has_more；data.medias[]{id(avid), type(2=视频/12=音频/21合集), title, cover, intro, duration(秒), bvid, bv_id, upper.name, attr, fav_time}。
  - **条目无 cid** → 每视频需调 view；attr 9/1 = 已失效跳过；type!=2 过滤。
  - 错误：-400/-403。

## 视频元数据 — GET /x/web-interface/view?bvid=
- 无需 WBI。data.cid（1P）、data.pages[]{cid,part,duration}、data.title、data.owner.name、data.duration。
- bvid→cid 永久缓存。错误 -404/62002/62004/62012。

## 播放地址 — GET /x/player/wbi/playurl（需 WBI 签名）
- params：bvid, cid, fnval=16(DASH；4048=全特性含 Dolby), fnver=0, fourk=1, platform=web, try_look=1(未登录试看), session。
- data.timelength(ms)；data.dash.audio[]{id, bandwidth, baseUrl/base_url, backupUrl, codecs, mimeType}；Dolby 在 dash.dolby.audio[]，Hi-Res 在 dash.flac.audio；无音轨时 audio:null。
- 音质 id：30216=64K、30232=132K、30280=192K（AAC，均可播放）、30250=Dolby(VIP+fnval&256)、30251=Hi-Res FLAC(登录+VIP)。qn 对 DASH 音频无效 → 从 dash.audio[] 按 id 选（升序，取 ≤偏好最高；同 id 选 mp4a.40.2 > mp4a.40.5）。
- **URL 有效期 120min**（内嵌 deadline）。/x/player/wbi/v2 是播放器元数据接口，不是 playurl 替代。

## DASH 音频 m4s
- 单个自包含 fMP4（ftyp+moov init + sidx + moof 分片），无需 DASH manifest 逻辑。
- 编解码：mp4a.40.2(AAC-LC)、mp4a.40.5(HE-AAC)、ec-3(Dolby)、fLaC。ffmpeg 可直接读 URL。
- CDN 防盗链：必须 `Referer`(under .bilibili.com) + 非空 UA，否则 403；**不需要 Cookie**；支持 Range。mcdn/xy 主机是 PCDN，不稳时用 backup_url。
- ffmpeg 示例：`ffmpeg -user_agent "<UA>" -headers "Referer: https://www.bilibili.com/\r\n" -i "<url>" -vn -c:a copy out.m4a`

## 风控与节奏
- 无官方限速；经验：API 间隔 ≥1s，全缓存（wbi 1h、bvid→cid 永久、fav 5min、playurl 30min）。
- HTTP 412 = 预检拦截（缺 buvid3/UA）；-352 = 风控拒绝；v_voucher = 被标记。

## 每曲调用链
fav list（分页 ps=20）→ view(bvid→cid, 永久缓存) → 签名 playurl（每次播放刷新）→ ffmpeg 读流/下载缓存。
