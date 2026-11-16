#!/usr/bin/env python3
"""douyin_dl.py — 抖音作品下载器（视频 / 图集 / 动图 GIF / 音频）

给一个抖音链接，把作品原片存到本地。只依赖 Python 标准库 + 本机 Chrome/Edge。

用法：
    python douyin_dl.py "<链接或整段分享文案>"
    python douyin_dl.py --batch links.txt -o downloads
    python douyin_dl.py <链接> --json
    python douyin_dl.py <链接> --to-gif        # 视频转 GIF
    python douyin_dl.py <链接> -q 720          # 指定清晰度
    python douyin_dl.py <链接> --audio         # 额外导出视频原声（需 ffmpeg）
    python douyin_dl.py <链接> --music         # 额外下载配乐/音乐（mp3）
    python douyin_dl.py <链接> --audio --music # 两个都要

支持的输入：
    https://v.douyin.com/XXXX/                   分享短链
    https://www.douyin.com/video/<id>            视频长链
    https://www.douyin.com/note/<id>             图集长链
    <19位数字>                                    裸作品 ID
    整段分享文案（自动从中提取链接）

产出：
    视频  → douyin_<id>.mp4          （最高清晰度，无水印）
    图集  → douyin_<id>_01.jpg …     （原图，按张顺序编号）
    音频  → douyin_<id>_audio.m4a    （--audio，从视频抽出的原声，默认无损复制）
    配乐  → douyin_<id>_music.mp3    （--music，作品自带音乐轨）
    GIF   → douyin_<id>.gif          （--to-gif）

音频/配乐默认**不下载**，只有显式加 --audio / --music 才产出。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from typing import Iterable

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

try:
    import douyin_cdp
except ImportError:
    douyin_cdp = None

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
UA_MOBILE = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
             "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1")

QUALITY_TIERS = {
    "1080": ("normal_1080", "1080"),
    "720": ("normal_720", "720"),
    "540": ("normal_540", "540"),
    "480": ("normal_480", "480"),
    "360": ("normal_360", "360"),
}


class DouyinError(RuntimeError):
    pass


# ------------------------------------------------------------------ 输入解析

def extract_targets(text: str) -> list[str]:
    """从任意文本里抽取短链 / 长链 / 裸 ID，去重保序。"""
    found: list[str] = []
    found += re.findall(r"https?://v\.douyin\.com/[A-Za-z0-9_-]+", text)
    found += [m.group(0) for m in re.finditer(
        r"https?://(?:www\.)?douyin\.com/(?:video|note)/\d{15,25}", text)]
    found += [m.group(1) for m in re.finditer(
        r"https?://(?:www\.)?iesdouyin\.com/share/(?:video|note)/(\d{15,25})", text)]
    found += [m.group(1) for m in re.finditer(r"(?<![\d/])(\d{19})(?![\d])", text)]
    seen, out = set(), []
    for f in found:
        if f not in seen:
            seen.add(f)
            out.append(f)
    return out


# ------------------------------------------------------------------ HTTP

try:
    import requests
    _SESSION = None

    def http_get(url: str, referer: str = "https://www.douyin.com/",
                 stream: bool = False, timeout: int = 60):
        global _SESSION
        if _SESSION is None:
            _SESSION = requests.Session()
            _SESSION.headers.update({"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9"})
        return _SESSION.get(url, headers={"User-Agent": UA, "Referer": referer},
                            stream=stream, timeout=timeout, allow_redirects=True)

except ImportError:  # 没有 requests 时退回 urllib
    import urllib.request

    class _Resp:
        def __init__(self, r):
            self.status_code = r.status
            self.headers = r.headers
            self._r = r

        def iter_content(self, n):
            while True:
                chunk = self._r.read(n)
                if not chunk:
                    return
                yield chunk

        def close(self):
            self._r.close()

        @property
        def text(self):
            return self._r.read().decode("utf-8", "replace")

    def http_get(url: str, referer: str = "https://www.douyin.com/",
                 stream: bool = False, timeout: int = 60):
        req = urllib.request.Request(url, headers={
            "User-Agent": UA, "Referer": referer, "Accept-Language": "zh-CN,zh;q=0.9"})
        return _Resp(urllib.request.urlopen(req, timeout=timeout))


# ------------------------------------------------------------------ 下载

def _download_url(url: str, out_path: str, referer: str = "https://www.douyin.com/") -> int:
    """流式下载单个文件，返回字节数。会校验内容不是错误页。"""
    r = http_get(url, referer=referer, stream=True)
    try:
        status = getattr(r, "status_code", 200)
        if status != 200:
            raise DouyinError(f"下载失败 HTTP {status}: {url[:80]}")
        ctype = r.headers.get("Content-Type", "")
        clen = int(r.headers.get("Content-Length") or 0)
        if clen and clen < 1024 and "text" in ctype:
            raise DouyinError(f"拿到的是错误页而不是文件: {url[:80]}")
        written = 0
        with open(out_path, "wb") as f:
            for chunk in r.iter_content(1 << 16):
                if chunk:
                    f.write(chunk)
                    written += len(chunk)
        if written == 0:
            raise DouyinError(f"下载到 0 字节: {url[:80]}")
        return written
    finally:
        try:
            r.close()
        except Exception:  # noqa: BLE001
            pass


def _pick_video_url(ad: dict, quality: str = "best") -> tuple[str, dict]:
    """从 aweme_detail 里挑视频直链。优先 bit_rate 档位表（多清晰度）。"""
    video = ad.get("video") or {}
    tiers = video.get("bit_rate") or []

    cands: list[dict] = []
    for t in tiers:
        pa = t.get("play_addr") or {}
        if pa.get("url_list"):
            cands.append({
                "urls": pa["url_list"],
                "gear": t.get("gear_name") or "",
                "br": t.get("bit_rate") or 0,
                "w": pa.get("width") or 0,
                "h": pa.get("height") or 0,
            })
    main_pa = video.get("play_addr") or {}
    if main_pa.get("url_list"):
        cands.append({
            "urls": main_pa["url_list"], "gear": "play_addr",
            "br": main_pa.get("data_size") or 0,
            "w": main_pa.get("width") or 0, "h": main_pa.get("height") or 0,
        })
    if not cands:
        raise DouyinError("作品里没有可用的视频直链")

    def area(c):
        return (c["w"] or 0) * (c["h"] or 0)

    if quality == "best":
        # 优先最高分辨率的 normal_ 档，其次按面积，最后按码率
        cands.sort(key=lambda c: (c["gear"].startswith("normal_"), area(c), c["br"]), reverse=True)
    else:
        want = QUALITY_TIERS.get(quality, ("", ""))[1]
        exact = [c for c in cands if f"_{want}_" in c["gear"] or c["gear"].endswith(f"_{want}_0")]
        pool = exact or [c for c in cands if (c["h"] or 0) <= int(want)] or cands
        pool.sort(key=lambda c: (area(c), c["br"]), reverse=True)
        cands = pool

    chosen = cands[0]
    # 优先 watermark=0 的地址（无水印）
    urls = sorted(chosen["urls"], key=lambda u: 0 if "watermark=0" in u else 1)
    return urls[0].replace("http://", "https://"), chosen


def _pick_image_urls(ad: dict) -> list[str]:
    """从图集作品里取每张图的无水印原图地址。

    抖音给两套 URL：
      - url_list          → `tplv-dy-aweme-images-v2`  **无水印**
      - download_url_list → `tplv-dy-water-v10`        **带「抖音号：xxx」水印**
    所以绝不能用 download_url_list 兜底，否则默认下到的是水印版。
    """
    imgs = ad.get("images") or ad.get("image_list") or []
    urls: list[str] = []
    for entry in imgs:
        if not isinstance(entry, dict):
            continue
        clean = [u for u in (entry.get("url_list") or []) if "water" not in u.lower()]
        pool = clean or [u for u in (entry.get("url_list") or []) if u]
        if not pool:
            # 没有 url_list 才退到 download_url_list（会带水印，但总比没有强）
            pool = entry.get("download_url_list") or []
        if not pool:
            continue
        # 同一条图的不同 CDN 节点内容一致，优先 jpeg 便于后续处理
        ranked = sorted(pool, key=lambda u: (
            0 if "water" not in u.lower() else 1,
            0 if ".jpeg" in u.lower() else 1,
        ))
        urls.append(ranked[0].replace("http://", "https://"))
    return urls


def _as_url_list(node) -> list[str]:
    """把 url_list / urlList / 裸字符串 / 字符串数组统一成 URL 列表。"""
    if isinstance(node, str):
        return [node]
    if isinstance(node, list):
        return [u for u in node if isinstance(u, str)]
    if isinstance(node, dict):
        for key in ("url_list", "urlList"):
            v = node.get(key)
            if isinstance(v, list):
                return [u for u in v if isinstance(u, str)]
            if isinstance(v, str):
                return [v]
    return []


def _pick_music_url(ad: dict) -> str | None:
    """作品自带的音乐轨（原声或配乐）。

    视频页 detail 是 snake_case，图文页 Flight 数据是 camelCase，两种都要认。
    注意：`video.audio` / `video.bit_rate_audio` 经常是空对象，别指望有独立音轨地址。
    """
    music = ad.get("music") or {}
    for key in ("play_url", "playUrl", "play_url_h265", "playUrlH265"):
        for u in _as_url_list(music.get(key)):
            if u.strip():
                return u.replace("http://", "https://")
    return None


def _music_meta(ad: dict) -> dict:
    """音乐轨的元信息，用于提示试听片段和输出展示。"""
    music = ad.get("music") or {}

    def first(*names, default=None):
        for n in names:
            v = music.get(n)
            if v not in (None, ""):
                return v
        return default

    return {
        "title": first("title", default="") or "",
        "author": first("author", default="") or "",
        "duration": int(first("duration", default=0) or 0),
        "audition": int(first("audition_duration", "auditionDuration", default=0) or 0),
        "is_original_sound": bool(first("is_original_sound", "isOriginalSound", default=False)),
        "prevent_download": bool(first("prevent_download", "preventDownload", default=False)),
    }


# ------------------------------------------------------------------ 校验

def probe(path: str) -> dict:
    """ffprobe 校验（没有 ffprobe 时退回文件头判断）。"""
    if shutil.which("ffprobe"):
        try:
            out = subprocess.run(
                ["ffprobe", "-v", "error", "-print_format", "json",
                 "-show_format", "-show_streams", path],
                capture_output=True, text=True, timeout=60)
            if out.returncode == 0:
                d = json.loads(out.stdout)
                fmt = d.get("format", {})
                vs = next((s for s in d.get("streams", []) if s.get("codec_type") == "video"), {})
                au = next((s for s in d.get("streams", []) if s.get("codec_type") == "audio"), {})
                return {
                    "ok": bool(vs.get("codec_name")),
                    "duration": round(float(fmt.get("duration", 0) or 0), 3),
                    "size": int(fmt.get("size", 0) or 0),
                    "codec": vs.get("codec_name"),
                    "width": vs.get("width"),
                    "height": vs.get("height"),
                    "fps": vs.get("r_frame_rate"),
                    "audio": au.get("codec_name"),
                }
        except Exception:  # noqa: BLE001
            pass

    # 无 ffprobe：只看文件头
    try:
        with open(path, "rb") as f:
            head = f.read(32)
        size = os.path.getsize(path)
        ok = (b"ftyp" in head) or size > 10_000
        return {"ok": ok, "size": size, "check": "header-only"}
    except OSError as e:
        return {"ok": False, "error": str(e)}


def _has_audio_stream(path: str) -> bool | None:
    """视频里有没有音轨。None = 没有 ffprobe，无法判断。"""
    if not shutil.which("ffprobe"):
        return None
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a",
             "-show_entries", "stream=codec_name", "-of", "csv=p=0", path],
            capture_output=True, text=True, timeout=60)
        if out.returncode == 0:
            return bool(out.stdout.strip())
    except Exception:  # noqa: BLE001
        pass
    return None


def probe_audio(path: str) -> dict:
    """音频校验：有 ffprobe 就读真实编码/时长，没有就看文件头。"""
    if shutil.which("ffprobe"):
        try:
            out = subprocess.run(
                ["ffprobe", "-v", "error", "-print_format", "json",
                 "-show_format", "-show_streams", path],
                capture_output=True, text=True, timeout=60)
            if out.returncode == 0:
                d = json.loads(out.stdout)
                au = next((s for s in d.get("streams", [])
                           if s.get("codec_type") == "audio"), {})
                fmt = d.get("format", {})
                if au:
                    return {
                        "ok": True,
                        "codec": au.get("codec_name"),
                        "duration": round(float(fmt.get("duration", 0) or 0), 2),
                        "size": int(fmt.get("size", 0) or 0),
                        "bit_rate": int(fmt.get("bit_rate", 0) or 0),
                    }
                return {"ok": False, "error": "文件里没有音频流"}
        except Exception:  # noqa: BLE001
            pass

    # 无 ffprobe：只认文件头
    try:
        with open(path, "rb") as f:
            head = f.read(16)
        size = os.path.getsize(path)
    except OSError as e:
        return {"ok": False, "error": str(e)}
    kind = None
    if head[:3] == b"ID3" or (head[:1] == b"\xff" and head[1:2] and head[1] & 0xE0 == 0xE0):
        kind = "mp3"
    elif b"ftyp" in head[:12]:
        kind = "m4a"
    elif head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        kind = "wav"
    elif head[:4] == b"OggS":
        kind = "ogg"
    return {"ok": bool(kind), "format": kind, "size": size, "check": "header-only"}


def extract_audio(src_mp4: str, out_base: str, fmt: str = "m4a") -> dict:
    """从视频里抽出音轨。

    默认 m4a + `-c:a copy`：不重编码、秒出，音质和视频里完全一致。
    fmt=mp3 时用 libmp3lame 重编码；源不是 AAC 时 m4a 会退到 AAC 重编码。
    """
    if not shutil.which("ffmpeg"):
        raise DouyinError("提取音频需要 ffmpeg，请先安装：winget install ffmpeg")
    fmt = "mp3" if str(fmt).lower() == "mp3" else "m4a"
    if fmt == "mp3":
        out = out_base + ".mp3"
        cmds = [
            ["ffmpeg", "-y", "-i", src_mp4, "-vn", "-c:a", "libmp3lame", "-q:a", "2", out],
            ["ffmpeg", "-y", "-i", src_mp4, "-vn", "-c:a", "mp3", "-b:a", "192k", out],
        ]
    else:
        out = out_base + ".m4a"
        cmds = [
            ["ffmpeg", "-y", "-i", src_mp4, "-vn", "-c:a", "copy",
             "-movflags", "+faststart", out],
            ["ffmpeg", "-y", "-i", src_mp4, "-vn", "-c:a", "aac", "-b:a", "192k", out],
        ]
    last = ""
    for cmd in cmds:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if r.returncode == 0 and os.path.exists(out) and os.path.getsize(out) > 1024:
            return {"path": out, "bytes": os.path.getsize(out), "format": fmt}
        last = (r.stderr or "")[-300:]
    try:
        os.remove(out)          # 两次都失败时别留下半截文件
    except OSError:
        pass
    raise DouyinError(f"ffmpeg 提取音频失败: {last}")


def download_music(ad: dict, out_dir: str, aweme_id: str, kind: str = "music") -> dict:
    """下载作品自带的音乐轨。kind="music" 存 _music.mp3，kind="audio" 存 _audio.mp3。"""
    meta = _music_meta(ad)
    url = _pick_music_url(ad)
    if not url:
        raise DouyinError("作品没有可用的音乐轨（music.play_url 为空）")
    ext = ".mp3"
    low = url.lower().split("?")[0]
    for cand in (".m4a", ".aac", ".wav", ".ogg"):
        if low.endswith(cand):
            ext = cand
            break
    out = os.path.join(out_dir, f"douyin_{aweme_id}_{kind}{ext}")
    size = _download_url(url, out)
    info = probe_audio(out)
    if not info.get("ok"):
        try:
            os.remove(out)
        except OSError:
            pass
        raise DouyinError("下载到的音乐不是有效音频（可能拿到错误页或被限制下载）")
    return {
        "path": os.path.abspath(out), "bytes": size, "kind": kind,
        "title": meta["title"], "music_meta": meta,
        "format": info.get("format") or ext.lstrip("."), **info,
    }


def _sniff_image(path: str) -> dict:
    """图片校验：读魔数确认是真图片，不是 HTML 错误页。"""
    try:
        with open(path, "rb") as f:
            head = f.read(16)
        size = os.path.getsize(path)
    except OSError as e:
        return {"ok": False, "error": str(e)}
    kind = None
    if head[:3] == b"\xff\xd8\xff":
        kind = "jpeg"
    elif head[:8] == b"\x89PNG\r\n\x1a\n":
        kind = "png"
    elif head[:6] in (b"GIF87a", b"GIF89a"):
        kind = "gif"
    elif head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        kind = "webp"
    return {"ok": bool(kind), "format": kind, "size": size}


def to_gif(src_mp4: str, out_gif: str, fps: int = 12, width: int = 480) -> dict:
    """把视频转成 GIF（需要 ffmpeg）。"""
    if not shutil.which("ffmpeg"):
        raise DouyinError("转 GIF 需要 ffmpeg，请先安装：winget install ffmpeg")
    vf = (f"fps={fps},scale={width}:-1:flags=lanczos,"
          "split[a][b];[a]palettegen=max_colors=128[p];[b][p]paletteuse=dither=bayer")
    r = subprocess.run(
        ["ffmpeg", "-y", "-i", src_mp4, "-vf", vf, "-loop", "0", out_gif],
        capture_output=True, text=True, timeout=600)
    if r.returncode != 0:
        raise DouyinError(f"ffmpeg 转 GIF 失败: {r.stderr[-300:]}")
    return {"path": out_gif, "bytes": os.path.getsize(out_gif)}


# ------------------------------------------------------------------ 主流程

def to_jpg(src: str, out_jpg: str) -> dict | None:
    """webp/heic 原图转成 JPG（有 ffmpeg 才转，没有就保留原格式）。"""
    if not shutil.which("ffmpeg"):
        return None
    r = subprocess.run(
        ["ffmpeg", "-y", "-i", src, "-q:v", "2", out_jpg],
        capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        return None
    return {"path": out_jpg, "bytes": os.path.getsize(out_jpg)}


def _audio_from_video(ad: dict, video_path: str, video_info: dict,
                      out_dir: str, aweme_id: str, audio_format: str,
                      notes: list[str]) -> dict:
    """从已下载的 mp4 抽音轨；抽不了再退回作品音乐轨。返回 file 记录。"""
    has_audio = _has_audio_stream(video_path)
    if has_audio is False:
        notes.append("这条视频本身没有音轨（无声视频），无法提取音频")
        return {"path": None, "kind": "audio", "ok": False,
                "error": "视频没有音轨"}

    if not shutil.which("ffmpeg"):
        if _pick_music_url(ad):
            notes.append("没装 ffmpeg，无法从视频抽音轨，已改为下载作品音乐轨")
            try:
                return download_music(ad, out_dir, aweme_id, "audio")
            except DouyinError as e:
                return {"path": None, "kind": "audio", "ok": False, "error": str(e)}
        return {"path": None, "kind": "audio", "ok": False,
                "error": "提取音频需要 ffmpeg：winget install ffmpeg"}

    try:
        base = os.path.splitext(video_path)[0] + "_audio"
        res = extract_audio(video_path, base, audio_format)
        info = probe_audio(res["path"])
        if not info.get("ok"):
            raise DouyinError("提取出的音频文件无效")
        if video_info.get("duration") and info.get("duration"):
            diff = abs(float(video_info["duration"]) - float(info["duration"]))
            if diff > 2:
                notes.append(
                    f"音频 {info['duration']}s 与视频 {video_info['duration']}s 不一致，"
                    "请检查是否截断")
        return {"path": os.path.abspath(res["path"]), "bytes": res["bytes"],
                "kind": "audio", "format": res["format"], **info}
    except DouyinError as e:
        if not _pick_music_url(ad):
            return {"path": None, "kind": "audio", "ok": False, "error": str(e)}
        notes.append(f"{e}；已改为下载作品音乐轨")
        try:
            return download_music(ad, out_dir, aweme_id, "audio")
        except DouyinError as e2:
            return {"path": None, "kind": "audio", "ok": False, "error": str(e2)}


def fetch_one(target: str, out_dir: str, quality: str = "best",
              make_gif: bool = False, images_only: bool = False,
              video_only: bool = False, prefer_jpg: bool = True,
              want_audio: bool = False, want_music: bool = False,
              audio_format: str = "m4a") -> dict:
    if douyin_cdp is None:
        raise DouyinError("缺少 douyin_cdp.py，请确保它和本脚本在同一个文件夹")

    ad = douyin_cdp.fetch_detail(target)
    aweme_id = ad.get("aweme_id") or "unknown"
    desc = (ad.get("desc") or "").strip()
    author = (ad.get("author") or {}).get("nickname") or ""

    os.makedirs(out_dir, exist_ok=True)
    files: list[dict] = []
    notes: list[str] = []
    kind = "unknown"

    # ---- 图集 ----
    image_urls = _pick_image_urls(ad)
    if image_urls:
        kind = "images"
        for idx, url in enumerate(image_urls, 1):
            ext = "jpg"
            low = url.lower()
            for cand, e in ((".png", "png"), (".webp", "webp"), (".gif", "gif")):
                if cand in low:
                    ext = e
                    break
            path = os.path.join(out_dir, f"douyin_{aweme_id}_{idx:02d}.{ext}")
            try:
                size = _download_url(url, path)
                info = _sniff_image(path)
                if not info.get("ok"):
                    raise DouyinError(f"第 {idx} 张不是有效图片（可能是错误页）")
                # webp 转 JPG：本地播放器/看图软件对 webp 支持参差，JPG 更省事
                if prefer_jpg and info.get("format") in ("webp", "heic"):
                    jpg_path = os.path.splitext(path)[0] + ".jpg"
                    conv = to_jpg(path, jpg_path)
                    if conv:
                        os.remove(path)
                        path = jpg_path
                        size = conv["bytes"]
                        info = _sniff_image(path)
                files.append({"path": os.path.abspath(path), "bytes": size,
                              "index": idx, **info})
            except DouyinError as e:
                files.append({"path": None, "index": idx, "ok": False, "error": str(e)})

        # 图文作品没有视频音轨：--audio 等价于 --music
        if want_audio and not want_music:
            notes.append("图文作品没有视频音轨，--audio 已改为保存作品配乐")
        if want_music or want_audio:
            wanted = "music" if want_music else "audio"
            try:
                files.append(download_music(ad, out_dir, aweme_id, wanted))
            except DouyinError as e:
                files.append({"path": None, "kind": wanted, "ok": False, "error": str(e)})

    # ---- 视频 ----
    if not image_urls or (video_only and ad.get("video")):
        if ad.get("video", {}).get("play_addr"):
            kind = "video"
            url, tier = _pick_video_url(ad, quality)
            path = os.path.join(out_dir, f"douyin_{aweme_id}.mp4")
            size = _download_url(url, path)
            info = probe(path)
            files.append({
                "path": os.path.abspath(path), "bytes": size, "kind": "video",
                "gear": tier["gear"], "src_w": tier["w"], "src_h": tier["h"],
                **info,
            })
            if make_gif:
                gif_path = os.path.join(out_dir, f"douyin_{aweme_id}.gif")
                g = to_gif(path, gif_path)
                files.append({"path": os.path.abspath(gif_path), "bytes": g["bytes"],
                              "kind": "gif", "ok": True})
            if want_audio:
                files.append(_audio_from_video(ad, path, info, out_dir,
                                               aweme_id, audio_format, notes))
            if want_music:
                try:
                    files.append(download_music(ad, out_dir, aweme_id, "music"))
                except DouyinError as e:
                    files.append({"path": None, "kind": "music", "ok": False,
                                  "error": str(e)})
        elif not image_urls:
            raise DouyinError("这条作品既没有视频也没有图片，无法下载")

    return {
        "target": target,
        "aweme_id": aweme_id,
        "kind": kind,
        "desc": desc,
        "author": author,
        "create_time": ad.get("create_time"),
        "page_url": f"https://www.douyin.com/{'note' if kind == 'images' else 'video'}/{aweme_id}",
        "files": files,
        "notes": notes,
        "ok": any(f.get("ok") for f in files),
    }


def _fmt_size(b: int) -> str:
    if b >= 1 << 20:
        return f"{b / (1 << 20):.2f} MB"
    return f"{b / 1024:.0f} KB"


def _print_result(r: dict) -> None:
    kind_cn = {"video": "视频", "images": "图集", "unknown": "未知"}[r["kind"]]
    flag = "OK " if r["ok"] else "BAD"
    print(f"{flag} [{kind_cn}] {r['aweme_id']}  @{r['author']}")
    print(f"     {r['desc'][:70]}")
    for f in r["files"]:
        fk = f.get("kind")
        if not f.get("path"):
            label = {"audio": "音频", "music": "配乐", "video": "视频",
                     "gif": "GIF"}.get(fk, f"第 {f.get('index')} 项")
            print(f"     ! {label}失败: {f.get('error', '')[:100]}")
            continue
        extra = ""
        if fk == "video" and f.get("width"):
            extra = f"  {f['width']}x{f['height']}  {f.get('duration')}s"
        elif fk == "gif":
            extra = "  (GIF)"
        elif fk in ("audio", "music"):
            extra = f"  {f.get('format') or f.get('codec') or ''}"
            if f.get("duration"):
                extra += f"  {f['duration']}s"
            if fk == "music" and f.get("title"):
                extra += f"  {f['title'][:28]}"
        elif f.get("format"):
            extra = f"  {f['format']}"
        print(f"     {os.path.basename(f['path'])}{extra}  {_fmt_size(f['bytes'])}")
        print(f"       {f['path']}")
    for n in r.get("notes") or []:
        print(f"     ! {n}")


def main(argv: Iterable[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="抖音作品下载器：视频 / 图集 / 动图 GIF / 音频",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="例: python douyin_dl.py \"https://v.douyin.com/xxxx/\" -o downloads\n"
               "     python douyin_dl.py \"<链接>\" --audio --music")
    ap.add_argument("targets", nargs="*", help="链接 / 分享文案 / 作品 ID")
    ap.add_argument("--batch", help="文本文件，每行一个链接或整段分享文案")
    ap.add_argument("-o", "--outdir", default="downloads", help="输出目录（默认 downloads）")
    ap.add_argument("-q", "--quality", default="best",
                    choices=["best", "1080", "720", "540", "480", "360"],
                    help="视频清晰度（默认最高）")
    ap.add_argument("--to-gif", action="store_true", help="视频额外转一份 GIF")
    ap.add_argument("--audio", action="store_true",
                    help="额外导出视频原声（从 mp4 抽音轨，需要 ffmpeg；图文作品自动改为下配乐）")
    ap.add_argument("--music", action="store_true",
                    help="额外下载作品配乐/音乐（mp3，不需要 ffmpeg）")
    ap.add_argument("--audio-format", default="m4a", choices=["m4a", "mp3"],
                    help="--audio 的音轨格式（默认 m4a 无损复制，mp3 需重编码）")
    ap.add_argument("--images-only", action="store_true",
                    help="（已废弃）现在默认就不下载配乐，保留参数仅为兼容")
    ap.add_argument("--video-only", action="store_true", help="只下视频")
    ap.add_argument("--json", action="store_true", help="输出 JSON（给程序调用）")
    args = ap.parse_args(list(argv) if argv is not None else None)

    raw = " ".join(args.targets)
    if args.batch:
        try:
            with open(args.batch, encoding="utf-8") as f:
                raw += "\n" + f.read()
        except OSError as e:
            print(f"读不到批量文件: {e}", file=sys.stderr)
            return 2

    targets = extract_targets(raw)
    if not targets:
        print("没有找到抖音链接。粘贴分享链接或整段分享文案再试。", file=sys.stderr)
        return 2

    results, failures = [], []
    for i, t in enumerate(targets, 1):
        if not args.json:
            print(f"[{i}/{len(targets)}] 处理中…", end="\r", flush=True)
        try:
            results.append(fetch_one(t, args.outdir, args.quality,
                                     make_gif=args.to_gif,
                                     images_only=args.images_only,
                                     video_only=args.video_only,
                                     want_audio=args.audio,
                                     want_music=args.music,
                                     audio_format=args.audio_format))
        except Exception as e:  # noqa: BLE001
            failures.append({"target": t, "error": str(e)})

    if args.json:
        print(json.dumps({"ok": results, "failed": failures}, ensure_ascii=False, indent=2))
    else:
        if len(targets) > 1:
            print(" " * 40, end="\r")
        for r in results:
            _print_result(r)
        for f in failures:
            print(f"ERR {f['target']}")
            for line in f["error"].splitlines():
                print(f"     {line}")
        good = sum(1 for r in results if r["ok"])
        print(f"\n成功 {good}/{len(targets)}，失败 {len(failures)}")
        if good:
            print(f"文件在: {os.path.abspath(args.outdir)}")
    return 0 if results else 1


if __name__ == "__main__":
    sys.exit(main())
