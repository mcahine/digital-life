"""时间线 section —— 浏览器窗口 + 其内部网页合成「一行」，整行做链接。

合并逻辑在 digitallife.timeline_merge（本模块只渲染）：
    link_url = 与窗口行**起止时间完全相同**的第一条网页（无则 ""）
    sub      = 同窗其余网页（极少）；对不上时间窗的网页独立成行，不嵌套
"""
from __future__ import annotations

from digitallife.timeline_merge import display_title, merge_browser_web, span_seconds
from slice_pages import base

ANCHOR = "timeline"
LABEL = "时间线"
MAJOR_SECONDS = 120
SHOW_FIRST = 60


def _sub_title(item: dict) -> str:
    title = str(item.get("title") or "").strip()
    return title or str(item.get("app") or "").strip()


def panel(pid: str, ctx, *, hidden: bool = False) -> tuple[str, int]:
    rows = merge_browser_web((ctx.structure or {}).get("timeline") or [])
    if not rows:
        return ('<div class="panel" id="%s"%s><p class="empty">这一天没有时间线记录。</p></div>'
                % (base.esc(pid), " hidden" if hidden else ""), 0)

    out: list[str] = []
    out.append('<div class="panel" id="%s"%s><div class="tw"><table>' % (
        base.esc(pid), " hidden" if hidden else ""))
    out.append('<thead><tr><th class="t">时间</th><th>内容</th></tr></thead>')
    out.append('<tbody id="%s-body">' % base.esc(pid))
    for i, row in enumerate(rows, 1):
        start = str(row.get("start") or "")
        end = str(row.get("end") or "")
        major = span_seconds(start, end) >= MAJOR_SECONDS
        title = display_title(row)
        url = str(row.get("link_url") or "").strip()
        head = base.link(title, url) if url else base.esc(title)
        extra = " data-extra hidden" if i > SHOW_FIRST else ""
        out.append('<tr%s><td class="t num muted">%s</td><td class="ev%s">%s</td></tr>' % (
            extra, base.esc(base.span(start, end)), " major" if major else "", head))
    out.append("</tbody></table></div>")
    if len(rows) > SHOW_FIRST:
        out.append(base.more_button(pid + "-body", "展开全部 %d 行" % len(rows)))
    out.append("</div>")
    return "".join(out), len(rows)
