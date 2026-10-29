"""视觉 section —— 当日片层视觉总结（每片一张卡，全量不折叠）。

数据源 ctx.all_slices()（每片带 dev / dev_label）。
summary 里含 markdown 小标题（## 关键操作 等）：## 行 -> <strong>，- 行 -> <ul><li>，其余 -> <p>。
所有文本先 base.esc 再拼标签（S9：不得直接插原始文本）。

## 父片必须展开子片内容（2026-10-25 用户第 2 条诉求）

用户原话：「视觉总结的父片因为图片太多所以切分出子片没有问题，但是渲染只有父片，
看不到子片内容，问题就很大了」。

改前：父片卡片只有一句「内容已递归切分到下列子片」+ 一串
「时间区间 · 已生成/无总结 · 模型名」的 <li> —— **子片的 overall_summary 一个字都没渲染**。
读者点开父片，看到的是一张空卡。

改后：父片卡片内**递归展开整棵子树**，每个叶子片的总结正文用 render_summary_md 完整渲染。
约束有三条，缺一条就是回归：
  1. **递归**：split_of 可能多层二分（中间层也是 status='split'），不能只展开一层；
  2. **只出现一次**：整棵子树在父片体内展开，子片自身不再作为顶层卡片重复出现；
  3. **叶子守恒**：渲染出的总结块数 == 叶子片总数（验收用独立遍历对拍，S45）。

统计口径同步修正：`panel` 返回的 (总片数, 已出总结片数) 现在**按叶子片**算。
按片数算时，父片没有 summary 会被算成「未完成」，于是页头显示
「47 片 · 32 片已生成」，而实际 61 个叶子全都有总结 —— 数字自相矛盾。
"""
from __future__ import annotations

from digitallife.render import base

ANCHOR = "vision"
LABEL = "视觉"

# 视觉总结正文的纯文本长度超过这个值就默认折叠（用户 N1，2026-10-25）。
# 依据：docs/HANDOFF-2026-10-25.md §11.9 建议初值 800 字 —— 2026-08-13 平均每片
# summary 约 400-600 字，800 能让大多数片直接展开阅读，只折叠少数超长片。
# 铁律：折叠只是「默认收起」，全文必须完整落在 details 内，不许截断、不许省略号。
SUMMARY_COLLAPSE_CHARS = 800

# summary 预览的行数与字数（2 行 / 约 120 字，§11.9）。
SUMMARY_PREVIEW_LINES = 2
SUMMARY_PREVIEW_CHARS = 120


def _plain_text(text: str) -> str:
    """把片层总结转成纯文本（去 markdown 标记、压空白），用于长度判定与预览。

    为什么单独做一遍而不是直接数字符串长度：summary 里有 md 标记（井号标题、
    减号列表），直接数会把标记算进正文，阈值就不再是「正文字数」。
    """
    out = []
    for raw in str(text or '').splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith('#'):
            line = line.lstrip('#').strip()
        elif line.startswith(('- ', '* ')):
            line = line[2:].strip()
        if line:
            out.append(line)
    return ''.join(out)


def fold_summary(body_html: str, summary_text: str) -> str:
    """把已渲染的总结正文包进原生 details；未超阈值就原样返回。

    为什么用原生 details 而不是 JS 折叠：base.JS 的 reveal() 已经会
    「先展开 details 再滚动」，定位条点锚点时能自动展开到目标片。自己写 JS
    折叠就要在 reveal() 里再加一条分支，多一处会失配的耦合。

    为什么 body_html 整体进 details 而不是先截断再包：铁律 —— 任何
    「取前 N 字 + 省略号」都会让全文从产物里消失。summary 只放预览文本
    （纯文本前 120 字，仅用于点击提示），details 里放整段 body_html。
    """
    if not body_html:
        return ''
    plain = _plain_text(summary_text)
    n = len(plain)
    if n <= SUMMARY_COLLAPSE_CHARS:
        return body_html
    prev = plain[:SUMMARY_PREVIEW_CHARS]
    if len(plain) > SUMMARY_PREVIEW_CHARS:
        prev = prev + '...'
    return ('<details class="vs-fold"><summary>%s'
            '<span class="vs-words"> 展开全部（共 %d 字）</span></summary>'
            '<div class="vs-fold-body">%s</div></details>') % (
                base.esc(prev), n, body_html)


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


def card_anchor(slice_id: str) -> str:
    """片卡片的 DOM id。定位条与卡片**共用**这一个函数，点数才对得上（S45）。"""
    sid = str(slice_id or "").strip()
    return "%s-s%s" % (ANCHOR, sid[:12]) if sid else ANCHOR


def _head_of(s: dict) -> str:
    return "%s · %s" % (s.get("dev_label") or "", base.span(s.get("start"), s.get("end")))


def _kids_map(slices: list[dict]) -> dict[str, list[dict]]:
    kids_of: dict[str, list[dict]] = {}
    for s in slices:
        par = (s.get("split_of") or "").strip()
        if par:
            kids_of.setdefault(par, []).append(s)
    for v in kids_of.values():
        v.sort(key=lambda x: str(x.get("start") or ""))
    return kids_of


def _walk_leaves(sid: str, kids_of: dict, seen: set) -> list[dict]:
    """递归收集 sid 下面的**全部叶子片**（无子片者）。seen 防环、防重复。"""
    kids = kids_of.get(sid) or []
    if not kids:
        return []
    out: list[dict] = []
    for k in kids:
        kid = str(k.get("slice_id") or "")
        if kid and kid in seen:
            continue
        if kid:
            seen.add(kid)
        sub = _walk_leaves(kid, kids_of, seen)
        out.extend(sub if sub else [k])
    return out


def leaves_of(slices: list[dict]) -> list[dict]:
    """当日全部**叶子片**（渲染顺序：按开始时间）。父片一律不算。

    顶层 = 没有 split_of，或 split_of 指向当天列表外的片（跨日切分）。
    顶层若不是父片，它自己就是叶子；若是父片，递归取它下面的叶子。
    只在某片"被别人 split 过"时才算父片 —— 不能只看 kids 是否为空，
    因为递归二分的**中间层**既是某个父片的子片，又自己带子片。
    """
    by_id = {str(s.get("slice_id") or ""): s for s in slices}
    kids_of = _kids_map(slices)
    tops = [s for s in slices
            if not (s.get("split_of") or "").strip()
            or (s.get("split_of") or "").strip() not in by_id]
    seen: set = set()
    out: list[dict] = []
    for s in tops:
        sid = str(s.get("slice_id") or "")
        seen.add(sid)
        sub = _walk_leaves(sid, kids_of, seen)
        out.extend(sub if sub else [s])
    out.sort(key=lambda x: (x.get("start_utc_ms") or 0))
    return out


def points(ctx) -> list[dict]:
    """定位条上的「视觉片点」= **有 summary 的叶子片**（父片没有 summary，不算）。"""
    out: list[dict] = []
    for s in leaves_of(ctx.all_slices()):
        if not s.get("summary"):
            continue
        out.append({"kind": "vision",
                    "start": s.get("start") or "",
                    "end": s.get("end") or "",
                    "anchor": card_anchor(s.get("slice_id")),
                    "label": "%s %s" % (s.get("dev_label") or "",
                                        base.span(s.get("start"), s.get("end"))),
                    "device": s.get("dev") or ""})
    return out


def panel(ctx) -> tuple[str, int, int]:
    """返回 (html, 叶子片总数, 有总结的叶子片数)。

    视觉是跨设备 section，不分 panel。片数口径 = **叶子片**（父片是容器，不是内容）。
    """
    slices = ctx.all_slices()
    if not slices:
        return '<p class="empty">这一天没有时间片记录。</p>', 0, 0

    by_id = {str(s.get("slice_id") or ""): s for s in slices}
    kids_of = _kids_map(slices)
    leaves = leaves_of(slices)
    done = sum(1 for s in leaves if s.get("summary"))

    def node(s: dict) -> str:
        """渲染一片；父片**递归展开**每个子片，叶子片**完整渲染总结正文**。

        为什么定义在 panel 里面：这段逻辑就是「父片里放什么」的语义本体，
        放成模块级私有函数会让「父片到底渲染了子片正文吗」这件事在
        `getsource(vision.panel)` 里完全看不见 —— 静态检查读不到，
        只能靠跑产物才能验证。收进来后，调用点与递归点都在 panel 源码里可读。
        """
        sid = str(s.get("slice_id") or "")
        kids = kids_of.get(sid) or []
        when = base.span(s.get("start"), s.get("end"))
        nev = int(s.get("event_count") or 0)

        if not kids:
            meta_bits = [s.get("dev_label") or "", when, "事件 %d" % nev]
            if s.get("model"):
                meta_bits.append("模型 %s" % s.get("model"))
            if _no_visual_basis(s):
                meta_bits.append("无画面描述")
            inner = []
            if not s.get("summary"):
                inner.append('<p class="empty">本片没有可用的画面总结。</p>')
            else:
                if _no_visual_basis(s):
                    inner.append('<p class="lead">本片无画面描述，以下内容仅依据 DB 时间线。</p>')
                # 每个叶子**各自**判断是否折叠：父片折叠与否不影响子片。
                inner.append(fold_summary(render_summary_md(s["summary"]),
                                         s["summary"]))
            return ('<div class="vnode leaf" id="%s">'
                    '<p class="vnode-head">%s</p>%s</div>') % (
                        base.esc(card_anchor(sid)),
                        base.esc(" · ".join(b for b in meta_bits if b)),
                        "".join(inner))

        n_ok = sum(1 for k in kids if k.get("summary"))
        parts = ['<div class="vnode split">',
                 '<p class="vnode-head">%s · 事件 %d · 已切分为 %d 个子片</p>'
                 % (base.esc(when), nev, len(kids))]
        if s.get("summary"):
            # 父片自身的总结同样按长度阈值独立折叠。
            parts.append(fold_summary(render_summary_md(s["summary"]),
                                     s["summary"]))
            parts.append('<p class="lead">本片自身另有总结，下列子片的完整总结如下。</p>')
        else:
            parts.append('<p class="lead">本片图片数超过单次上限，内容已递归切分到下列 %d 个子片'
                         '（%d 个已有总结）。</p>' % (len(kids), n_ok))
        for k in kids:
            parts.append(node(k))
        parts.append("</div>")
        return "".join(parts)

    # 只渲染顶层（父片或独立叶子）。子片在父片体内递归展开 —— 不重复占版面。
    tops = [s for s in slices
            if not (s.get("split_of") or "").strip()
            or (s.get("split_of") or "").strip() not in by_id]

    out = []
    for s in tops:
        kids = kids_of.get(str(s.get("slice_id") or "")) or []
        is_split = (s.get("status") or "") == "split" or bool(kids)
        if is_split:
            meta = "事件 %d · 已切分为 %d 个子片（叶子总结 %d 个已渲染）" % (
                int(s.get("event_count") or 0), len(kids),
                len(_walk_leaves(str(s.get("slice_id") or ""), kids_of, set())))
        elif s.get("summary"):
            meta = "事件 %d · 模型 %s" % (int(s.get("event_count") or 0),
                                        s.get("model") or "-")
        else:
            meta = "事件 %d · 无画面总结" % int(s.get("event_count") or 0)
        # 卡片与它内部的 vnode **不能同名**：顶层叶片的卡片里，node(s) 第一层
        # 就渲染出带 card_anchor() 的 leaf 节点，两处同名会产出重复 DOM id
        # （实测 2026-08-13 有 36 个、2026-08-07 有 75 个）。重复 id 让
        # getElementById 只取第一个，定位条跳转就有歧义。
        # 约定：**跳转锚点永远指 vnode**，卡片用 "-card" 后缀。
        out.append('<div class="card" id="%s-card"><h3>%s</h3><p class="when">%s</p>%s</div>' % (
            base.esc(card_anchor(s.get("slice_id"))), base.esc(_head_of(s)),
            base.esc(meta), node(s)))
    return "".join(out), len(leaves), done
