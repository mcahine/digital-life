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
    # 返回 (html, 总片数, 已出总结片数)。视觉是跨设备 section，不分 panel。
    #
    # 父子片关系（2026-10-24 用户要求）：父片图片数超上限会被递归二分，
    # 只有叶子片跑视觉。父片**保留展示**，但必须说清「已切分为 N 个子片」并列出子片，
    # 不能显示成「未跑视觉模型 / 本片尚无视觉总结」—— 那会让人以为数据丢了。
    slices = ctx.all_slices()
    if not slices:
        return '<p class="empty">这一天没有时间片记录。</p>', 0, 0

    by_id = {s.get("slice_id"): s for s in slices}
    kids_of = {}
    for s in slices:
        par = (s.get("split_of") or "").strip()
        if par:
            kids_of.setdefault(par, []).append(s)

    done = sum(1 for s in slices if s.get("summary"))
    # 只渲染顶层（父片、以及未切分的叶子）。子片在父片体内列出，避免重复占版面；
    # 若某子片的父片不在当天列表里（跨日切分），仍单独渲染，不能丢。
    tops = [s for s in slices
            if not (s.get("split_of") or "").strip()
            or (s.get("split_of") or "").strip() not in by_id]

    out = []
    for s in tops:
        kids = sorted(kids_of.get(s.get("slice_id")) or [],
                      key=lambda x: str(x.get("start") or ""))
        # 判「父片」只看 status。
        # 不能写成 `or bool(kids)`：一个叶子片自己也可能被别人当作 split_of
        # （递归二分的中间层），那会让有总结的叶子被误标成父片。
        is_split = (s.get("status") or "") == "split"
        head = "%s · %s" % (s.get("dev_label") or "",
                            base.span(s.get("start"), s.get("end")))
        if is_split and not s.get("summary"):
            n_ok = sum(1 for k in kids if k.get("summary"))
            meta = "事件 %d · 已切分为 %d 个子片（%d 个已有总结）" % (
                int(s.get("event_count") or 0), len(kids), n_ok)
            rows = []
            for k in kids:
                tag = ("模型 " + k.get("model")) if k.get("present") else "无总结"
                rows.append("<li>%s · %s · %s</li>" % (
                    base.span(k.get("start"), k.get("end")),
                    base.esc("已生成" if k.get("summary") else "无总结"),
                    base.esc(tag)))
            body = ('<p class="lead">此片图片数超过单次上限，内容已递归切分到下列子片。'
                    '</p><ul>' + "".join(rows) + "</ul>")
        elif not s.get("summary"):
            meta = "事件 %d · 无画面总结" % int(s.get("event_count") or 0)
            body = '<p class="empty">本片没有可用的画面总结。</p>'
        elif _no_visual_basis(s):
            meta = "事件 %d · 模型 %s · 无画面描述" % (
                int(s.get("event_count") or 0), s.get("model") or "-")
            body = ('<p class="lead">本片无画面描述，以下内容仅依据 DB 时间线。</p>'
                    + render_summary_md(s["summary"]))
        else:
            meta = "事件 %d · 模型 %s" % (
                int(s.get("event_count") or 0), s.get("model") or "-")
            body = render_summary_md(s["summary"])
        out.append(base.card(head, meta, body))
    return "".join(out), len(tops), done
