"""时间线 section —— 浏览器窗口 + 其内部网页合成「一行」，整行做链接。

合并逻辑在 digitallife.timeline_merge（本模块只渲染）：
    link_url = 与窗口行**起止时间完全相同**的第一条网页（无则 ""）
    sub      = 同窗其余网页（极少）；对不上时间窗的网页独立成行，不嵌套

两处改动（2026-10-25）：
1. **锚文本统一走 linktitle.resolve()**（单一真相）。带链接的行不再拼
   `应用 · 标题`：应用名在网页行上恒为浏览器名，重复且会与网站 section
   的文本打架（用户第 4 条诉求）。无链接的窗口行保持原样。
2. **每行带 id 锚点**，供页面顶部「24 小时定位条」点击跳转（用户第 1 条诉求）。
   锚点规则只有 row_anchor() 一处，定位条与表格共用，点数才对得上（S45）。
"""
from __future__ import annotations

from digitallife.domain.timeline_merge import clean_title, display_title, span_seconds
from digitallife.render import base, linktitle

ANCHOR = "timeline"
LABEL = "时间线"
MAJOR_SECONDS = 120
SHOW_FIRST = 60


def row_anchor(pid: str, i: int) -> str:
    """第 i 行（1 起）的 DOM id。定位条与表格**必须**共用这一个函数。"""
    return "%s-r%d" % (pid, i)


def row_text(row: dict, ctx) -> str:
    """一行该显示什么：带链接走 resolve（与网站 section 同一个函数），否则原样。"""
    url = str(row.get("link_url") or "").strip()
    if not url:
        return display_title(row)
    return linktitle.resolve(clean_title(row.get("title") or ""), url,
                             index=ctx.link_index)[0]


def rows_of(ctx, dev: str | None = None) -> list[dict]:
    return ctx.merged(dev)


def points(ctx) -> list[dict]:
    """当天时间线上的「有效点」= 时长 >= MAJOR_SECONDS 的行（跨设备）。

    返回 [{kind, start, end, anchor, label, device}]。定位条消费它，
    验收断言用独立遍历对拍（数量必须相等）。
    """
    out: list[dict] = []
    for dev in (ctx.devices or []):
        pid = "%s-%s" % (ANCHOR, dev)
        for i, row in enumerate(ctx.merged(dev), 1):
            s = str(row.get("start") or "")
            e = str(row.get("end") or "")
            if span_seconds(s, e) < MAJOR_SECONDS:
                continue
            out.append({"kind": "timeline", "start": s, "end": e,
                        "anchor": row_anchor(pid, i),
                        "label": row_text(row, ctx) or s,
                        "device": dev})
    return out


def panel(pid: str, ctx, *, hidden: bool = False) -> tuple[str, int]:
    rows = rows_of(ctx)
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
        url = str(row.get("link_url") or "").strip()
        text = row_text(row, ctx)
        head = base.link(text, url) if url else base.esc(text)
        extra = " data-extra hidden" if i > SHOW_FIRST else ""
        out.append('<tr id="%s"%s><td class="t num muted">%s</td><td class="ev%s">%s</td></tr>' % (
            base.esc(row_anchor(pid, i)), extra,
            base.esc(base.span(start, end)), " major" if major else "", head))
    out.append("</tbody></table></div>")
    if len(rows) > SHOW_FIRST:
        out.append(base.more_button(pid + "-body", "展开全部 %d 行" % len(rows)))
    out.append("</div>")
    return "".join(out), len(rows)
