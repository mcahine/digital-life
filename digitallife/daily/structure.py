"""当日结构：DB 事实 -> 应用时长/离开/电源/专注段。

从 daily.py 拆出（T4，2026-10-25 纯搬迁，不改任何函数体）。
"""
from __future__ import annotations

import sqlite3
from datetime import datetime

from ..domain import dbview
from ..ingest import store
from ..ingest.model import DEVICES
from ..textutil import parse_json as _parse_json
from ..ingest.tz import local_day, local_to_utc_ms, utc_ms_to_local


def normalize_date(date_key: str) -> str:
    s = (date_key or "").strip()
    try:
        return datetime.strptime(s, "%Y-%m-%d").strftime("%Y-%m-%d")
    except ValueError as e:
        raise ValueError(f"date_key 必须是 YYYY-MM-DD，收到 {date_key!r}") from e


def day_window(date_key: str) -> tuple[int, int]:
    """本地日 → [lo_ms, hi_ms) 的 UTC 毫秒区间。"""
    from ..ingest import tz

    d = tz.parse_local(f"{normalize_date(date_key)} 00:00:00")
    lo = tz.local_to_utc_ms(d)
    return lo, lo + 86_400_000


def _hms(ms: int) -> str:
    from ..ingest import tz

    return tz.utc_ms_to_local(int(ms)).strftime("%H:%M:%S")


def _hm(ms: int) -> str:
    from ..ingest import tz

    return tz.utc_ms_to_local(int(ms)).strftime("%H:%M")


def _fmt_dur(seconds) -> str:
    s = int(seconds or 0)
    if s <= 0:
        return "0 分"
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    if h:
        return f"{h} 小时 {m} 分"
    if m:
        return f"{m} 分"
    return f"{sec} 秒"


def _cell(v) -> str:
    return str(v if v is not None else "").replace("|", "/").replace("\n", " ").strip()



# ---------------------------------------------------------------- 区间运算

def _clip(iv: tuple[int, int], lo: int, hi: int) -> tuple[int, int] | None:
    s, e = max(iv[0], lo), min(iv[1], hi)
    return (s, e) if e > s else None


def _merge(ints: list[tuple[int, int]]) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for s, e in sorted(ints):
        if out and s <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], e))
        else:
            out.append((s, e))
    return out


def _total(ints: list[tuple[int, int]]) -> int:
    return sum(e - s for s, e in ints)


def _intersect(a: list[tuple[int, int]], b: list[tuple[int, int]]) -> list[tuple[int, int]]:
    res: list[tuple[int, int]] = []
    i = j = 0
    while i < len(a) and j < len(b):
        s, e = max(a[i][0], b[j][0]), min(a[i][1], b[j][1])
        if e > s:
            res.append((s, e))
        if a[i][1] < b[j][1]:
            i += 1
        else:
            j += 1
    return res


def _classify_power(title: str | None) -> str | None:
    """power 事件 → 'locked' / 'away' / None（Active 不算离开，不从时长里剔除）。"""
    t = (title or "").strip().lower()
    if not t:
        return None
    if "lock" in t or "锁屏" in t or "锁定" in t:
        return "locked"
    if "away" in t or "离开" in t or "idle" in t:
        return "away"
    return None


# ---------------------------------------------------------------- 当日结构

def _fresh_structure(date_key: str, device: str) -> dict:
    return {"date": date_key, "device": device, "apps": [], "timeline": [],
            "switch_count": 0, "focus_blocks": [], "away": [], "locked": [],
            "screens": [], "totals": dict(_DEFAULT_TOTALS)}


def _focus_blocks(rows, lo: int, hi: int) -> list[dict]:
    """同一 app 连续 ≥15 分钟的使用段（一律按毫秒算，不按展示串）。"""
    blocks: list[dict] = []
    run_key = None
    run_start = None
    run_end = None

    def _flush() -> None:
        if run_key is not None and run_start is not None and run_end is not None:
            secs = int((run_end - run_start) // 1000)
            if secs >= _MIN_FOCUS_S:
                blocks.append({"start": _hms(run_start), "end": _hms(run_end),
                               "app": run_key, "seconds": secs})

    for r in rows:
        if r["kind"] not in ("app", "web"):
            continue
        iv = _clip((int(r["start_utc_ms"]), int(r["end_utc_ms"] or r["start_utc_ms"])), lo, hi)
        if iv is None:
            continue
        st, en = iv
        key = r["app"] or r["title"] or "(未知)"
        if key == run_key and run_end is not None and st - run_end <= _GAP_MERGE_MS:
            run_end = max(run_end, en)
            continue
        _flush()
        run_key, run_start, run_end = key, st, en
    _flush()
    return blocks


def _structure_fallback(con, date_key: str, device: str) -> dict:
    """纯 event 表实现（dbview 未就绪时的降级路径），口径对齐 dbview。"""
    from ..ingest import store

    lo, hi = day_window(date_key)
    rows = store.events_between(con, lo, hi, device=device)
    apps: dict[str, dict] = {}
    timeline: list[dict] = []
    away_iv: list[tuple[int, int]] = []
    locked_iv: list[tuple[int, int]] = []
    app_iv: list[tuple[int, int]] = []
    away_list: list[dict] = []
    locked_list: list[dict] = []
    screens: list[dict] = []
    switch = 0
    prev_key = None

    for r in rows:
        st = int(r["start_utc_ms"])
        en = int(r["end_utc_ms"] or r["start_utc_ms"])
        kind = r["kind"]
        if kind == "power":
            cls = _classify_power(r["title"])
            if cls == "away":
                away_iv.append((st, en))
                away_list.append({"start": _hms(st), "end": _hms(en), "reason": r["title"]})
            elif cls == "locked":
                locked_iv.append((st, en))
                locked_list.append({"start": _hms(st), "end": _hms(en), "reason": r["title"]})
            continue
        if kind == "screen":
            screens.append({"event_id": r["event_id"], "path": r["path"], "ts": _hms(st)})
            continue
        if kind in ("app", "web"):
            iv = _clip((st, en), lo, hi)
            if iv:
                app_iv.append(iv)
            key = r["app"] or r["title"] or "(未知)"
            d = apps.get(key)
            if d is None:
                d = apps[key] = {"app": r["app"] or key, "app_id": r["app_id"],
                                 "seconds": 0, "sessions": 0}
            d["seconds"] += max(0, (en - st) // 1000)
            d["sessions"] += 1
            timeline.append({"start": _hms(st), "end": _hms(en), "app": r["app"],
                             "title": r["title"], "url": r["url"]})
            if prev_key is not None and key != prev_key:
                switch += 1
            prev_key = key

    merged_app = _merge(app_iv)
    excluded = _merge(away_iv + locked_iv)
    active_ms = max(0, _total(merged_app) - _total(_intersect(merged_app, excluded)))

    st_out = _fresh_structure(date_key, device)
    st_out.update({
        "apps": sorted(apps.values(), key=lambda a: (-a["seconds"], a["app"] or "")),
        "timeline": timeline,
        "switch_count": switch,
        "focus_blocks": _focus_blocks(rows, lo, hi),
        "away": away_list,
        "locked": locked_list,
        "screens": screens,
        "totals": {
            "active_s": active_ms // 1000,
            "away_s": _total(_merge(away_iv)) // 1000,
            "locked_s": _total(_merge(locked_iv)) // 1000,
        },
        "source_basis": "event",
    })
    return st_out


def device_structure(con, date_key: str, device: str) -> dict:
    """优先 dbview.build_day_structure；未就绪（ImportError）时走纯 event 降级。"""
    build_day_structure = None
    try:
        from ..domain.dbview import build_day_structure
    except ImportError:
        build_day_structure = None

    data = None
    if build_day_structure is not None:
        try:
            got = build_day_structure(con, date_key, device)
        except Exception:  # noqa: BLE001 —— dbview 故障不得让日报整条崩掉
            got = None
        if isinstance(got, dict):
            data = got

    if data is None:
        return _structure_fallback(con, date_key, device)

    data.setdefault("date", date_key)
    data["device"] = data.get("device") or device
    data.setdefault("apps", [])
    data.setdefault("timeline", [])
    data.setdefault("switch_count", 0)
    data.setdefault("focus_blocks", [])
    data.setdefault("away", [])
    data.setdefault("locked", [])
    data.setdefault("screens", [])
    totals = data.get("totals")
    if not isinstance(totals, dict):
        totals = {}
    for k, v in _DEFAULT_TOTALS.items():
        totals.setdefault(k, v)
    data["totals"] = totals
    data["source_basis"] = "dbview"
    return data


# ---------------------------------------------------------------- 当日切片

def slices_for_day(con, date_key: str, device: str | None = None) -> list[dict]:
    lo, hi = day_window(date_key)
    sql = ("SELECT s.slice_id, s.device, s.start_utc_ms, s.end_utc_ms, s.image_count, "
           "s.event_count, s.status, r.overall_summary, r.structured_json, r.conflict_count "
           "FROM slice s LEFT JOIN slice_result r ON r.slice_id = s.slice_id "
           "WHERE s.start_utc_ms >= ? AND s.start_utc_ms < ?")
    args: list = [lo, hi]
    if device:
        from ..domain import devices as _dv
        clause, aliases = _dv.sql_device_filter(device, "s.device")
        sql += " AND " + clause
        args.extend(aliases)
    sql += " ORDER BY s.start_utc_ms, s.device"
    out: list[dict] = []
    # 一次性取出本日所有「带画面描述」的 screen 事件起始时间（毫秒）。
    # 逐片查库会退化成「每片扫全表 screen 事件」，实测单日要 70–103 秒。
    desc_ts = [row[0] for row in con.execute(
        "SELECT start_utc_ms FROM event "
        "WHERE start_utc_ms >= ? AND start_utc_ms < ? AND kind='screen' "
        "AND payload LIKE '%\"description\"%'", (lo, hi))]
    for r in con.execute(sql, args):
        d = dict(r)
        d["structured"] = _parse_json(d.get("structured_json"))
        # 画面描述的真值在事件层。用批量取出的时间点判断，不逐片查库
        # （逐片查会退化成每片扫全表，实测单日 70–103 秒）。
        _ss = int(d["start_utc_ms"]); _ee = int(d["end_utc_ms"])
        d["has_description"] = any(_ss <= t < _ee for t in desc_ts)
        d["start"] = _hm(d["start_utc_ms"])
        d["end"] = _hm(d["end_utc_ms"])
        out.append(d)
    return out


def _event_counts(con, lo: int, hi: int, device: str) -> tuple[int, dict[str, int]]:
    # 设备在库里可能以旧值（pc/phone）或注册 id（pc-main/phone-1）存在，必须全部计入。
    # 曾因这里精确匹配 device=?，改造后 event_count 恒为 0，日报整篇显示「当日无数据」。
    from ..domain import devices as _dv
    aliases = _dv.query_aliases(device)
    rows = con.execute(
        "SELECT kind, COUNT(*) FROM event WHERE device IN (" + ",".join(["?"] * len(aliases)) +
        ") AND start_utc_ms>=? AND start_utc_ms<? GROUP BY kind", (*aliases, lo, hi),
    ).fetchall()
    kinds = {r[0]: int(r[1]) for r in rows}
    return sum(kinds.values()), kinds


# ---------------------------------------------------------------- markdown

_MIN_FOCUS_S = 15 * 60
_GAP_MERGE_MS = 120_000
_DEFAULT_TOTALS = {"active_s": 0, "away_s": 0, "locked_s": 0}
