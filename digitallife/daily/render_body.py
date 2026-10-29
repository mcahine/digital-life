"""设备节与日报正文的组装（六节骨架 + 叙事落位）。

从 daily.py 拆出（T4，2026-10-25 纯搬迁，不改任何函数体）。
"""
from __future__ import annotations

from ..ingest.model import DEVICES
from ..domain.timeline_merge import merge_browser_web
from .render import (
    _BT, _EMPTY_SECTION, _KEY_ACTION_LIMIT, _NARR_MISSING_NONE,
    _NARR_MISSING_OTHERS, _TIMELINE_LIMIT, _TIMELINE_MIN_S, _compress_timeline,
    _span, _strip_md_heads, _tl_line, _top_apps_section,
    _top_sites_section
)
from .structure import _cell, _fmt_dur


def _device_label(dev: str) -> str:
    """`## <设备> 使用` 里的设备名：**一律**用真实设备名（D-001 / iQQQZ3 / iQQQ Neo11）。

    2026-10-25 用户裁决：「电脑使用，直接用设备名表示，就是 D-001」。
    旧规则「同类只有一台时沿用电脑/手机」已删除 —— 它让 pc-main 显示成「电脑」，
    与网页侧 contract.device_label()（本来就用真名）口径不一致。契约见
    04-Areas/数字人生/AGENTS.md §D.1（先改契约后改代码）。

    旧库裸值（pc/phone）由 devices.LEGACY_ALIASES 解析到注册 id，故 name_of("pc")
    返回 "D-001"；只有**完全未登记**的值才走 except 退回原值（实测 name_of 内部已兜底，
    不抛异常，此处保留只为防御）。
    """
    from ..domain import devices as _dev_mod
    try:
        return _dev_mod.name_of(dev) or dev
    except ValueError:
        return dev


def _cjk_gap_before(text: str) -> str:
    """中文与西文（ASCII 字母/数字）相邻处补一个空格。

    为什么需要它：`D-001` 这类设备真名以 ASCII 打头，直接接在中文后面会拼出
    「设备是D-001」「## D-001使用」这种没有间隔的排印（用户 2026-10-25 改成设备名后
    才暴露 —— 旧的「电脑」是汉字，本来就不需要空格）。

    单一出处：`_device_heading()` 与元信息行都调它，不得各写一套（S51）。
    """
    s = str(text or "")
    if not s:
        return s
    if s[0].isascii() and s[0].isalnum():
        return " " + s
    return s


def _device_heading(dev: str) -> str:
    """`## <label> 使用` 标题行。

    中文与英文/数字之间留一个空格：旧写法 f"## {label}使用" 会拼出「手机 1使用」。
    """
    label = _device_label(dev)
    tail = " " if label and label[-1].isascii() and label[-1].isalnum() else ""
    return f"## {label}{tail}使用"


def _render_device(dev: str, bd: dict, st: dict, sl: list[dict],
                   include_summaries: bool = True,
                   narrative: str | None = None,
                   narrative_why: str = "") -> list[str]:
    """渲染一台设备节：**六个 `###` 一节不少、顺序固定**（2026-10-25 用户骨架）。

    参数：
      narrative       本节视觉总结要放的**模型叙事正文**（已渲染好的 markdown）。
                      为 None 时按 narrative_why 写缺省说明。
      narrative_why   缺省说明行（三级降级文案），仅当 narrative 为空时使用。
    """
    lines = [_device_heading(dev), ""]
    if bd["event_count"] or bd["slice_count"]:
        lines.append(f"- 活跃 **{_fmt_dur(bd['active_s'])}**；离开 {_fmt_dur(bd['away_s'])}、"
                     f"锁屏 {_fmt_dur(bd['locked_s'])} 已从活跃时长中剔除（C13）")
        k = bd["kinds"]
        lines.append(f"- 事件 {bd['event_count']} 条（app {k.get('app', 0)} / "
                     f"web {k.get('web', 0)} / screen {k.get('screen', 0)} / "
                     f"power {k.get('power', 0)}）；时段 {bd['slice_count']} 片，"
                     f"视觉总结 {bd['summary_count']} 片；切换 {bd['switch_count']} 次")
        if bd["slice_count"] and not bd["image_count"]:
            lines.append("- 当日仅活动，无截图")
    # 无论有没有数据，六节都必须出 —— 结构恒定是硬要求。
    lines += _render_device_body(dev, bd, st, sl, include_summaries,
                                 narrative, narrative_why)
    return lines + [""]


def _render_device_body(dev: str, bd: dict, st: dict, sl: list[dict],
                        include_summaries: bool,
                        narrative: str | None, narrative_why: str) -> list[str]:
    """六节正文。**只产出正文，不产出 `##` 设备标题**（标题由 _render_device 给）。"""
    date_key = bd.get("date") or ""
    sl = sl or []
    n_sum = sum(1 for s in sl if (s.get("overall_summary") or "").strip())
    tail = (f"- 本设备当日 {len(sl)} 个时段、{n_sum} 片有画面级总结；"
            f"逐片全文见时段网页{_BT}10-时段/{date_key}.html#vision{_BT}")
    out: list[str] = []

    # ---- 1) 应用
    apps_body = _top_apps_section(st, date_key, "")
    out += ["", "### 应用", ""]
    out += apps_body if apps_body else [_EMPTY_SECTION]

    # ---- 2) 网站
    sites_body = _top_sites_section(st, date_key, "")
    out += ["", "### 网站", ""]
    out += sites_body if sites_body else [_EMPTY_SECTION]

    # ---- 3) 专注段
    focus = st.get("focus_blocks") or []
    out += ["", "### 专注段", ""]
    if focus:
        for b in focus[:10]:
            out.append(f"- {_span(b.get('start'), b.get('end'))} "
                       f"{_cell(b.get('app'))} · {_fmt_dur(b.get('seconds'))}")
    else:
        out.append(_EMPTY_SECTION)

    # ---- 4) 时间线
    tl = st.get("timeline") or []
    out += ["", "### 时间线", ""]
    if tl:
        merged = merge_browser_web(tl)
        kept = _compress_timeline(merged, min_seconds=_TIMELINE_MIN_S)
        # 标题与引用块之间必须留空行，否则 markdown 渲染时 > 会紧贴标题。
        out += [f"> 只保留单段 ≥{int(_TIMELINE_MIN_S // 60)} 分钟的事件；"
                f"完整 {len(tl)} 条见时段网页（`scripts/build_slice_html.py`）。", ""]
        for t in kept[:_TIMELINE_LIMIT]:
            out.append(_tl_line(t))
            # 时间线**不做嵌套子行**（用户 2026-10-22：
            # 「都不是一个事件的为什么要搞一个次级列表，搞得好像它们是一回事似的」）。
            # merge 阶段已保证：只有时间窗完全一致的行为才合并成一行，
            # 其余网页各自独立成行 —— 所以这里 sub 基本恒为空，保持不渲染。
            extra = [s for s in (t.get("sub") or []) if (s.get("url") or "").strip()]
            if extra:
                out[-1] += " 等 %d 页" % (len(extra) + 1)
        if len(kept) > _TIMELINE_LIMIT:
            out.append(f"- 其余 {len(kept) - _TIMELINE_LIMIT} 条见时段网页")
        elif len(merged) > len(kept):
            # merged 是合并后的行数（网页已挂到父行），不能再与原始 tl 条数混算
            out.append(f"- 另有 {len(merged) - len(kept)} 条碎片事件"
                       f"（<{int(_TIMELINE_MIN_S // 60)} 分钟）已折叠")
    else:
        out.append(_EMPTY_SECTION)

    # ---- 5) 离开时段
    away = st.get("away") or []
    out += ["", "### 离开时段", ""]
    if away:
        for a in away[:10]:
            out.append(f"- {_span(a.get('start'), a.get('end'))} "
                       f"{_cell(a.get('reason') or 'Away')}")
    else:
        out.append(_EMPTY_SECTION)

    # ---- 6) 视觉总结（叙事体落位：用户「（叙事体存放）」= 这一节下面放叙事自然段）
    out += ["", "### 视觉总结", ""]
    # 叙事原文自带 `#` 级标题（2026-08-13 那篇有 H1 + 7 个 H2）。
    # 这里必须过 _strip_md_heads —— 它把标题行**降级成加粗**（不是删除，
    # 删了会丢掉叙事的层次且不可重算）。复用库里的旧叙事时同样要过这一道，
    # 否则模型写的小标题会直接顶乱日报骨架（实测：漏这一步立刻出现 `## 凌晨启动：…`）。
    body = _strip_md_heads(narrative or "").strip()
    if body:
        out += [body, ""]
        # 叙事之外的客观规模行仍留着：它是「今天到底有多少数据」的唯一出口
        if sl:
            out.append(tail)
    else:
        out.append(narrative_why or _NARR_MISSING_NONE)
        if sl and not include_summaries:
            out.append(tail)
    return out



def _no_visual_basis(sl: dict) -> bool:
    """该片是否**没有**画面描述（判据同 slice_pages/vision.py）。

    真值在事件层 payload.description，由 slices_for_day 一并取出并挂在 `has_description`。
    不能用 structured 里的 described_images —— 纯文本模式下它们恒为空。
    """
    hd = sl.get("has_description")
    if hd is None:
        return False
    return not hd


def _actions_of(structured) -> list[str]:
    if not isinstance(structured, dict):
        return []
    items = structured.get("key_actions")
    if items is None:
        items = structured.get("keyActions")
    if isinstance(items, dict):
        items = [items]
    out: list[str] = []
    if isinstance(items, list):
        for it in items:
            if isinstance(it, str) and it.strip():
                out.append(" ".join(it.split()))
            elif isinstance(it, dict):
                for key in ("detail", "text", "action", "title", "desc", "summary"):
                    v = it.get(key)
                    if isinstance(v, str) and v.strip():
                        out.append(" ".join(v.split()))
                        break
    return out


def _render_key_actions(slices_by_dev: dict[str, list[dict]],
                        date_key: str = "") -> list[str]:
    # 当日关键操作的摘要（不是全量流水账）。
    # R12：日报只写突出部分，全量明细归时段网页。2026-10-24 之前这里把每片的每条
    # action 都列出来 —— 2026-09-15 实测 248 条，日报沦为逐条时间戳的流水账。
    lines = ["## 关键操作", "",
             "> 当日较突出的操作（全量见时段网页「视觉」区）；DB 时间线为基线（R3）。", ""]
    rows: list[tuple[int, str, str]] = []
    for dev in DEVICES:
        for s in slices_by_dev.get(dev, []):
            for txt in _actions_of(s.get("structured")):
                from ..domain import devices as _dm
                try:
                    _k = _dm.kind_of(dev)
                    _lb = _dm.label_of(dev)
                except ValueError:
                    _k, _lb = ("pc" if dev.startswith("pc") else "phone"), dev
                tag = _lb if len(_dm.by_kind(_k)) > 1 else ("电脑" if _k == "pc" else "手机")
                rows.append((int(s["start_utc_ms"]), dev,
                             f"{_span(s['start'], s['end'])} [{tag}] {txt}"))
    rows.sort(key=lambda x: (x[0], x[1]))
    if not rows:
        return lines + ["- （当日无结构化关键操作；见上方时间线与视觉总结）", ""]
    for _, _, txt in rows[:_KEY_ACTION_LIMIT]:
        lines.append(f"- {txt}")
    if len(rows) > _KEY_ACTION_LIMIT:
        _where = (f"{_BT}10-时段/{date_key}.html#vision{_BT}" if date_key
                  else f"{_BT}10-时段/{_BT}")
        lines.append(f"- 其余 {len(rows) - _KEY_ACTION_LIMIT} 条见 {_where}")
    return lines + [""]


def _device_narratives(breakdown: dict, narrative: str | None,
                      narratives: dict | None) -> dict[str, tuple[str, str]]:
    """按设备分配叙事正文，**三级降级**（2026-10-25 用户裁决，不许自创第四级）。

    1. `structured_json.narratives = {"pc-main": "...", "phone-1": "..."}` —— 按设备叙事，首选。
    2. 只有整天 `narrative` 时：**只放在第一个有数据的设备节**下，其余设备节写
       「本节未单独生成叙事」。2026-08-13 那 9,331 字孤本因此既不丢、也不重复三遍。
    3. 两者都没有（752 天的现状）：写「尚无模型叙事，见时段网页」。

    返回 {device_id: (正文, 缺省说明行)}。
    """
    dd = breakdown["devices"]
    have = [d for d in DEVICES if dd[d]["event_count"] or dd[d]["slice_count"]]
    out: dict[str, tuple[str, str]] = {}
    # 注意：**不得把换行压成空格**。2026-08-13 的叙事是 51 个自然段，
    # 压成一行等于毁掉段落结构（曾在这一行写 " ".join(...split("\n"))，一次就毁掉）。
    per = {k: str(v or "").strip()
           for k, v in (narratives or {}).items() if str(v or "").strip()} \
        if isinstance(narratives, dict) else {}
    first = have[0] if have else None
    for d in DEVICES:
        body = per.get(d) or ""
        if body:
            out[d] = (body, "")
            continue
        if (narrative or "").strip() and d == first:
            out[d] = (narrative or "", "")
            continue
        if (narrative or "").strip():
            out[d] = ("", _NARR_MISSING_OTHERS)
            continue
        out[d] = ("", _NARR_MISSING_NONE)
    return out


def render_narrative(date_key: str, breakdown: dict, structures: dict,
                     slices_by_dev: dict, narrative: str | None = None,
                     narratives: dict | None = None) -> str:
    """日报正文：用户 2026-10-25 定稿骨架（**结构唯一**，753 篇归一化后只剩 1 种模式）。

        # YYYY-MM-DD 数字人生日报
        > 元信息（含当天统计陈述）
        ## <设备> 使用           ← 每台当天有数据的设备一节，顺序 pc-main → phone-1 → phone-2
        ### 应用 / ### 网站 / ### 专注段 / ### 时间线 / ### 离开时段 / ### 视觉总结
        > 文末数据规模 + 全量指路

    三个「为什么」：
    - 六个 ### **一节都不能少**：旧实现每节都是 `if focus:` 之类，没数据就整节消失，
      753 篇因此长出 28 种结构模式。现在没数据写「本设备当日无此类记录」。
    - `## 今天` 与 `## 展开看全量` **已删除**：用户新骨架里没有这两个二级节。
      前者那句统计陈述并进元信息行，后者那句指路移到文末引用块（它不是章节）。
    - 叙事体落位在**每台设备自己的 `### 视觉总结` 下**（用户括号注「叙事体存放」的位置）。
    """
    dd = breakdown["devices"]

    has_data = [d for d in DEVICES if dd[d]["event_count"] or dd[d]["slice_count"]]
    total_events = sum(v["event_count"] for v in dd.values())
    total_slices = sum(v["slice_count"] for v in dd.values())
    total_sums = sum(v["summary_count"] for v in dd.values())
    labels = "、".join(_device_label(d) for d in has_data)

    narr_map = _device_narratives(breakdown, narrative, narratives)

    out = ["---", "date: %s" % date_key, "source: digital-life", "---", "",
           "# %s 数字人生日报" % date_key, "",
           "> 机器生成（日层）。周/月/季/年由日记系统自行滚动。"]

    if not has_data:
        out += ["", "- 这一天没有任何设备使用记录。", ""]
    else:
        # 原 `## 今天` 里那句统计陈述 —— 并进元信息行（用户骨架里没有 `## 今天`）
        out.append("> 今天有记录的设备是%s，共 %d 条事件、切成 %d 个时段。"
                   % (_cjk_gap_before(labels), total_events, total_slices))
        out.append("")
        for dev in has_data:
            v = dd[dev]
            body, why = narr_map.get(dev, ("", _NARR_MISSING_NONE))
            out += _render_device(dev, v, structures.get(dev) or {},
                                  slices_by_dev.get(dev, []),
                                  include_summaries=True,
                                  narrative=body, narrative_why=why)

    # 原 `## 展开看全量` 那句指路 —— 移到文末引用块（它不是章节，不占 ## 标题）
    all_sl = [(d, s) for d in DEVICES for s in (slices_by_dev.get(d) or [])]
    all_sl.sort(key=lambda p: (str(p[1].get("start") or ""), p[0]))
    n_with = sum(1 for _d, s in all_sl if (s.get("overall_summary") or "").strip())
    if all_sl:
        note = ("全量明细见时段网页%s（本日共 %d 个时段、%d 片有画面级总结）。"
                % (_BT + "10-时段/%s.html#vision" % date_key + _BT, len(all_sl), n_with))
    else:
        note = "全量明细：本日没有截图，以上内容全部来自应用与网页记录。"

    out += ["---", "",
            "> 数据规模：%s，共 %d 条事件、%d 个时段、%d 片有视觉总结。"
            % (labels, total_events, total_slices, total_sums),
            "> " + note, ""]

    return "\n".join(out).rstrip("\n") + "\n"


def render_daily(date_key: str, breakdown: dict, structures: dict[str, dict],
                 slices_by_dev: dict[str, list[dict]], narrative: str | None = None) -> str:
    """[已停用] 模板渲染路径，保留仅为兼容旧调用方。

    2026-10-24 用户裁决：「我不需要日报里的视觉总结搞模板铁铁，
    render_daily 这种输出长列表的无效」。

    它的问题在 _render_key_actions：把当天每片的每条 action 都列出来
    （2026-08-14 实测 168 条、2026-09-15 实测 248 条），日报沦为流水账。
    build_daily 已改为只走 render_narrative；这里直接委托过去，
    避免任何残留调用产生长列表。
    """
    return render_narrative(date_key, breakdown, structures, slices_by_dev, narrative)


# ---------------------------------------------------------------- 可选：模型概述
