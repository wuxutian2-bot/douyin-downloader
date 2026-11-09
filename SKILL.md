---
name: douyin-downloader
description: "Douyin link to video/album/GIF: local tool."
version: 1.0.0
author: tibo
license: MIT
platforms: [windows, linux, macos]
metadata:
  hermes:
    tags: [Douyin, 抖音, Video, Image, Album, GIF, Download, Chrome, CDP]
    related_skills: [video-download]
---

# 抖音作品下载器（视频 / 图集 / GIF）

给一个抖音链接，把作品的原片存到本地：视频取最高清晰度无水印源，图集取原图（自动转 JPG），
需要时把视频转成 GIF。完全本地运行，不依赖任何第三方 Python 包。

## When to Use

用户粘贴抖音链接（短链 `v.douyin.com/xxx`、`/video/<id>`、`/note/<id>`、裸 19 位 ID，
或整段分享文案）并想要其中的视频、图片、图集或 GIF。也用于用户问"这个抖音链接能下吗"、
"帮我存下来"、"要原图"、"转个 GIF"。非抖音平台（YouTube/TikTok/B站）用 `video-download`。

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
3. 可选：装 ffmpeg，用于图片转 JPG、视频转 GIF、以及下载后的时长校验。
   ```bash
   winget install ffmpeg     # Windows
   brew install ffmpeg       # macOS
   sudo apt install ffmpeg   # Debian/Ubuntu
   ```
   没装也能跑，只是 webp 原图保持原格式、`--to-gif` 不可用、校验退化为读文件头。

## 用法

```bash
# 最常用：粘贴链接或整段分享文案
python douyin_dl.py "https://v.douyin.com/XXXX/"
python douyin_dl.py "7.17 复制此链接打开抖音 https://v.douyin.com/XXXX/ ..."

# 落地到指定目录
python douyin_dl.py "<链接>" -o D:\my\videos

# 图集（自动逐张下载，webp 转 JPG）
python douyin_dl.py "https://www.douyin.com/note/7697265979428200050"

# 视频额外转一份 GIF（12fps / 480px 宽）
python douyin_dl.py "<链接>" --to-gif

# 只要图片、不要图集附带的背景音乐
python douyin_dl.py "<链接>" --images-only

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
| 图集带音乐 | 额外 `douyin_<id>.mp3` |
| `--to-gif` | 额外 `douyin_<id>.gif` |

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
ffprobe -v error -show_entries stream=width,height -of csv=p=0 <图片>       # 确认是原图尺寸
```

视频要看到 `[视频]` + 分辨率 ≥ 1080 宽；图集要看到 `[图集]` + 逐张 `jpeg`；
两者都要 `成功 N/N，失败 0`。跑完删掉 `_verify/`。

**用户反馈某一项不对（水印、作者名、尺寸、张数）时，先把产物真的看一眼再回答。**
用 `vision_analyze` 带 `region` 裁到底部/角落看水印，用 `ffprobe` 看尺寸，
用原始 JSON 比对字段。不要凭"应该是干净的"就向用户保证——本 skill 里每一条陷阱都是
在"以为没问题、实则取错字段"之后才发现的。

**说"以后不会再出现"之前，必须先从新位置重跑一次并亲眼确认。** 用户会直接追问
"那下次是不是不会了"，答案要建立在刚跑出来的产物上，而不是刚改完的代码上。

## 文件清单

| 文件 | 作用 |
|---|---|
| `scripts/douyin_dl.py` | 主程序：解析链接、挑档、下载、校验、转格式 |
| `scripts/douyin_cdp.py` | CDP 抓取器：自带 WebSocket 客户端，负责拿到作品 JSON |
| `scripts/下载抖音视频.bat` | Windows 双击入口（ASCII 主体 + GBK + CRLF） |
| `scripts/README.md` | 面向使用者的说明 |
