# 抖音作品下载器 · Douyin Downloader

> 给一个抖音链接，把作品原片存到本地 —— 视频取最高清晰度无水印源，图集取原图，需要时转 GIF。
> 完全本地运行，**零第三方依赖**，同时也是一个可被 Agent 直接加载的 Skill。

![License](https://img.shields.io/badge/license-MIT-blue.svg)
![Python](https://img.shields.io/badge/python-3.8%2B-blue.svg)
![Dependencies](https://img.shields.io/badge/dependencies-none-brightgreen.svg)
![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey.svg)

---

## 特性

- **无水印原片**：视频走 `video.bit_rate` 最高档；图集只用 `url_list` 的干净原图，避开带「抖音号：xxx」水印的 `download_url_list`。
- **零依赖**：只用 Python 标准库，`pip install` 一条都不用。CDP 客户端是自己实现的 RFC6455 WebSocket。
- **三种作品全覆盖**：视频、图文/图集、动图 GIF。
- **健壮解析**：短链、长链、裸作品 ID、整段分享文案都能直接喂进去。
- **可编程**：`--json` 输出结构化结果，方便别的程序调用。
- **可批量**：`--batch links.txt` 一行一个链接。

---

## 支持的链接

| 形式 | 示例 |
|---|---|
| 分享短链 | `https://v.douyin.com/5XAQS3L98Lk/` |
| 视频长链 | `https://www.douyin.com/video/7672606966908412657` |
| 图集长链 | `https://www.douyin.com/note/7697265979428200050` |
| 裸作品 ID | `7672606966908412657` |
| 整段分享文案 | 「7.17 复制此链接打开抖音 https://v.douyin.com/XXXX/ …」 |

分享文案不用手动挑链接，整段粘进来就会自动提取。

---

## 安装

### 1. 拿到代码

```bash
git clone https://github.com/wuxutian2-bot/douyin-downloader.git
cd douyin-downloader/scripts
```

> `douyin_dl.py` 和 `douyin_cdp.py` **必须放在同一个文件夹**，前者通过 `__file__` 定位后者。

### 2. 准备浏览器（必须）

需要一个 Chromium 内核浏览器 —— Chrome、Edge 或 Chromium 之一。Windows 一般自带 Edge，可以直接用。

找不到时用环境变量指定：

```bat
:: Windows (cmd)
set CHROME_PATH=D:\Chrome\chrome.exe
```

```bash
# Linux / macOS
export CHROME_PATH=/usr/bin/google-chrome
```

### 3. 安装 ffmpeg（可选）

用于 webp 转 JPG、视频转 GIF，以及下载后的时长校验。

```bash
winget install ffmpeg     # Windows
brew install ffmpeg       # macOS
sudo apt install ffmpeg   # Debian/Ubuntu
```

没装也能跑，只是 webp 原图保持原格式、`--to-gif` 不可用、校验退化为读文件头。

---

## 用法

### 双击运行（Windows，最简单）

双击 `scripts/下载抖音视频.bat`，在 `> ` 提示处粘贴链接回车，文件存到 `scripts/downloads/`。
也可以把链接直接拖到 bat 图标上。

### 命令行（所有平台）

```bash
# 最常用：粘贴链接或整段分享文案
python douyin_dl.py "https://v.douyin.com/XXXX/"
python douyin_dl.py "7.17 复制此链接打开抖音 https://v.douyin.com/XXXX/ ..."

# 落地到指定目录
python douyin_dl.py "<链接>" -o D:\my\videos

# 图集（自动逐张下载，webp 转 JPG）
python douyin_dl.py "https://www.douyin.com/note/7697265979428200050"

# 视频额外输出一份 GIF（12fps / 480px 宽）
python douyin_dl.py "<链接>" --to-gif

# 只要图片，不要图集附带的背景音乐
python douyin_dl.py "<链接>" --images-only

# 指定清晰度
python douyin_dl.py "<链接>" -q 720

# 批量：文件里一行一个链接或整段文案
python douyin_dl.py --batch links.txt -o downloads

# 给程序调用
python douyin_dl.py "<链接>" --json
```

### 产出文件

| 作品类型 | 产出 |
|---|---|
| 视频 | `douyin_<id>.mp4` — 最高清晰度，无水印 |
| 图集 | `douyin_<id>_01.jpg`、`_02.jpg` … — 原图，按张编号 |
| 图集带音乐 | 额外 `douyin_<id>.mp3` |
| `--to-gif` | 额外 `douyin_<id>.gif` |

---

## 作为 Agent Skill 使用

本仓库根目录的 [`SKILL.md`](SKILL.md) 就是 skill 定义（含 YAML frontmatter、适用场景、工作原理与踩坑清单）。
把整个仓库放进你的 skills 目录即可被支持 Skills 的 Agent 加载，例如：

```bash
git clone https://github.com/wuxutian2-bot/douyin-downloader.git \
  ~/.claude/skills/douyin-downloader
```

之后直接对 Agent 说「帮我把这个抖音链接的视频存下来」就会命中该 skill。

---

## 工作原理（改代码前必读）

抖音三类作品的数据来源不同，不能用一个思路套全部。完整说明见 [`SKILL.md`](SKILL.md)，核心三点：

1. **优先解析 `window.__pace_f`（React Flight）** —— 它同时覆盖 `/video/<id>` 和 `/note/<id>`，`/note/` 页甚至不发 detail 请求。字段是 camelCase（`urlList` / `authorInfo` / `mediaType`）。
2. **视频页可走 CDP 网络响应** —— 监听 `Network.responseReceived`，一发现 `aweme/v1/web/aweme/detail/` 立刻 `getResponseBody`，晚一步浏览器就丢弃缓冲。
3. **图集有两套 URL** —— `url_list` 是干净原图，`download_url_list` 是水印版，取错就会下到带水印的图。

常见踩坑（UA、`/json/new` 必须 PUT、验证码页、CDN 直链会过期、`/note/` 的 `author` 是空壳等）都写在 `SKILL.md` 里了。

---

## 常见问题

**提示「找不到 Chrome / Edge / Chromium」**
装个 Chrome，或用 `CHROME_PATH` 指定路径。

**提示「作品需要登录才能查看」**
部分长视频要登录才给播，这是抖音的限制，不是工具问题。

**提示「触发了验证码」**
短时间请求太频繁，等几分钟再试或换个网络。不要试图解验证码 —— 用短链或作品 ID 直接解析。

**图集只下到 1 张**
作品本身就只有 1 张图，数量以作品实际为准。

**下载的图片打不开 / 有水印**
装 ffmpeg 会自动把 webp 转 JPG。若带水印，说明取到了 `download_url_list`，检查 `_pick_image_urls` 的过滤逻辑。

**耗时多久？**
固定开销约 10–15 秒（启动浏览器 + 打开页面 + 拿数据），跟视频长短基本无关。

---

## 项目结构

```
douyin-downloader/
├── SKILL.md                 # Agent Skill 定义（适用场景 / 原理 / 踩坑清单）
├── README.md                # 本文件
├── LICENSE                  # MIT
└── scripts/
    ├── douyin_dl.py         # 主程序：解析链接、挑档、下载、校验、转格式
    ├── douyin_cdp.py        # CDP 抓取器：自带 WebSocket 客户端，负责拿到作品 JSON
    ├── 下载抖音视频.bat      # Windows 双击入口
    └── README.md            # 面向使用者的快速说明
```

---

## 免责声明

本项目仅供**个人学习与技术研究**使用。请在使用时遵守抖音的用户协议与相关法律法规，
尊重原作者版权，**不要**用于批量抓取、二次分发或任何商业用途。
下载内容的版权归原权利人所有，使用者需自行承担因使用本项目产生的一切责任。

---

## License

[MIT](LICENSE) © muxia
