"""网站 section —— 按域名聚合的全部网页访问（全量：不截断、不折叠）。

数据：ctx.structure["timeline"] 中 url 以 http/https 开头的条目。
用户 2026-10-22 明确取消「代表页面」列；域名一律从 URL 提取（tai 源的 app 字段
存的是页面标题，不能当域名用）。
形态照搬 00-Inbox/index.html 的 <details class="site"> + .visits 逐次访问列表。
"""
from __future__ import annotations

from digitallife.timeline_merge import _readable_from_url, _clean_title, span_seconds
from slice_pages import base

ANCHOR = "sites"
LABEL = "网站"
SHOW_FIRST = 8


def _str(v) -> str:
    return str(v or "").strip()


def _domain_of(url: str) -> str:
    """从 URL 取域名：去 www./端口/大小写；不合法返回 ""（由调用方退回原值）。"""
    raw = _str(url)
    if not raw.startswith(("http://", "https://")):
        return ""
    host = raw.split("//", 1)[-1].split("/", 1)[0].split("@")[-1].split(":")[0].lower()
    if host.startswith("www."):
        host = host[4:]
    import re
    if not re.match(r"^[a-z0-9]([a-z0-9.-]*[a-z0-9])?\.[a-z]{2,}$", host):
        return ""
    return host


def items_of(structure: dict) -> list[dict]:
    out: list[dict] = []
    for it in (structure or {}).get("timeline") or []:
        url = _str(it.get("url"))
        if not url.startswith(("http://", "https://")):
            continue
        out.append({
            "start": _str(it.get("start")),
            "end": _str(it.get("end")),
            "title": _str(it.get("title")),
            "url": url,
            "domain": _domain_of(url) or _str(it.get("app")) or "(未知)",
        })
    return out


def group_of(items: list[dict]) -> list[dict]:
    """按域名聚合：时长 = 各条目 span 之和；页面数 = 去重 url 数；访问次数 = 条目数。"""
    bucket: dict[str, dict] = {}
    for it in items:
        d = it["domain"]
        g = bucket.setdefault(d, {"domain": d, "seconds": 0, "pages": set(), "visits": []})
        g["seconds"] += int(span_seconds(it["start"], it["end"]))
        g["pages"].add(it["url"])
        g["visits"].append(it)
    out = list(bucket.values())
    for g in out:
        g["pages"] = len(g["pages"])
        g["visits"].sort(key=lambda v: (v["start"], v["end"]))
    out.sort(key=lambda g: (-g["seconds"], g["domain"]))
    return out


def _is_url(s: str) -> bool:
    return str(s or "").strip().startswith(("http://", "https://"))


def window_titles(structure: dict) -> dict:
    """(start, end) -> 窗口行标题。网页事件自己没有可读标题时向它借。

    实测 2025-02-17：334 条网页事件里只有 190 条自带可读标题，
    另有 112 条能从同时间窗的浏览器窗口行借到（ManicTime 两条时间线的分布差异）。
    """
    out: dict = {}
    for it in (structure or {}).get("timeline") or []:
        if _is_url(it.get("url")):
            continue
        title = _clean_title(it.get("title") or "")
        if title and not _is_url(title):
            out.setdefault((str(it.get("start") or ""), str(it.get("end") or "")), title)
    return out


def _page_label(v: dict, win_titles: dict | None = None) -> str:
    """一次访问的锚文本，三级降级：

    ① 自己的页面标题（实测 57% 有：`解释合取谬误 - 豆包`）
    ② 同时间窗浏览器窗口行的标题（再补 112 条）
    ③ `域名 · 路径末段`（`doubao.com · chat/1398560757277442`）

    绝不整条 URL 上屏 —— 用户 2026-10-22：「我的页面级记录去了哪里」，
    记录一直在（每次访问一行），但之前锚文本是
    `www.bilibili.com/video/BV191AVe8Ev1/?spm_id_from=333.337.search-card.a`，
    一屏全是这种串，人眼读不出是哪个页面。
    """
    url = str(v.get("url") or "").strip()
    title = str(v.get("title") or "").strip()
    if title and not _is_url(title) and title != url:
        return title
    borrowed = (win_titles or {}).get((str(v.get("start") or ""), str(v.get("end") or "")))
    if borrowed:
        return borrowed
    return _readable_from_url(url) or (url.split("//", 1)[-1] or url)


def panel(pid: str, ctx, *, hidden: bool = False) -> tuple[str, int]:
    groups = group_of(items_of(ctx.structure or {}))
    if not groups:
        return ('<div class="panel" id="%s"%s><p class="empty">这一天没有网页访问记录。</p></div>'
                % (base.esc(pid), " hidden" if hidden else ""), 0)

    out: list[str] = []
    out.append('<div class="panel" id="%s"%s>' % (base.esc(pid), " hidden" if hidden else ""))
    win_titles = window_titles(ctx.structure)
    out.append('<div class="sites" id="%s-list">' % base.esc(pid))
    for i, g in enumerate(groups, 1):
        extra = " data-extra hidden" if i > SHOW_FIRST else ""
        out.append('<details class="site"%s id="site-%s-%s">' % (
            extra, base.esc(pid), base.esc(g["domain"].replace(".", "-"))))
        out.append('<summary><span class="site-name">%s</span>'
                   '<span class="site-figs"><span class="site-meta">%d 次 · %d 页</span>'
                   '<span class="site-dur">%s</span></span>'
                   '<svg class="chev" viewBox="0 0 16 16" aria-hidden="true"><path d="M4 6l4 4 4-4" '
                   'fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" '
                   'stroke-linejoin="round"/></svg></summary>'
                   % (base.esc(g["domain"]), len(g["visits"]), g["pages"],
                      base.esc(base.fmt_dur(g["seconds"]))))
        out.append('<div class="visits">')
        for v in g["visits"]:
            out.append('<div class="visit"><span class="vt">%s</span>'
                       '<span class="vd">%s</span><a href="%s" target="_blank" rel="noreferrer" '
                       'title="%s">%s</a></div>' % (
                           base.esc(base.span(v["start"], v["end"])),
                           base.esc(base.fmt_dur(span_seconds(v["start"], v["end"]))),
                           base.esc(v["url"]),
                           base.esc(v["url"]),      # hover 时给完整地址，便于核对
                           base.esc(_page_label(v, win_titles))))
        out.append("</div></details>")
    out.append("</div>")
    if len(groups) > SHOW_FIRST:
        out.append(base.more_button(pid + "-list", "展开全部 %d 个站点" % len(groups)))
    out.append("</div>")
    return "".join(out), len(groups)
