"""网页标题解析 —— 给只有域名/URL 的链接补上「网页真实标题」。

为什么需要（2026-10-24 用户要求）：
  时段页时间线里很多行锚文本只有 `message.bilibili.com` 这种域名，
  读的人不知道那是视频、私信还是专栏。

  数据里 `event.title` 通常已经有标题，但有一部分只是域名本身（ManicTime
  记录的是窗口标题，某些页面就是域名），这些行需要补。

策略（三层，从便宜到贵）：
  1. `event.title` 可用（不是纯域名）→ 直接用，不联网
  2. 磁盘缓存命中 → 用缓存，不联网
  3. 联网抓 `<title>` → 写入缓存；失败则退回域名，不报错

缓存落 `tmp/.titlecache.json`（tmp 已被 .gitignore 排除，不进版本库）。
联网是**可选的**：默认只在显式调用 `scripts/fetch_titles.py` 时批量抓，
渲染时只查缓存，保证建页不会因为网络变慢。
"""
from __future__ import annotations

import json
import re
import threading
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

# 本文件在 scripts/slice_pages/ 下，项目根要往上**两层**（parents[2]）。
# 写成 parents[1] 会落到 scripts/tmp/ —— 实测抓了 54 条却一条都没生效，
# 因为建页读的是项目根 tmp/。这类「路径算错但一切看起来正常」的 bug
# 只能靠比对缓存条数为 0 才发现，见 T36f。
_CACHE = Path(__file__).resolve().parents[2] / "tmp" / ".titlecache.json"
_lock = threading.Lock()
_mem: dict[str, str] | None = None

# 明显是「域名当标题」的形态：不含空格、含点、像个主机名
_HOSTLIKE = re.compile(r"^[a-z0-9][a-z0-9.\-]*\.[a-z]{2,}(/.*)?$", re.I)


def _load() -> dict[str, str]:
    global _mem
    if _mem is None:
        try:
            _mem = json.loads(_CACHE.read_text(encoding="utf-8"))
        except Exception:
            _mem = {}
    return _mem


def host_of(url: str) -> str:
    try:
        h = urlparse(str(url or "").strip()).netloc.lower()
    except Exception:
        return ""
    return h[4:] if h.startswith("www.") else h


def is_hostlike(title: str, url: str = "") -> bool:
    t = str(title or "").strip()
    if not t:
        return True
    if " " in t:
        return False
    if _HOSTLIKE.match(t):
        return True
    h = host_of(url)
    return bool(h) and t.lower().replace("www.", "") == h


def cached(url: str) -> str:
    return (_load() or {}).get(str(url or "").strip(), "")


def put(url: str, title: str) -> None:
    global _mem
    t = str(title or "").strip()
    if not t:
        return
    with _lock:
        d = _load()
        d[str(url or "").strip()] = t[:300]
        try:
            _CACHE.parent.mkdir(parents=True, exist_ok=True)
            _CACHE.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass


def fetch(url: str, timeout: float = 6.0) -> str:
    """联网抓 <title>。失败返回空串，绝不抛异常（建页/批处理不能因网络失败）。"""
    u = str(url or "").strip()
    if not u.startswith(("http://", "https://")):
        return ""
    try:
        req = urllib.request.Request(u, headers={
            "User-Agent": "Mozilla/5.0 (compatible; digital-life/1.0)",
        })
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read(200_000)
        charset = "utf-8"
        m = re.search(rb'charset=["\']?([\w-]+)', raw[:2000], re.I)
        if m:
            charset = m.group(1).decode("ascii", "ignore")
        html = raw.decode(charset, "replace")
        m = re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I)
        if not m:
            return ""
        t = re.sub(r"\s+", " ", m.group(1)).strip()
        return t[:300]
    except Exception:
        return ""


def display_title(raw_title: str, url: str) -> tuple[str, bool]:
    """返回 (用于显示的标题, 是否用了外部来源)。

    优先顺序：原始标题非域名形态 → 缓存 → 原样返回域名。
    **渲染期不联网**：网络只在 scripts/fetch_titles.py 里批量做。
    """
    t = str(raw_title or "").strip()
    u = str(url or "").strip()
    if t and not is_hostlike(t, u):
        return t, False
    c = cached(u)
    if c:
        return c, True
    return t, False
