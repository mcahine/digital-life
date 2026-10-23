"""应用 section —— 该设备一天的全部应用（全量：不截断、不折叠、不分页）。

数据由 contract.Ctx 提供：ctx.structure["apps"] = [{"app","app_id","seconds","sessions"}, ...]。
本模块只产出 **section 片段**（一天一页的其中一个 <section>），不再拼整页。

首屏只显示前 SHOW_FIRST 个，其余打 data-extra 折叠，由「展开全部」按钮打开
（照搬 00-Inbox/index.html 的 data-extra 机制）。
"""
from __future__ import annotations

from slice_pages import base

ANCHOR = "apps"
LABEL = "应用"
SHOW_FIRST = 12


def _int(v) -> int:
    try:
        return int(float(v or 0))
    except (TypeError, ValueError):
        return 0


def _name(item: dict) -> str:
    """应用名为空退回 app_id，两者都空写 (未知)。"""
    app = str(item.get("app") or "").strip()
    if not app:
        app = str(item.get("app_id") or "").strip()
    return app or "(未知)"


def _pct(part: int, whole: int) -> str:
    return "%.1f%%" % ((100.0 * part / whole) if whole else 0.0)


def rows_of(structure: dict) -> list[dict]:
    out: list[dict] = []
    for item in (structure or {}).get("apps") or []:
        out.append({
            "name": _name(item),
            "seconds": max(0, _int(item.get("seconds"))),
            "sessions": max(0, _int(item.get("sessions"))),
        })
    # 时长降序、同长按名称升序。两个分量都是可比较类型，两次生成结果一致（S59）。
    out.sort(key=lambda r: (-r["seconds"], r["name"]))
    return out


def panel(pid: str, ctx, *, hidden: bool = False) -> tuple[str, int]:
    """渲染一个设备面板。返回 (html, 行数)；行数供 section-head 的计数与 tab 徽标用。"""
    rows = rows_of(ctx.structure or {})
    if not rows:
        return ('<div class="panel" id="%s"%s><p class="empty">这一天没有应用使用记录。</p></div>'
                % (base.esc(pid), " hidden" if hidden else ""), 0)

    total_seconds = sum(r["seconds"] for r in rows)
    total_sessions = sum(r["sessions"] for r in rows)
    switches = _int((ctx.structure or {}).get("switch_count"))

    out: list[str] = []
    out.append('<div class="panel" id="%s"%s>' % (base.esc(pid), " hidden" if hidden else ""))
    out.append('<p class="lead">共 %d 个应用、合计活跃 %s、切换 %s 次</p>' % (
        len(rows), base.esc(base.fmt_dur(total_seconds)), "{:,}".format(switches)))
    out.append('<div class="tw"><table>')
    out.append('<thead><tr><th class="num">序号</th><th>应用</th>'
               '<th class="num">时长</th><th class="num">占比</th>'
               '<th class="num">次数</th></tr></thead>')
    out.append('<tbody id="%s-body">' % base.esc(pid))
    for i, r in enumerate(rows, 1):
        extra = ' data-extra hidden' if i > SHOW_FIRST else ""
        out.append(
            '<tr%s><td class="num muted">%d</td><td>%s</td><td class="num">%s</td>'
            '<td class="num">%s</td><td class="num">%s</td></tr>' % (
                extra, i, base.esc(r["name"]), base.esc(base.fmt_dur(r["seconds"])),
                _pct(r["seconds"], total_seconds), base.esc("{:,}".format(r["sessions"])))
        )
    out.append("</tbody></table></div>")
    if len(rows) > SHOW_FIRST:
        out.append(base.more_button(pid + "-body", "展开全部 %d 个应用" % len(rows)))
    out.append("</div>")
    return "".join(out), len(rows)
