---
name: douyin-downloader
description: "Douyin link to video/album/live-photo/GIF/audio/music: local tool."
version: 1.2.0
author: tibo
license: MIT
platforms: [windows, linux, macos]
metadata:
  hermes:
    tags: [Douyin, 抖音, Video, Image, Album, LivePhoto, 动图, 实况, GIF, Audio, Music, 配乐, 原声, Download, Chrome, CDP]
    related_skills: [video-download]
---

# 抖音作品下载器（视频 / 图集 / 动图 / GIF / 音频）

给一个抖音链接，把作品的原片存到本地：视频取最高清晰度无水印源，图集取原图（自动转 JPG），
动图/实况连动画一起存（`_01.mp4`）。用户明确要音频时，还能导出视频原声或作品配乐。
完全本地运行，不依赖任何第三方 Python 包。

## When to Use

用户粘贴抖音链接（短链 `v.douyin.com/xxx`、`/video/<id>`、`/note/<id>`、裸 19 位 ID，
或整段分享文案）并想要其中的视频、图片、图集、动图/实况或 GIF。也用于用户问"这个抖音链接能下吗"、
"帮我存下来"、"要原图"、"转个 GIF"、"动图怎么不动"。非抖音平台（YouTube/TikTok/B站）用 `video-download`。

用户额外提到**声音/音频**时也归这里：`原声`、`视频里的声音`、`音频`、`配音`、`人声` →
加 `--audio`；`配乐`、`BGM`、`背景音乐`、`这首歌`、`音乐` → 加 `--music`。
**用户没提音频就不要加这两个参数**——默认只下原片/原图，一个音频文件都不产出。
要音频时把参数加在**同一条命令**上，一次跑完，不要先下视频再单独跑一遍。

动图/实况默认会连 `_01.mp4` 一起存；用户明确说"只要图/不要动图"时才加 `--still-only`。
用户要 GIF 时加 `--to-gif`——别自己把动图 mp4 重命名成 `.gif`，那不是 GIF。

**路径**：`scripts/douyin_dl.py`、`scripts/douyin_cdp.py`、`scripts/下载抖音视频.bat`、`scripts/README.md`。
把前两个 `.py` 拷到同一目录即可运行；`README.md` 可以直接给用户看。

## 安装（三步，无需 pip）

1. 把 `scripts/douyin_dl.py` 和 `scripts/douyin_cdp.py` 放进**同一个文件夹**。
   `douyin_dl.py` 通过 `os.path.dirname(__file__)` 定位 `douyin_cdp.py`，两个文件不能分开。
2. 装一个 Chromium 内核浏览器。Windows 一般已有 Chrome 或 Edge，直接可用。
   找不到时设置环境变量：
   ```bash
   # Windows (cmd)
   set CHROME_PATH=D:\Chrome\chrome.exe
   # Linux/macOS
   export CHROME_PATH=/usr/bin/google-chrome
   ```
3. 可选：装 ffmpeg，用于图片转 JPG、视频转 GIF、**从视频里抽音轨**、以及下载后的时长校验。
   ```bash
   winget install ffmpeg     # Windows
   brew install ffmpeg       # macOS
   sudo apt install ffmpeg   # Debian/Ubuntu
   ```
   没装也能跑，只是 webp 原图保持原格式、`--to-gif` 不可用、`--audio` 退化为直接下载音乐轨
   （见「音频与配乐」）、校验退化为读文件头。

## 用法

```bash
# 最常用：粘贴链接或整段分享文案
python douyin_dl.py "https://v.douyin.com/XXXX/"
python douyin_dl.py "7.17 复制此链接打开抖音 https://v.douyin.com/XXXX/ ..."

# 落地到指定目录
python douyin_dl.py "<链接>" -o D:\my\videos

# 图集 / 动图（自动逐张下载；有动图的会连 _01.mp4 一起存）
python douyin_dl.py "https://www.douyin.com/note/7697265979428200050"

# 只要静态图，不要动图/实况片段
python douyin_dl.py "<链接>" --still-only

# 视频或动图额外转 GIF（12fps / 480px 宽）
python douyin_dl.py "<链接>" --to-gif

# 额外导出视频原声（m4a，需 ffmpeg）
python douyin_dl.py "<链接>" --audio

# 额外下载作品配乐/音乐（mp3，不需要 ffmpeg）
python douyin_dl.py "<链接>" --music

# 原声和配乐都要；原声要 mp3 就再指定格式
python douyin_dl.py "<链接>" --audio --music --audio-format mp3

# 指定清晰度
python douyin_dl.py "<链接>" -q 720

# 批量：文件里一行一个链接或整段文案
python douyin_dl.py --batch links.txt -o downloads

# 给程序调用
python douyin_dl.py "<链接>" --json
```

### 输出约定

| 作品类型 | 产出文件 |
|---|---|
| 视频 | `douyin_<id>.mp4`（最高清晰度，无水印） |
| 图集 | `douyin_<id>_01.jpg`、`_02.jpg` …（原图，按张编号） |
| 动图/实况 | 额外 `douyin_<id>_01.mp4` …（每张图的动画，默认保存） |
| `--still-only` | 不产出上面的动图 mp4，只留静态图 |
| `--audio` | 额外 `douyin_<id>_audio.m4a`（`--audio-format mp3` 时是 `.mp3`） |
| `--music` | 额外 `douyin_<id>_music.mp3` |
| `--to-gif` | 额外 `douyin_<id>.gif`；动图作品额外 `_01.gif` … |

### 动图 / 实况（默认连动画一起存）

抖音的动图/实况作品，每张图其实是「静态首帧 + 一段短 mp4」：
`url_list` 里是首帧 jpg/webp，真正的动画在 `images[i].video.play_addr`。
默认两个都存，编号一致：

| 产出 | 说明 |
|---|---|
| `douyin_<id>_01.jpg` | 首帧原图 |
| `douyin_<id>_01.mp4` | 动图/实况片段（h264，通常带 aac 音轨） |

- 只要静态图：加 `--still-only`。
- 要 GIF：再加 `--to-gif`，每段额外产出 `_01.gif`（无音轨，480px/12fps，体积可能 3–5 MB）。
- 普通图集没有 `video` 字段，不会凭空多出 mp4；判断依据是 `images[i].video.play_addr` 是否存在。

### 音频与配乐（默认不下载）

| 用户说的 | 参数 | 产出 |
|---|---|---|
| 原声、视频里的声音、音频、配音、人声 | `--audio` | `douyin_<id>_audio.m4a`（图文作品为 `_audio.mp3`） |
| 配乐、BGM、背景音乐、这首歌、音乐 | `--music` | `douyin_<id>_music.mp3` |
| 两个都要 | `--audio --music` | 两个文件 |

- `--audio` 从**已下载的 mp4** 里抽音轨：默认 `-c:a copy` 到 m4a，不重编码、几秒完成；
  要 mp3 就加 `--audio-format mp3`（重编码，听感接近但非逐位一致）。
- `--audio` 需要 ffmpeg。没装时会自动退化为下载 `music.play_url`，文件名是 `_audio.mp3`，
  输出里带一行提示。这条音轨是抖音单独存的音乐，**不等于视频里的混音**（可能缺人声/环境声），
  要如实转告用户，别当成原声保证。
- 图文作品没有视频音轨，`--audio` 自动按 `--music` 处理（文件名 `_audio.mp3`）并给出提示。
- 无声视频会明确报「视频没有音轨」，不要拿配乐冒充原声。
- **默认不产出任何音频**：没加参数时目录里不能出现 `.mp3` / `.m4a`。图集也不再自动附 mp3。

### 双击运行（Windows）

随包附带 `下载抖音视频.bat`。双击后在 `> ` 提示处粘贴链接回车即可，文件存到 `downloads\`。
也可以把链接直接拖到 bat 图标上。

## 工作原理（改代码前必读）

抖音的三类作品数据来源不同，别用一个思路套全部：

0. **先试 `window.__pace_f`（React Flight），它同时覆盖视频页和图文页。** `/note/<id>` **完全不发**
   detail 请求，整份作品数据以 camelCase 形式内嵌在 `window.__pace_f` 的分片字符串里；`/video/<id>`
   虽然会发 XHR，但 Flight 里同样有一份。只要 JSON 里能找到整个作品对象，这条路就比拦网络响应更稳
   （不依赖响应缓冲是否还在）。做法：把所有分片字符串拼起来，定位 `"images":[`，往前找
   `{"awemeId"` 作锚点，往后做括号配对（必须处理字符串内的转义引号），切出 JSON 后 `json.loads`。
   字段一律 camelCase：`urlList` / `authorInfo` / `mediaType`，不是 snake_case。

1. **视频页 `/video/<id>` 的网络响应路径。** 页面会发 XHR 到
   `https://www.douyin.com/aweme/v1/web/aweme/detail/`。用 CDP 监听 `Network.responseReceived`，
   一发现该请求**立刻** `Network.getResponseBody`——晚一步浏览器就丢弃缓冲，报
   `No resource with given identifier found`，这是最常踩的坑。这个 JSON 里 `video.bit_rate`
   有十几档清晰度（`normal_1080_0` 等），取最高档即原片。

2. **图文页 `/note/<id>` 的 `author` 是空壳**（见下方陷阱），真名在 `authorInfo`。
   源图 URL 特征是 `~tplv-dy-aweme-images` / `-images-v2`，分辨率即原图。

3. **图集图片**：`images[i]` 里有**两套** URL，内容不同：
   - `url_list` → `tplv-dy-aweme-images-v2` — **无水印**，用这个
   - `download_url_list` → `tplv-dy-water-v10` — **带「抖音号：xxx」水印**，是抖音"保存到相册"给的那版
   取错了就会下到水印版。`_pick_image_urls` 先过滤掉含 `water` 的 URL；
   只有 `url_list` 完全缺失时才退到 `download_url_list`。
   页面 DOM 上的 `440x330` 小图是评论区缩略图，不是作品图，别抓错。

4. **音频只有两条可靠来源。** `video.audio` / `video.bit_rate_audio` 在 detail JSON 里经常是
   `{}` / `null`，别去找"纯音频直链"字段。`--music` 用 `music.play_url.url_list[0]`（mp3，
   原声作品里那条就是原声）；`--audio` 从已下载的 mp4 用 `ffmpeg -vn -c:a copy` 抽音轨，
   抽失败才退回 `music.play_url`。Flight 数据的音乐字段是 camelCase（`playUrl` / `urlList`），
   `douyin_cdp._normalize_camel` 会统一成 snake_case。

5. **动图/实况的动画在 `images[i].video` 里，不在图片 URL 里。** `url_list` 只是首帧；
   `video.play_addr` 是 1–5 秒的 mp4（常带 aac）。普通图集没有 `video` 字段。
   detail 路径是 `video.play_addr.url_list`，Flight 路径是 `video.playAddr: [{src}]`，
   `_normalize_camel` 必须原样保留这个 `video`——1.2.0 之前它被整个丢掉，所以只下到首帧静图。

## 关键陷阱

- **别覆盖 User-Agent。** 用浏览器默认 UA 最稳。抖音对移动端 UA 或陌生指纹更容易回验证码中间页。
- **`/json/new` 必须用 PUT。** 新版 Chrome 的 CDP HTTP 端点创建 target 只接受 PUT，GET 返回 405。
- **不要用 browser-use / browser-harness 驱动 CDP。** 那条链路的 WebSocket 会以
  `no close frame received or sent` 失败，且 `browser-harness.exe` 的 uv trampoline 会报
  `failed to canonicalize script path`。`douyin_cdp.py` 自己实现了 RFC6455 客户端（含掩码与 ping/pong），
  零依赖、也不受这些包装层影响——继续沿用，别为了"省事"换回 heavy 封装。
- **搜索页会被验证码拦。** `/search/<关键词>` 常常直接跳 `验证码中间页`。
  不要试图解验证码——用短链、地址栏 ID 或截图里的 ID 直接解析。
- **部分长视频需要登录。** 页面能打开但播放器不挂源，`fetch_detail` 会明确报错。
  这不是带宽或文件大小问题，如实告诉用户，不要造空文件糊过去。
- **CDN 直链带签名会过期**（URL 里的 `x-expires` / `x-signature`）。
  不要缓存复用直链，每次重新解析。
- **手机端分享链接一样能用。** 「复制打开抖音极速版」那种短链（带 liteapp 追踪参数）跟网页版短链走完全相同的 `v.douyin.com` 重定向，不需要任何特殊处理。不要事先告诉用户"手机端链接可能不行"——直接跑，实测这条路径通畅。
- **图文页的 `author` 是空壳。** `/note/` 的 Flight 数据里 `author` 只有空的 `nickname`/`sec_uid`，真名在 `authorInfo.nickname` 里（`authorInfo.secUid` 对应 sec_uid）。不做这层回退，输出会显示空作者名。
- **图集有两套 URL，别拿错。** `url_list` 是干净原图（`tplv-dy-aweme-images-v2`），
  `download_url_list` 是带水印版（`tplv-dy-water-v10`）。用户说"有水印"时，
  先检查取的是不是 `download_url_list`，不要一上来就去搞图像修补/裁剪——源里本来就有干净的。
- **`url_list` 里的 webp/jpeg 不是动画。** 动图/实况只下 `url_list` 就是一张首帧静图，
  动画在 `images[i].video.play_addr`。用户说"动图不动"时先查这里。
- **`_normalize_camel` 不能丢 `image.video`。** 1.2.0 的修复点：Flight 归一化以前只保留
  `url_list`，把 `video` 整个丢了，于是动图作品稳定退化成静图。
- **别给普通图集造 mp4。** 只有 `images[i].video.play_addr` 存在时才是动图/实况；
  普通图集没有 `video` 字段，别按"每张图都有动画"处理。
- **动图片段通常带音轨。** 保留在 mp4 里；`--to-gif` 会丢音轨，而且 4–5 秒 480px 的 GIF
  单张就有 3–5 MB，只在用户明确要 GIF 时才加。
- **`video.audio` 是空壳，不是音轨地址。** 实测 detail JSON 里它是 `{}`，`bit_rate_audio` 是
  `null`。找不到"纯音频直链"是正常的，改用 `music.play_url` 或从 mp4 抽。
- **用曲作品的 `music.play_url` 可能只有试听片段。** `music.prevent_download` 为真，或下载时长
  明显短于 `music.duration` 时，要告诉用户"只拿到前 XX 秒"，不能当整首交差。原声作品一般
  就是完整时长（`is_original_sound=true`）。
- **静音视频没有音轨。** 有 ffmpeg 时 `ffprobe -select_streams a` 会返回空，这时如实说
  "视频本身没有声音"，不要用配乐冒充原声。
- **`--audio` 默认输出 m4a（无损复制），不是 mp3。** 用户说"要 mp3"时才加
  `--audio-format mp3`；两者听感接近，但 mp3 是重编码，不是逐位一致。
- **v1.1 起图集不再自动附 mp3。** 只有 `--music`（或 `--audio`）才下音频；`--images-only`
  保留但已无实际作用，不要拿它当"关闭音乐"的理由——默认本来就关。
- **图文页图片分辨率不高。** 图集原图常常只有 964×1216、980×922 这种尺寸（作者上传尺寸决定），不是水印或降质。要区分"作者上传尺寸小"和"工具降质"：比对 `images[i].width/height` 与下载后 ffprobe 的尺寸，一致就是原图。

## 耗时参考

固定开销约 10–15 秒（启动浏览器 + 打开页面 + 等数据），与视频长短基本无关：

| 源时长 | 文件大小 | 耗时 |
|---|---|---|
| 5.5 s | 0.96 MB | ~12 s |
| 23.9 s | 7.86 MB | ~10 s |
| 43.3 s | 16.62 MB | ~11 s |

10 秒的视频约 11 秒，60 秒的视频约 15 秒。传输速度 10–20 MB/s，文件大小影响很小。

## 验证清单

改完代码必须实地跑一遍，不能只看输出像不像。**验证要在"用户实际会用的条件"下做**——
把 `scripts/*.py` 拷到一个全新的空目录再跑，不要在原开发目录里跑完就说好了：

```bash
mkdir _verify && cp scripts/*.py _verify/ && cd _verify
python douyin_dl.py "https://v.douyin.com/<short>/"                        # 视频路径
python douyin_dl.py "https://www.douyin.com/note/<id>"                     # 图文路径
python douyin_dl.py "https://v.douyin.com/<short>/" --to-gif               # GIF 路径
python douyin_dl.py "https://v.douyin.com/<short>/" --audio                # 抽音轨
python douyin_dl.py "https://v.douyin.com/<short>/" --music                # 配乐
python douyin_dl.py "https://www.douyin.com/note/<id>" --music             # 图集配乐
python douyin_dl.py "https://v.douyin.com/31jrmGbZcLg/"                    # 动图/实况：6 jpg + 6 mp4
python douyin_dl.py "https://v.douyin.com/31jrmGbZcLg/" --still-only       # 只留静态图
python douyin_dl.py "https://v.douyin.com/31jrmGbZcLg/" --to-gif           # 动图转 GIF
ffprobe -v error -show_entries stream=width,height -of csv=p=0 <图片>       # 确认是原图尺寸
ffprobe -v error -select_streams a -show_entries stream=codec_name -of csv=p=0 <音频>
ffprobe -v error -count_frames -select_streams v:0 -show_entries stream=nb_read_frames -of csv=p=0 <GIF>
```

视频要看到 `[视频]` + 分辨率 ≥ 1080 宽；图集要看到 `[图集]` + 逐张 `jpeg`；
`--audio` 要看到 `_audio.m4a` 且 ffprobe 能读到 `aac` 音轨；`--music` 要看到 `_music.mp3`；
**不带 `--audio` / `--music` 时目录里不能出现 `.mp3` / `.m4a`**。
动图作品要看到 `_01.jpg` 和 `_01.mp4` **成对出现**，mp4 有 h264 视频流（通常还有 aac 音轨）；
普通图集（如 `7707963177069283283`）只有 jpg，**不能多出 `_01.mp4`**；
GIF 的 `nb_read_frames` 必须 > 1。
以上都要 `成功 N/N，失败 0`。跑完删掉 `_verify/`。

`--audio` 还要在"没装 ffmpeg"的条件下跑一次：临时把 PATH 收窄到只剩 Python
（`shutil.which("ffmpeg")` 为 `None`），确认输出出现"已改为下载作品音乐轨"的提示、文件变成
`_audio.mp3`，而不是直接失败或静默产出空文件。

**用户反馈某一项不对（水印、作者名、尺寸、张数）时，先把产物真的看一眼再回答。**
用 `vision_analyze` 带 `region` 裁到底部/角落看水印，用 `ffprobe` 看尺寸，
用原始 JSON 比对字段。不要凭"应该是干净的"就向用户保证——本 skill 里每一条陷阱都是
在"以为没问题、实则取错字段"之后才发现的。

**说"以后不会再出现"之前，必须先从新位置重跑一次并亲眼确认。** 用户会直接追问
"那下次是不是不会了"，答案要建立在刚跑出来的产物上，而不是刚改完的代码上。

## 文件清单

| 文件 | 作用 |
|---|---|
| `scripts/douyin_dl.py` | 主程序：解析链接、挑档、下载、校验、转格式、抽音轨/配乐、取动图片段 |
| `scripts/douyin_cdp.py` | CDP 抓取器：自带 WebSocket 客户端，负责拿到作品 JSON |
| `scripts/下载抖音视频.bat` | Windows 双击入口（ASCII 主体 + GBK + CRLF） |
| `scripts/README.md` | 面向使用者的说明 |
