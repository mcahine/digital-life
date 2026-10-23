"""视觉 section —— 当日片层视觉总结（每片一张卡，全量不折叠）。

数据源 ctx.all_slices()（每片带 dev / dev_label）。
summary 里含 markdown 小标题（## 关键操作 等）：## 行 -> <strong>，- 行 -> <ul><li>，其余 -> <p>。
所有文本先 base.esc 再拼标签（S9：不得直接插原始文本）。
"""
from __future__ import annotations

from slice_pages import base

ANCHOR = "vision"
LABEL = "视觉"


def render_summary_md(text: str) -> str:
    """把片层总结（##小节 + - 要点 + 散文）渲染成自然段；每段文本独立转义。"""
    out: list[str] = []
    open_ul = False
    for raw_line in str(text or "").splitlines():
        line = raw_line.strip()
        if not line:
            if open_ul:
                out.append("</ul>")
                open_ul = False
            continue
        if line.startswith("#"):
            if open_ul:
                out.append("</ul>")
                open_ul = False
            out.append("<p><strong>%s</strong></p>" % base.esc(line.lstrip("#").strip()))
        elif line.startswith(("- ", "* ")):
            if not open_ul:
                out.append("<ul>")
                open_ul = True
            out.append("<li>%s</li>" % base.esc(line[2:].strip()))
        else:
            if open_ul:
                out.append("</ul>")
                open_ul = False
            out.append("<p>%s</p>" % base.esc(line))
    if open_ul:
        out.append("</ul>")
    return "".join(out)




def _no_visual_basis(s: dict) -> bool:
    """该片是否**没有**画面描述。

    判据来自 `load_slices` 预算好的 `has_description`（查事件层 payload.description）。
    **不能**用 structured.described_images 判断——纯文本模式下模型把画面细节写进
    overall_summary，那三个结构化字段全空，会导致「全部被判为无描述」
    （2026-10-23 实测踩到，2026-07-21 的 123 片全被误标）。
    """
    hd = s.get("has_description")
    if hd is None:
        return False          # 取不到就不标注，宁可不标也不要错标
    return not hd


def panel(ctx) -> tuple[str, int, int]:
    """返回 (html, 总片数, 已出总结片数)。视觉是跨设备 section，不分 panel。"""
    slices = ctx.all_slices()
    if not slices:
        return '<p class="empty">这一天没有时间片记录。</p>', 0, 0

    done = sum(1 for s in slices if s.get("summary"))
    out: list[str] = []
    for s in slices:
        head = "%s · %s" % (s.get("dev_label") or "", base.span(s.get("start"), s.get("end")))
        # 用户 2026-10-22：「视觉总结方面我不需要截图展示，只要文字就可以」。
        # 所以不再提「截图 N 张」——它是读图过程的内部量，对读总结的人没有意义。
        meta = "事件 %d · %s" % (
            int(s.get("event_count") or 0),
            ("模型 %s" % s.get("model")) if s.get("present") else "未跑视觉模型")
        if not s.get("summary"):
            body = '<p class="empty">本片尚无视觉总结。</p>'
        elif _no_visual_basis(s):
            # 该片没有画面描述，总结完全基于 DB 时间线。必须写明，
            # 否则读者会以为模型看过画面（2026-10-23 用户视角检查发现）。
            body = ('<p class="lead">本片无画面描述，以下内容仅依据 DB 时间线。</p>'
                    + render_summary_md(s["summary"]))
        else:
            body = render_summary_md(s["summary"])
        out.append(base.card(head, meta, body))
    return "".join(out), len(slices), done
