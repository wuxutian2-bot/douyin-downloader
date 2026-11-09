#!/usr/bin/env python3
"""douyin_cdp.py — 抖音页面数据抓取器（零第三方依赖）

职责：给一个抖音作品 URL/ID，返回官方 detail 接口的完整 JSON。
只用 Python 标准库 + 本机 Chrome/Edge/Chromium。

为什么不是直接读 <video>：
  - 图集作品（media_type=68）根本没有 <video> 元素；
  - GIF / 动图作品在页面里也不暴露循环播放源；
  - detail JSON 里视频有十几档清晰度、图集有原图 URL 和逐帧动图，
    一次抓全，不用在 DOM 上猜。

用法（通常由 douyin_dl.py 调用）：
    python douyin_cdp.py <url-or-id>            # 打印作品元信息
    python douyin_cdp.py <url> --raw            # 打印完整 detail JSON
"""
from __future__ import annotations

import base64
import json
import os
import re
import socket
import struct
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

CHROME_CANDIDATES = [
    os.environ.get("CHROME_PATH", ""),
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
]


def find_browser() -> str:
    """按优先级找一个可用的 Chromium 内核浏览器。"""
    for c in CHROME_CANDIDATES:
        if c and Path(c).exists():
            return c
    raise RuntimeError(
        "找不到 Chrome / Edge / Chromium。\n"
        "请安装 Chrome，或设置环境变量 CHROME_PATH 指向浏览器可执行文件。"
    )


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# --------------------------------------------------------------- websocket
# CDP 只需要 text frame，这里实现最小可用的 RFC6455 客户端（掩码 + 心跳）

class WS:
    def __init__(self, ws_url: str, timeout: float = 90.0):
        host_port, path = ws_url.split("://", 1)[1].split("/", 1)
        host, port = host_port.rsplit(":", 1)
        self.sock = socket.create_connection((host, int(port)), timeout=timeout)
        self.sock.settimeout(timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        req = (f"GET /{path} HTTP/1.1\r\nHost: {host_port}\r\n"
               "Upgrade: websocket\r\nConnection: Upgrade\r\n"
               f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n")
        self.sock.sendall(req.encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise RuntimeError("WebSocket 握手失败：连接被关闭")
            buf += chunk
        status = buf.split(b"\r\n", 1)[0]
        if b" 101 " not in status:
            raise RuntimeError(f"WebSocket 握手被拒: {status[:80]!r}")
        self._rest = buf.split(b"\r\n\r\n", 1)[1]

    def _read_exact(self, n: int) -> bytes:
        while len(self._rest) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise RuntimeError("WebSocket 连接意外断开")
            self._rest += chunk
        out, self._rest = self._rest[:n], self._rest[n:]
        return out

    def send(self, text: str) -> None:
        data = text.encode()
        header = bytearray([0x81])
        n = len(data)
        if n < 126:
            header.append(0x80 | n)
        elif n < 65536:
            header.append(0x80 | 126)
            header += struct.pack(">H", n)
        else:
            header.append(0x80 | 127)
            header += struct.pack(">Q", n)
        mask = os.urandom(4)
        header += mask
        self.sock.sendall(bytes(header) + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def recv(self) -> str:
        while True:
            b1, b2 = self._read_exact(2)
            opcode, n = b1 & 0x0F, b2 & 0x7F
            if n == 126:
                n = struct.unpack(">H", self._read_exact(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._read_exact(8))[0]
            payload = self._read_exact(n) if n else b""
            if opcode == 0x8:
                raise RuntimeError("WebSocket 被对端关闭")
            if opcode == 0x9:                      # ping → pong
                self.sock.sendall(b"\x8a\x80" + os.urandom(4))
                continue
            if opcode in (0x1, 0x2):
                return payload.decode("utf-8", "replace")

    def settimeout(self, t: float) -> None:
        self.sock.settimeout(t)

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


class CDP:
    """极简 CDP 客户端。事件消息存进 self.events，供调用方检索。"""

    def __init__(self, ws_url: str):
        self.ws = WS(ws_url)
        self._id = 0
        self.events: list[dict] = []

    def call(self, method: str, timeout: float = 90.0, **params):
        self._id += 1
        mid = self._id
        self.ws.send(json.dumps({"id": mid, "method": method, "params": params}))
        deadline = time.time() + timeout
        while time.time() < deadline:
            self.ws.settimeout(max(1.0, deadline - time.time()))
            msg = json.loads(self.ws.recv())
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result", {})
            if "method" in msg:
                self.events.append(msg)
        raise RuntimeError(f"{method} 超时")

    def drain(self, seconds: float) -> None:
        """收集接下来 seconds 秒内到达的事件。"""
        end = time.time() + seconds
        while time.time() < end:
            self.ws.settimeout(max(0.3, end - time.time()))
            try:
                msg = json.loads(self.ws.recv())
            except (socket.timeout, TimeoutError):
                return
            if "method" in msg:
                self.events.append(msg)

    def eval(self, expr: str, await_promise: bool = True):
        r = self.call("Runtime.evaluate", expression=expr, returnByValue=True,
                      awaitPromise=await_promise, userGesture=True)
        if r.get("exceptionDetails"):
            return None
        return r.get("result", {}).get("value")


# ------------------------------------------------------------------ launch

def launch(headless: bool = True):
    """启动浏览器，返回 (process, base_url, ws_url)。调用方负责 terminate。"""
    exe = find_browser()
    port = free_port()
    profile = tempfile.mkdtemp(prefix="dy_cdp_")
    args = [
        exe,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={profile}",
        "--no-first-run", "--no-default-browser-check",
        "--disable-gpu", "--disable-dev-shm-usage",
        "--disable-blink-features=AutomationControlled",
        "--autoplay-policy=no-user-gesture-required",
        "--mute-audio",
        "--window-size=1280,900",
        "about:blank",
    ]
    if headless:
        args.insert(1, "--headless=new")
    proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{port}"
    for _ in range(120):
        try:
            with urllib.request.urlopen(f"{base}/json/version", timeout=1) as r:
                json.loads(r.read())
            break
        except Exception:  # noqa: BLE001
            if proc.poll() is not None:
                raise RuntimeError("浏览器启动后立刻退出，可能是权限或 profile 目录问题")
            time.sleep(0.25)
    else:
        raise RuntimeError("浏览器调试端口没有起来（30 秒超时）")

    # 新版 Chrome 的 /json/new 只接受 PUT，GET 返回 405
    req = urllib.request.Request(f"{base}/json/new?about:blank", method="PUT")
    with urllib.request.urlopen(req, timeout=5) as r:
        target = json.loads(r.read())
    return proc, base, target["webSocketDebuggerUrl"]


# ---------------------------------------------------------------- scraper

def fetch_detail(target: str, wait_s: float = 16.0, headless: bool = True) -> dict:
    """打开作品页，优先拦截 /aweme/detail/ 响应；失败则读 SSR Flight 数据。

    两种页面的数据来源不同：
      - /video/<id>  页面会发 XHR 到 aweme/detail，拦响应最准；
      - /note/<id>   不发 detail 请求，整份数据以 camelCase 形式内嵌在
                     window.__pace_f（React Flight）的字符串里。
    """
    if re.fullmatch(r"\d{15,25}", target):
        url = f"https://www.douyin.com/video/{target}"
    elif "douyin.com" in target:
        url = target          # 短链/长链都让浏览器自己跟跳
    else:
        raise RuntimeError(f"无法识别的输入: {target}")

    proc, base, ws_url = launch(headless=headless)
    try:
        c = CDP(ws_url)
        c.call("Page.enable")
        c.call("Runtime.enable")
        c.call("Network.enable")
        # 不覆盖 UA：默认就是真实 Chrome 指纹，抖音对陌生 UA 反而更容易回风控页
        c.call("Page.navigate", url=url)

        deadline = time.time() + wait_s
        detail_rid = None
        pending: list[str] = []
        while time.time() < deadline:
            c.drain(1.5)
            for ev in c.events:
                if ev.get("method") != "Network.responseReceived":
                    continue
                u = ev["params"]["response"]["url"]
                if "/aweme/detail/" in u and "douyin.com" in u:
                    rid = ev["params"]["requestId"]
                    if rid not in pending:
                        pending.append(rid)

            # 必须在响应还被浏览器保存时立刻读 body
            for rid in list(pending):
                try:
                    raw = c.call("Network.getResponseBody", requestId=rid)
                except RuntimeError:
                    pending.remove(rid)
                    continue
                body = raw.get("body", "")
                if raw.get("base64Encoded"):
                    body = base64.b64decode(body).decode("utf-8", "replace")
                try:
                    data = json.loads(body)
                except json.JSONDecodeError:
                    pending.remove(rid)
                    continue
                ad = data.get("aweme_detail") or data.get("aweme") or {}
                pending.remove(rid)
                if ad.get("aweme_id"):
                    return ad

        # ---- 兜底：从 SSR Flight 数据里抽（图文页走这条路）----
        ad = extract_from_flight(c)
        if ad:
            return _normalize_camel(ad)

        title = c.eval("document.title") or ""
        href = c.eval("location.href") or ""
        raise RuntimeError(
            "页面里没有找到作品数据。\n"
            f"  当前页面: {href}\n"
            f"  标题: {title}\n"
            "  常见原因：作品需要登录才能查看、作品已删除、或触发了验证码。"
        )
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=8)
        except Exception:  # noqa: BLE001
            proc.kill()


FLIGHT_DUMP_JS = r"""
(async () => {
  // React Flight 数据被切成多段字符串塞进 window.__pace_f，
  // 拼起来是一份不完整的 JSON 片段流。用括号配对从中抠出 aweme 对象。
  const f = window.__pace_f || [];
  let all = '';
  for (const item of f) {
    if (!Array.isArray(item)) continue;
    for (const part of item) {
      if (typeof part === 'string') all += part;
    }
  }
  const marker = '"images"';
  let pos = all.indexOf(marker);
  if (pos < 0) pos = all.indexOf('"desc"');
  if (pos < 0) return null;

  // 从 marker 往前找所属对象的起点，往后做括号配对
  const start = all.lastIndexOf('{"awemeId"', pos);
  const anchor = start >= 0 ? start : Math.max(0, all.lastIndexOf('{', pos));
  let depth = 0, inStr = false, esc = false, end = -1;
  for (let i = anchor; i < all.length; i++) {
    const ch = all[i];
    if (inStr) {
      if (esc) { esc = false; }
      else if (ch === '\\') { esc = true; }
      else if (ch === '"') { inStr = false; }
      continue;
    }
    if (ch === '"') { inStr = true; continue; }
    if (ch === '{' || ch === '[') depth++;
    else if (ch === '}' || ch === ']') {
      depth--;
      if (depth === 0) { end = i + 1; break; }
    }
  }
  if (end < 0) return null;
  return all.slice(anchor, end);
})()
"""


def extract_from_flight(c) -> dict | None:
    """从 window.__pace_f 里抠出 aweme 对象（图文页唯一的数据来源）。"""
    raw = c.eval(FLIGHT_DUMP_JS)
    if not raw:
        return None
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError:
        return None
    # 结构可能是 {awemeId, aweme:{detail:{...}}} 或直接是 detail
    if isinstance(obj, dict):
        if obj.get("aweme", {}).get("detail"):
            return obj["aweme"]["detail"]
        if obj.get("detail"):
            return obj["detail"]
        if obj.get("aweme_id") or obj.get("awemeId"):
            return obj
    return None


def _normalize_camel(ad: dict) -> dict:
    """Flight 数据是 camelCase，转成下载器使用的 snake_case 结构。"""
    def g(*names, default=None):
        for n in names:
            if n in ad and ad[n] is not None:
                return ad[n]
        return default

    out = dict(ad)
    out["aweme_id"] = str(g("aweme_id", "awemeId", default="") or "")
    out["desc"] = g("desc", default="") or ""
    out["media_type"] = g("media_type", "mediaType")
    out["aweme_type"] = g("aweme_type", "awemeType")
    out["create_time"] = g("create_time", "createTime")

    author = g("author", default={}) or {}
    if isinstance(author, dict):
        out["author"] = {
            "nickname": author.get("nickname") or author.get("nickName") or "",
            "sec_uid": author.get("sec_uid") or author.get("secUid") or "",
        }
    out.setdefault("author", {})
    # 图文页的 author 常常是空壳，真名在 authorInfo 里
    if not (out.get("author") or {}).get("nickname"):
        ai = g("authorInfo", "author_info", default={}) or {}
        if isinstance(ai, dict) and (ai.get("nickname") or ai.get("nickName")):
            out["author"] = {
                "nickname": ai.get("nickname") or ai.get("nickName") or "",
                "sec_uid": ai.get("secUid") or ai.get("sec_uid") or "",
            }

    # images: [{width,height,uri,urlList:[...]}] → [{url_list:[...]}]
    imgs = g("images", "image_list", "imageList", default=[]) or []
    norm_imgs = []
    for it in imgs:
        if not isinstance(it, dict):
            continue
        urls = it.get("url_list") or it.get("urlList") or it.get("download_url_list") or []
        norm_imgs.append({
            "url_list": urls,
            "download_url_list": it.get("downloadUrlList") or [],
            "width": it.get("width"),
            "height": it.get("height"),
        })
    out["images"] = norm_imgs

    # video：Flight 里的直链字段也是 camelCase
    video = g("video", default={}) or {}
    if isinstance(video, dict) and video:
        vid = dict(video)
        pa = video.get("play_addr") or video.get("playAddr") or {}
        if isinstance(pa, list):          # 某些结构里 playAddr 直接是 URL 数组
            pa = {"url_list": pa}
        if isinstance(pa, dict) and pa:
            vid["play_addr"] = {
                "url_list": pa.get("url_list") or pa.get("urlList") or [],
                "width": pa.get("width"),
                "height": pa.get("height"),
                "data_size": pa.get("data_size") or pa.get("dataSize"),
            }
        brs = []
        for b in (video.get("bit_rate") or video.get("bitRate") or []):
            if not isinstance(b, dict):
                continue
            p = b.get("play_addr") or b.get("playAddr") or {}
            if isinstance(p, list):
                p = {"url_list": p}
            if not isinstance(p, dict):
                continue
            brs.append({
                "gear_name": b.get("gear_name") or b.get("gearName") or "",
                "bit_rate": b.get("bit_rate") or b.get("bitRate") or 0,
                "play_addr": {
                    "url_list": p.get("url_list") or p.get("urlList") or [],
                    "width": p.get("width"),
                    "height": p.get("height"),
                },
            })
        vid["bit_rate"] = brs
        out["video"] = vid

    if not out["aweme_id"]:
        return None
    return out



def summarize(ad: dict) -> dict:
    """把 aweme_detail 压成元信息（不含直链）。"""
    imgs = ad.get("images") or ad.get("image_list") or []
    video = ad.get("video") or {}
    return {
        "aweme_id": ad.get("aweme_id"),
        "desc": (ad.get("desc") or "").strip(),
        "author": (ad.get("author") or {}).get("nickname") or "",
        "create_time": ad.get("create_time"),
        "media_type": ad.get("media_type"),
        "aweme_type": ad.get("aweme_type"),
        "is_images": bool(imgs),
        "image_count": len(imgs),
        "has_video": bool(video.get("play_addr")),
        "duration_ms": video.get("duration") or ad.get("duration"),
        "statistics": ad.get("statistics") or {},
    }


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    ad = fetch_detail(sys.argv[1])
    if "--raw" in sys.argv:
        print(json.dumps(ad, ensure_ascii=False))
    else:
        print(json.dumps(summarize(ad), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
