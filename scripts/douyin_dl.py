#!/usr/bin/env python3
"""douyin_dl.py — 抖音作品下载器（视频 / 图集 / 动图 GIF）

给一个抖音链接，把作品原片存到本地。只依赖 Python 标准库 + 本机 Chrome/Edge。

用法：
    python douyin_dl.py "<链接或整段分享文案>"
    python douyin_dl.py --batch links.txt -o downloads
    python douyin_dl.py <链接> --json
    python douyin_dl.py <链接> --to-gif        # 视频转 GIF
    python douyin_dl.py <链接> -q 720          # 指定清晰度

支持的输入：
    https://v.douyin.com/XXXX/                   分享短链
    https://www.douyin.com/video/<id>            视频长链
    https://www.douyin.com/note/<id>             图集长链
    <19位数字>                                    裸作品 ID
    整段分享文案（自动从中提取链接）

产出：
    视频  → douyin_<id>.mp4       （最高清晰度，无水印）
    图集  → douyin_<id>_01.jpg …  （原图，按张顺序编号）
    图表带音乐 → douyin_<id>.mp3  （附带的背景音乐，如果有）
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


def _pick_music_url(ad: dict) -> str | None:
    """图集附带的背景音乐。"""
    music = ad.get("music") or {}
    for key in ("play_url", "play_url_h265"):
        v = music.get(key)
        if isinstance(v, dict):
            lst = v.get("url_list") or []
            if lst:
                return lst[0].replace("http://", "https://")
    return None


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


def fetch_one(target: str, out_dir: str, quality: str = "best",
              make_gif: bool = False, images_only: bool = False,
              video_only: bool = False, prefer_jpg: bool = True) -> dict:
    if douyin_cdp is None:
        raise DouyinError("缺少 douyin_cdp.py，请确保它和本脚本在同一个文件夹")

    ad = douyin_cdp.fetch_detail(target)
    aweme_id = ad.get("aweme_id") or "unknown"
    desc = (ad.get("desc") or "").strip()
    author = (ad.get("author") or {}).get("nickname") or ""

    os.makedirs(out_dir, exist_ok=True)
    files: list[dict] = []
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

        # 图集背景音乐
        music_url = _pick_music_url(ad)
        if music_url and not images_only:
            mpath = os.path.join(out_dir, f"douyin_{aweme_id}.mp3")
            try:
                size = _download_url(music_url, mpath)
                files.append({"path": os.path.abspath(mpath), "bytes": size,
                              "kind": "music", "ok": size > 1024})
            except DouyinError:
                pass

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
        if not f.get("path"):
            print(f"     ! 第 {f.get('index')} 项失败: {f.get('error', '')[:80]}")
            continue
        extra = ""
        if f.get("kind") == "video" and f.get("width"):
            extra = f"  {f['width']}x{f['height']}  {f.get('duration')}s"
        elif f.get("kind") == "gif":
            extra = "  (GIF)"
        elif f.get("format"):
            extra = f"  {f['format']}"
        print(f"     {os.path.basename(f['path'])}{extra}  {_fmt_size(f['bytes'])}")
        print(f"       {f['path']}")


def main(argv: Iterable[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="抖音作品下载器：视频 / 图集 / 动图 GIF",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="例: python douyin_dl.py \"https://v.douyin.com/xxxx/\" -o downloads")
    ap.add_argument("targets", nargs="*", help="链接 / 分享文案 / 作品 ID")
    ap.add_argument("--batch", help="文本文件，每行一个链接或整段分享文案")
    ap.add_argument("-o", "--outdir", default="downloads", help="输出目录（默认 downloads）")
    ap.add_argument("-q", "--quality", default="best",
                    choices=["best", "1080", "720", "540", "480", "360"],
                    help="视频清晰度（默认最高）")
    ap.add_argument("--to-gif", action="store_true", help="视频额外转一份 GIF")
    ap.add_argument("--images-only", action="store_true", help="只下图片，不要背景音乐")
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
                                     video_only=args.video_only))
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
