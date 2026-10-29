"""24 小时定位条 —— 页面顶部两条独立轴（时间线 / 视觉），每轴按设备分行。

用户原话（2026-10-25，第 1 条）：「时段页的时间线和视觉很容易过场，要找到有用时间点
比较麻烦，需要设置一个条，这个条里是当天 24 小时内记录的有效点，方便我去找时间点」

用户原话（2026-10-25，第 2 轮）：「这个条里的时间线事件怎么做不到全部呢，还有要区分出
不同设备的时间线。这个条需要考虑到能够容纳几百、几千的事件……视觉总结和时间线分成
两个段，这两个段按照设备又分出独立线，这样最大化避免冲突」

## 结构（N2 + N3 + N4）

    #hourbar[data-n ... data-n-timeline-rows ...]
      .hb-axis[data-seg=timeline]        <- 时间线轴
        .hb-rows
          .hb-row[data-dev=pc-main][data-bucket-w=N][data-n=1771][data-n-marks=101]
            .hb-lane > a.hb-p / a.hb-b   <- 单点 或 聚合桶
        .hb-scale > .hb-scale-in > .hb-tick/.hb-lab
      .hb-axis[data-seg=vision]          <- 视觉轴（结构与上完全对称）

两条轴而不是一条：时间线行与视觉片在时间上大量重叠，挤在同一条轨道里必然互相压盖，
事件一多就退化成一团色块。轴内再按设备分行 —— 冲突只可能发生在「同轴同设备同秒」，
而那是同一个点，本来就该合并。

## 零丢失（N2 的核心，也是旧实现的真 bug）

旧实现只画 timeline.points()（>=120 秒的行）：2026-08-13 的 653 行里只有 65 行被画出来，
覆盖率 10.0%；2026-08-07 的 1771 行里只有 59 行，3.3%。用户看到的就是「怎么做不到全部」。
新实现分两档（按「该设备该段」独立判定，不是全局判定）：
  · 有效点 <= POINT_CAP(200)：逐点画，一个不聚合；
  · > 200：按时间桶聚合，每桶一个标记 + 条数（data-c），桶数 <= 200。
    桶宽 bucket_w = max(60, min(900, ceil(span_s / 200)))，span 是该设备当日实际活跃跨度。
  两档都满足「Σ data-c == 该设备该段总条数」，所以零丢失是结构性的，不是抽样。

## 口径不许混（S63）

data-n-timeline（历史名）= 旧口径「>=120s 的行数」，保留给旧断言过渡；
data-n-timeline-rows = 该日时间线全部行数（跨设备合计）；
data-n-timeline-points = 时间线轴实际渲染的标记数。
2026-08-13 这三个数分别是 65 / 653 / 72 —— 三个不同的东西，不要互相替代。

## 锚点（S65 / DL-18）

每标记的 href 一律由 timeline.row_anchor() / vision.card_anchor() 生成 ——
与表格行 / 片卡片用的是同一个函数。聚合桶指向桶内第一条事件的锚点。
落在 hidden panel 或 details 里的目标由 base.JS 的 reveal() 负责先展开再滚动。
"""
from __future__ import annotations

import math

from digitallife.render import base, timeline, vision
from digitallife.render.contract import device_label

ANCHOR = "hourbar"

# 单行逐点上限（docs/HANDOFF-2026-10-25.md §11.9）：<= 此值逐点画，> 此值按时间桶聚合。
POINT_CAP = 200

# 桶宽上下界（秒）。60 = 再细没有意义；900 = 与切片窗一致，超过就该改用按片聚合。
BUCKET_MIN_S = 60
BUCKET_MAX_S = 900

# 两条轴的固定顺序：时间线在上、视觉在下（用户：视觉总结和时间线分成两个段）。
SEGMENTS = (("timeline", "时间线"), ("vision", "视觉总结"))


def _sec(hhmmss) -> int:
    """HH:MM:SS 转当日秒数。解析失败返回 -1（调用方跳过该条）。"""
    parts = str(hhmmss or "").strip().split(":")
    if len(parts) < 2:
        return -1
    try:
        h = int(parts[0])
        m = int(parts[1])
        sec = int(float(parts[2])) if len(parts) > 2 else 0
    except (TypeError, ValueError):
        return -1
    if not (0 <= h <= 23 and 0 <= m <= 59 and 0 <= sec <= 59):
        return -1
    return h * 3600 + m * 60 + sec


def _pct(sec: int) -> float:
    """当日秒数转 0..100 百分比（轨道是 00:00-24:00 满轴，不按数据跨度缩放）。"""
    return max(0.0, min(100.0, sec / 86400.0 * 100.0))


def collect(ctx) -> list[dict]:
    """当天全部有效点（时间线在前、视觉在后，各自按时间排序）。

    口径与 DL-18 一致：时间线用 timeline.points()、视觉用 vision.points()，
    本模块不自己重算有效点。注意 timeline.points() 仍只收 >=120s 的行 ——
    「全量覆盖」由 rows_of_segment() 另行统计，两者不可互相替代。
    """
    pts = list(timeline.points(ctx)) + list(vision.points(ctx))
    out = []
    for p in pts:
        sec = _sec(p.get("start"))
        if sec < 0:
            continue
        q = dict(p)
        q["sec"] = sec
        q["pct"] = _pct(sec)
        out.append(q)
    out.sort(key=lambda p: (p["sec"], p["kind"]))
    return out


def rows_of_segment(ctx, seg: str) -> dict:
    """该段全量条目，按设备分组：{dev: [{sec, anchor, label, dev}]}。

    与 collect() 的区别：collect() 是「有效点」（时间线 >=120s、视觉有 summary），
    这里要的是全部条目 —— 用户要的「时间线事件做全」。
    视觉段仍以「有 summary」为准：父片/空片没有正文，跳过去只会到空卡片（DL-17 口径）。

    性能（DL-9 红线）：只读 ctx.merged() 内存缓存 + ctx.slices 内存字典，
    循环里没有任何 SQL。1771 行 x 3 设备纯内存遍历，单日建页不增加可测耗时。
    """
    out: dict = {}
    if seg == "vision":
        for dev in (ctx.devices or []):
            items = []
            for s in (ctx.slices.get(dev) or []):
                if not s.get("summary"):
                    continue
                sec = _sec(s.get("start"))
                if sec < 0:
                    continue
                items.append({"sec": sec,
                              "anchor": vision.card_anchor(s.get("slice_id")),
                              "label": base.span(s.get("start"), s.get("end")),
                              "dev": dev})
            if items:
                out[dev] = items
        return out
    if seg == "timeline":
        for dev in (ctx.devices or []):
            pid = "%s-%s" % (timeline.ANCHOR, dev)
            items = []
            for i, row in enumerate(ctx.merged(dev), 1):
                sec = _sec(row.get("start"))
                if sec < 0:
                    continue
                items.append({"sec": sec,
                              "anchor": timeline.row_anchor(pid, i),
                              "label": timeline.row_text(row, ctx) or "",
                              "dev": dev})
            if items:
                out[dev] = items
        return out
    return out


def bucket_w(secs: list) -> int:
    """桶宽（秒）：max(60, min(900, ceil(span / POINT_CAP)))。空/单点返回最小桶宽。

    这是黄金基线公式（§11.8.1 的 tmp/a0_verify_bucket.py 已证数学自洽）：
    全天 86400 秒时恰好 432 秒 / 200 桶，是该公式的理论上界；
    真实数据（跨度 15-22h）落在 9-101 桶。不要自己另发明公式。
    """
    if not secs:
        return BUCKET_MIN_S
    lo, hi = min(secs), max(secs)
    if hi - lo <= 0:
        return BUCKET_MIN_S
    return max(BUCKET_MIN_S, min(BUCKET_MAX_S, math.ceil((hi - lo) / float(POINT_CAP))))


def pack(items: list) -> tuple:
    """把一行的条目压成标记列表。返回 (桶宽, [标记])。

    标记 = {sec, pct, count, anchor, label}；count==1 表示单点。
    桶的锚点取桶内第一条事件的锚点（先按 sec 稳定排序，同一秒内保持原始行序，
    所以「第一条」是确定的；验收用独立遍历复算同一个判定）。
    """
    if not items:
        return BUCKET_MIN_S, []
    items = sorted(items, key=lambda x: x["sec"])
    if len(items) <= POINT_CAP:
        return BUCKET_MIN_S, [{"sec": it["sec"], "pct": _pct(it["sec"]), "count": 1,
                               "anchor": it["anchor"], "label": it["label"]}
                              for it in items]
    w = bucket_w([it["sec"] for it in items])
    lo = min(it["sec"] for it in items)
    marks: list = []
    for it in items:
        k = (it["sec"] - lo) // w
        if not marks or marks[-1]["k"] != k:
            marks.append({"k": k, "count": 0, "sec": it["sec"],
                          "anchor": it["anchor"], "label": it["label"]})
        marks[-1]["count"] += 1
    for m in marks:
        m["pct"] = _pct(m["sec"])
    return w, marks


def _tip(seg: str, dev: str, m: dict) -> str:
    """悬停提示：设备 + 段 + 时刻 + 标题；聚合桶额外写明「本桶 N 条」。"""
    who = "时间线" if seg == "timeline" else "视觉总结"
    hh = "%02d:%02d" % (m["sec"] // 3600, (m["sec"] % 3600) // 60)
    head = "%s · %s %s" % (hh, device_label(dev), who)
    if m.get("count", 1) > 1:
        head = "%s · 本桶 %d 条" % (head, m["count"])
    label = str(m.get("label") or "").strip()
    if len(label) > 60:
        label = label[:57] + ".."
    return "%s · %s" % (head, label) if label else head


def _mark(seg: str, dev: str, m: dict) -> str:
    """一个标记：单点是 hb-p，聚合桶是 hb-b。条数一律写进 data-c（可被产物侧对拍）。

    体积取舍：旧实现把 title 与 aria-label 写成**同一段长文本**，一条属性重复两遍。
    实测 2026-08-07 光这两样就占 32.6 KB（aria 16.9 + title 15.7）。现在 title 保留
    完整提示（悬停用），aria-label 压到「设备 + 段 + 时刻」的短名（读屏用），
    信息量不减、体积减半。"""
    kind = "tl" if seg == "timeline" else "vi"
    cls = ("hb-p " if m["count"] == 1 else "hb-b ") + kind
    txt = str(m["count"]) if m["count"] > 1 else ""
    who = "时间线" if seg == "timeline" else "视觉总结"
    hh = "%02d:%02d" % (m["sec"] // 3600, (m["sec"] % 3600) // 60)
    short = "%s %s %s" % (device_label(dev), who, hh)
    if m["count"] > 1:
        short += " 共%d条" % m["count"]
    return ('<a class="%s" href="#%s" style="left:%.4f%%" data-c="%d" '
            'title="%s" aria-label="%s">%s</a>') % (
                cls, base.esc(m["anchor"]), m["pct"], m["count"],
                base.esc(_tip(seg, dev, m)), base.esc(short), base.esc(txt))


def _scale() -> str:
    """00-24 刻度。与 .hb-lane 共用同一对左右边界（CSS 里同值），保证刻度线落在点上。"""
    parts = ['<div class="hb-scale"><div class="hb-scale-in">']
    for h in range(0, 24, 3):
        parts.append('<span class="hb-tick" style="left:%.4f%%"></span>' % (h / 24.0 * 100.0))
    for h in (0, 6, 12, 18, 24):
        parts.append('<span class="hb-lab" style="left:%.4f%%">%02d:00</span>'
                     % (h / 24.0 * 100.0, 0 if h == 24 else h))
    parts.append("</div></div>")
    return "".join(parts)


def _axis(seg, name, devs) -> str:
    """一条轴：轴头（含降采样图例）+ 每设备一行 + 刻度。"""
    n_marks = sum(len(d[1]) for d in devs)
    n_items = sum(d[2] for d in devs)
    parts = ['<div class="hb-axis" data-seg="%s">' % seg,
             '<div class="hb-axis-head">%s' % base.esc(name),
             '<span class="hb-axis-n">%s 条 / %d 个标记 / %d 台设备</span>' % (
                 "{:,}".format(n_items), n_marks, len(devs)),
             "</div>",
             '<div class="hb-rows">']
    for dev, marks, total, w in devs:
        parts.append('<div class="hb-row" data-dev="%s" data-seg="%s" '
                     'data-bucket-w="%d" data-n="%d" data-n-marks="%d">'
                     % (base.esc(dev), seg, w, total, len(marks)))
        parts.append('<span class="hb-row-dev">%s</span>' % base.esc(device_label(dev)))
        parts.append('<div class="hb-lane">')
        for m in marks:
            parts.append(_mark(seg, dev, m))
        parts.append("</div></div>")
    parts.append("</div>")
    parts.append(_scale())
    parts.append("</div>")
    return "".join(parts)


def render(ctx) -> str:
    """渲染定位条。当天**两段都没有任何条目**时返回空串（不占版面）。

    ⚠️ 早退条件必须是「全量条目为 0」，**不能用 `collect()`（有效点）判断**（DL-19）。
    实测反例（2026-10-25，主代理全量 753 页验收抓到）：2024-10-16 有 51 条时间线，
    但每段都 < 120 秒 → `collect()` 返回 0 → 旧写法直接返回空串 → **整条定位条不渲染**，
    可那 51 条恰恰是用户要「全部看到」的内容。旧口径（DL-18）与新语义（DL-19）
    不在这里混用：`collect()` 只留给日报口径与旧断言。
    """
    has_items = any(rows_of_segment(ctx, seg) for seg, _ in SEGMENTS)
    if not has_items:
        return ""
    segs = []
    notes = []
    for seg, name in SEGMENTS:
        src = rows_of_segment(ctx, seg)
        devs = []
        for dev in (ctx.devices or []):
            items = src.get(dev) or []
            if not items:
                continue
            w, marks = pack(items)
            devs.append((dev, marks, len(items), w))
            if len(marks) < len(items):
                notes.append("%s %s %s 条 → 按 %d 秒聚合为 %d 点" % (
                    device_label(dev), name, "{:,}".format(len(items)), w, len(marks)))
        segs.append((seg, name, devs))

    n_tl_rows = sum(len(v) for v in rows_of_segment(ctx, "timeline").values())
    n_tl_marks = sum(len(d[1]) for d in segs[0][2])
    n_vi_leaf = sum(len(v) for v in rows_of_segment(ctx, "vision").values())
    n_vi_marks = sum(len(d[1]) for d in segs[1][2])
    # 旧口径（DL-18）只作为**可观测指标**保留在 data-n-timeline / data-n-vision 里，
    # 供旧断言与「有效点数」核对；它**不参与**是否渲染的判定（那是 DL-19 的全量口径）。
    _pts = collect(ctx)
    n_tl_major = sum(1 for p in _pts if p.get("kind") == "timeline")
    n_vi_pt = sum(1 for p in _pts if p.get("kind") == "vision")

    out = ['<div class="hourbar" id="%s" data-n="%d" data-n-timeline="%d" '
           'data-n-vision="%d" data-n-timeline-rows="%d" '
           'data-n-timeline-points="%d" data-n-vision-leaves="%d" '
           'data-n-vision-points="%d">' % (
               ANCHOR, n_tl_marks + n_vi_marks, n_tl_major, n_vi_pt,
               n_tl_rows, n_tl_marks, n_vi_leaf, n_vi_marks)]
    out.append('<div class="hb-top"><span class="hb-title">24 小时</span>'
               '<span class="hb-legend">'
               '<i class="hb-dot tl"></i>时间线 %d'
               '<i class="hb-dot vi"></i>视觉 %d</span>'
               '<span class="hb-legend2">两条轴各按设备分行；每格 3 小时</span></div>'
               % (n_tl_marks, n_vi_marks))
    for seg, name, devs in segs:
        out.append(_axis(seg, name, devs))
    out.append('<p class="hb-note">时间线 <b>%s</b> 行（>=120 秒 <b>%d</b> 行）、'
               '视觉总结 <b>%s</b> 个；时间线轴 <b>%d</b> 个标记、视觉轴 <b>%d</b> 个。'
               '单行超过 %d 点按时间桶聚合，桶内条数写在标记上（data-c）。</p>' % (
                   "{:,}".format(n_tl_rows), n_tl_major, "{:,}".format(n_vi_leaf),
                   n_tl_marks, n_vi_marks, POINT_CAP))
    if notes:
        out.append('<p class="hb-note">降采样：%s 。</p>' % base.esc("；".join(notes)))
    out.append("</div>")
    return "".join(out)