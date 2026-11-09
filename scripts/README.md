# 抖音作品下载器

给一个抖音链接，把作品原片存到本地。支持**视频**、**图集**、**动图 GIF**。
纯 Python 标准库 + 本机浏览器，不需要 `pip install` 任何东西。

---

## 快速开始

### Windows

1. 双击 **`下载抖音视频.bat`**
2. 在 `> ` 后面粘贴抖音链接，按回车
3. 文件存到 `downloads\` 文件夹

也可以把链接直接拖到 bat 图标上。

### 命令行（所有平台）

```bash
python douyin_dl.py "https://v.douyin.com/XXXX/"
```

---

## 支持的链接

| 形式 | 示例 |
|---|---|
| 分享短链 | `https://v.douyin.com/5XAQS3L98Lk/` |
| 视频长链 | `https://www.douyin.com/video/7672606966908412657` |
| 图集长链 | `https://www.douyin.com/note/7697265979428200050` |
| 裸作品 ID | `7672606966908412657` |
| 整段分享文案 | 「7.17 复制此链接打开抖音 https://v.douyin.com/XXXX/ …」 |

分享文案不用手动挑链接，整段粘进来就行，会自动提取。

---

## 常用参数

```bash
# 指定输出目录
python douyin_dl.py "<链接>" -o D:\我的视频

# 视频额外转一份 GIF
python douyin_dl.py "<链接>" --to-gif

# 指定清晰度（默认最高）
python douyin_dl.py "<链接>" -q 720

# 图集只要图片，不要背景音乐
python douyin_dl.py "<链接>" --images-only

# 批量：links.txt 里一行一个
python douyin_dl.py --batch links.txt

# JSON 输出（给别的程序调用）
python douyin_dl.py "<链接>" --json
```

---

## 产出文件

| 作品类型 | 文件 |
|---|---|
| 视频 | `douyin_<id>.mp4` — 最高清晰度，无水印 |
| 图集 | `douyin_<id>_01.jpg`、`_02.jpg` … — 原图，自动从 webp 转 JPG |
| 图集带音乐 | 额外 `douyin_<id>.mp3` |
| `--to-gif` | 额外 `douyin_<id>.gif` |

---

## 环境准备

**必须有**：Chrome、Edge 或 Chromium 之一（Windows 一般自带 Edge）。
找不到时设置环境变量：

```bat
set CHROME_PATH=D:\Chrome\chrome.exe
```

```bash
export CHROME_PATH=/usr/bin/google-chrome
```

**可选**：ffmpeg。用于 webp 转 JPG、视频转 GIF、以及下载后校验。
```bash
winget install ffmpeg    # Windows
brew install ffmpeg      # macOS
sudo apt install ffmpeg  # Debian/Ubuntu
```
没装也能用，只是 webp 图保持原格式、`--to-gif` 不可用。

---

## 耗时

固定开销约 10–15 秒（启动浏览器 + 打开页面 + 拿数据），跟视频长短基本无关：

| 源时长 | 文件大小 | 耗时 |
|---|---|---|
| 5.5 秒 | 0.96 MB | ~12 秒 |
| 23.9 秒 | 7.86 MB | ~10 秒 |
| 43.3 秒 | 16.62 MB | ~11 秒 |

10 秒的视频约 11 秒，60 秒的视频约 15 秒。

---

## 常见问题

**提示「找不到 Chrome / Edge / Chromium」**
装个 Chrome，或者用 `CHROME_PATH` 指定路径。

**提示「作品需要登录才能查看」**
部分长视频要登录才给播。这不是工具的问题，抖音本身限制了匿名访问。

**提示「触发了验证码」**
短时间内请求太频繁。等几分钟再试，或者换个网络。

**图集只下到 1 张**
作品本身就只有 1 张图。图片数量以作品实际为准。

**下载的图片打不开**
装 ffmpeg，工具会自动把 webp 转成 JPG。

---

## 两个文件必须放一起

`douyin_dl.py` 和 `douyin_cdp.py` 必须放在**同一个文件夹**里，
前者靠 `__file__` 定位后者。分开就报「缺少 douyin_cdp.py」。
