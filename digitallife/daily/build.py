"""build_daily 主编排：取数 -> 渲染 -> （可选）落库。

从 daily.py 拆出（T4，2026-10-25 纯搬迁，不改任何函数体）。
"""
from __future__ import annotations

import json
import sqlite3
import time

from ..ingest.model import DEVICES
from ..ingest.tz import local_day, local_to_utc_ms
from .prompt import _llm_narrative
from .render import _apps_list
from .render_body import render_narrative
from .structure import _event_counts, day_window, device_structure, normalize_date, slices_for_day


LAYER = "day"
LAYERS = (LAYER,)  # 唯一允许的粒度；本模块不产出 week/month/quarter/year

def build_daily(con, date_key: str, client=None, persist: bool = True) -> dict:
    # 本模块内部大量使用 r["col"] 取值，隐含要求连接带 row_factory。
    # 外部脚本/测试用裸 sqlite3.connect() 调进来时会崩在 tuple 下标上，
    # 所以在入口统一兜住，不要求调用方记得设（2026-10-23 实测踩到）。
    if con.row_factory is None:
        con.row_factory = sqlite3.Row
    """汇总当日 → 一段 markdown + device_breakdown，写入 daily_summary（只日层）。"""
    date_key = normalize_date(date_key)
    lo, hi = day_window(date_key)

    all_slices = slices_for_day(con, date_key)
    # slice 表里历史数据仍是旧值（pc/phone），必须按别名集合归拢，
    # 否则改造后该设备一片都匹配不到 -> 日报「时段 0 片」、视觉总结整段消失。
    from ..domain import devices as _dvs
    slices_by_dev = {}
    for d in DEVICES:
        _al = set(_dvs.query_aliases(d))
        slices_by_dev[d] = [s for s in all_slices if s["device"] in _al]
    structures = {d: device_structure(con, date_key, d) for d in DEVICES}

    devices: dict[str, dict] = {}
    structured_devices: dict[str, dict] = {}
    for dev in DEVICES:
        st = structures[dev]
        sl = slices_by_dev[dev]
        totals = st.get("totals") or {}
        n_events, kinds = _event_counts(con, lo, hi, dev)
        apps = _apps_list(st)
        summary_count = sum(1 for s in sl if (s.get("overall_summary") or "").strip())
        devices[dev] = {
            "date": date_key, "device": dev,
            "active_s": int(totals.get("active_s") or 0),
            "away_s": int(totals.get("away_s") or 0),
            "locked_s": int(totals.get("locked_s") or 0),
            "event_count": n_events, "kinds": kinds,
            "switch_count": int(st.get("switch_count") or 0),
            "focus_count": len(st.get("focus_blocks") or []),
            "screen_count": len(st.get("screens") or []),
            "slice_count": len(sl), "summary_count": summary_count,
            "image_count": sum(int(s.get("image_count") or 0) for s in sl),
            "apps_top": [{"app": a.get("app") or a.get("app_id"),
                          "seconds": int(a.get("seconds") or 0)} for a in apps[:10]],
            "source_basis": st.get("source_basis", "event"),
        }
        structured_devices[dev] = {
            "active_s": devices[dev]["active_s"],
            "away_s": devices[dev]["away_s"],
            "locked_s": devices[dev]["locked_s"],
            "switch_count": devices[dev]["switch_count"],
            "focus_blocks": devices[dev]["focus_count"],
            "screens": devices[dev]["screen_count"],
            "slices": len(sl),
            "summaries": summary_count,
            "apps_top": devices[dev]["apps_top"],
        }

    breakdown = {
        "date": date_key, "layer": LAYER, "devices": devices,
        "note": "各设备时长独立记账，不相加为总时长（同日并行，相加即重复计入）",
    }

    # ---- 叙事：优先复用库里已存的（唯一不可重算的东西），没有再问模型
    # 用户要求（2026-10-21）：日报正文由模型对当天片段做二次总结，用自然段写。
    # 「模型不可用」不等于「把已写好的叙事丢掉」：库里若已存模型写的叙事，
    # 复用它重新渲染版式（改版式不是改内容）。否则一次无模型的重跑
    # 会把这天唯一的生产内容降级成模板 —— 那是净损失。
    prev = con.execute(
        "SELECT structured_json, model FROM daily_summary WHERE date_key = ?",
        (date_key,)).fetchone()
    stored_narratives: dict = {}
    narrative = None
    model = None
    if prev is not None:
        try:
            old = json.loads(prev["structured_json"] or "{}")
        except (TypeError, ValueError):
            old = {}
        if not isinstance(old, dict):
            old = {}
        if isinstance(old.get("narratives"), dict):
            stored_narratives = {str(k): str(v or "").strip()
                                 for k, v in old["narratives"].items() if str(v or "").strip()}
        kept = (old.get("narrative") or "").strip()
        if kept:
            narrative = kept
            model = prev["model"]

    narratives: dict = dict(stored_narratives)
    have_dev = [d for d in DEVICES
                if devices[d]["event_count"] or devices[d]["slice_count"]]
    if client is not None and have_dev:
        # 按设备生成（三级降级的第 1 级）。**只为缺叙事的设备调模型**：
        # 库里已有叙事的日子重跑一次全量，不会又烧 753 次调用。
        for _dev in have_dev:
            if narratives.get(_dev):
                continue
            _txt, _mdl = _llm_narrative(client, date_key, breakdown, slices_by_dev, device=_dev)
            if _txt:
                narratives[_dev] = _txt
                model = model or _mdl
    if not have_dev:
        # 没设备可归属时退回整天叙事（保持旧行为，不丢内容）
        _txt, _mdl = _llm_narrative(client, date_key, breakdown, slices_by_dev)
        if _txt:
            narrative, model = _txt, _mdl
    # 日报只有一条渲染路径：模型叙事（2026-10-24 用户裁决）。
    #
    # 用户原话：「我不需要日报里的视觉总结搞模板铁铁，render_daily 这种输出长列表的无效」。
    # 旧逻辑是 narrative 为空就走 render_daily —— 那条路径会输出「## 关键操作」
    # 加上当天每片的每条 action（2026-08-14 实测 168 条），正是被反复否掉的流水账。
    # 模板路径已不再被调用；没有叙事时 render_narrative 只出陈述性统计，不出长列表。
    output = render_narrative(date_key, breakdown, structures, slices_by_dev, narrative,
                              narratives=narratives)

    structured = {
        "date": date_key, "layer": LAYER, "devices": structured_devices,
        "slices": [{"slice_id": s["slice_id"], "device": s["device"],
                    "start": s["start"], "end": s["end"],
                    "has_summary": bool((s.get("overall_summary") or "").strip())}
                   for s in all_slices],
        "counts": {"event": sum(v["event_count"] for v in devices.values()),
                   "slice": len(all_slices),
                   "slice_with_summary": sum(v["summary_count"] for v in devices.values())},
        "narrative": narrative,
        "narratives": narratives,
    }

    # 只存"模型叙事原文"，不存渲染结果（2026-10-24 用户裁决：
    # 「日报不需要存储到数据库…本身就是基于数据库内容进行的导出」）。
    #   - narrative 是唯一不可重算的东西（要么调用模型，要么丢）
    #   - output_text / structured / breakdown 全部可由 slice+event 现算
    # persist=False（publish 走的路径）连 narrative 都不写，只把结果返回。
    if persist:
        _prev = con.execute(
            "SELECT structured_json FROM daily_summary WHERE date_key = ?", (date_key,)
        ).fetchone()
        _keep = (narrative or "")
        _keep_dev: dict = {}
        if _prev:
            try:
                _oldj = json.loads(_prev["structured_json"] or "{}")
            except Exception:
                _oldj = {}
            if isinstance(_oldj, dict):
                if not _keep:
                    _keep = str(_oldj.get("narrative") or "")
                if isinstance(_oldj.get("narratives"), dict):
                    _keep_dev = {str(k): str(v or "") for k, v in _oldj["narratives"].items()
                                 if str(v or "").strip()}
        # 本次算出的按设备叙事覆盖旧的（两者都是"模型原文"，不可重算，必须留住）
        _keep_dev.update({k: v for k, v in narratives.items() if str(v or "").strip()})
        con.execute(
            "INSERT INTO daily_summary(date_key, model, output_text, structured_json, "
            "device_breakdown_json, created_at) VALUES(?,?,?,?,?,?) "
            "ON CONFLICT(date_key) DO UPDATE SET model=excluded.model, "
            "output_text=excluded.output_text, structured_json=excluded.structured_json, "
            "device_breakdown_json=excluded.device_breakdown_json, created_at=excluded.created_at",
            (date_key, model, output,
             json.dumps({"_narrative_only": True, "narrative": _keep,
                         "narratives": _keep_dev,
                         "layer": LAYER, "date": date_key}, ensure_ascii=False),
             json.dumps({}, ensure_ascii=False),
             int(time.time() * 1000)),
        )

    return {
        "date": date_key, "layer": LAYER, "output_text": output,
        "device_breakdown": breakdown, "structured": structured,
        "narratives": narratives,
        "slices": len(all_slices), "model": model, "counts": structured["counts"],
    }
