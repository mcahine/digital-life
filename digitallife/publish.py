"""落盘 + 留存包 —— 把 DB 里的生成物写进 vault，并做可重放的全量导出。

落点（均为生成物，同名整体覆盖，不是人工记录）：
  VAULT/10-时段/YYYY-MM-DD/HHMM-HHMM.md   每片一篇（含 DB 时间线与 overall_summary）
  VAULT/20-日报/YYYY-MM-DD.md             机器总结（frontmatter 仅 date 与 source）
  INDEX_DIR/exports/digital-life-events.ndjson   全量 event（含 payload）
  INDEX_DIR/manifest.json                 generated_at / counts / sources / files
  INDEX_DIR/exports/digital-life-YYYYMMDD.zip   只含 ndjson + manifest

可重放（R6）：ndjson 按固定键排序、md 不含运行时刻，因此两次 publish 的
files[].sha256 与 counts 完全一致；manifest 里唯一逐次变化的是 generated_at。
本模块不写 04-Areas/GTD/ 下任何文件。
"""
from __future__ import annotations

import hashlib
import json
import os
import time
import zipfile
from pathlib import Path

from . import paths, tz
from .model import EVENT_COLUMNS

_MD_HEADER_NOTE = "> 机器生成（digital-life），同名整体覆盖；不是人工记录。"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _now_iso() -> str:
    return tz.utc_ms_to_local(int(time.time() * 1000)).isoformat(timespec="seconds")


def _hm(ms: int) -> str:
    return tz.utc_ms_to_local(int(ms)).strftime("%H:%M")


def _hms(ms: int) -> str:
    return tz.utc_ms_to_local(int(ms)).strftime("%H:%M:%S")


def _hmm(ms: int) -> str:
    """文件名用 HHMM（Windows 文件名不允许冒号）——契约 2.13 的 HHMM-HHMM.md。"""
    return tz.utc_ms_to_local(int(ms)).strftime("%H%M")


def _local_day_of_ms(ms: int) -> str:
    return tz.local_day(int(ms))


def _cell(v) -> str:
    return str(v if v is not None else "").replace("|", "/").replace("\n", " ").strip()


def _rel(p: Path) -> str:
    """vault 相对路径（正斜杠）——manifest 里跨机稳定的写法。"""
    try:
        return p.resolve().relative_to(paths.VAULT.resolve()).as_posix()
    except ValueError:
        return p.resolve().as_posix()


# ---------------------------------------------------------------- ndjson 全量导出

def _write_ndjson(con) -> dict:
    """全量 event 导出（含 payload）。排序固定 → 内容可重放。"""
    out = paths.exports_dir() / "digital-life-events.ndjson"
    out.parent.mkdir(parents=True, exist_ok=True)

    cols = ", ".join(EVENT_COLUMNS)
    rows = con.execute(
        f"SELECT {cols} FROM event ORDER BY start_utc_ms, device, source, kind, event_id"
    ).fetchall()

    days: set[str] = set()
    buf: list[str] = []
    for r in rows:
        rec = {c: r[c] for c in EVENT_COLUMNS}
        payload = rec.get("payload")
        if isinstance(payload, str):
            try:
                rec["payload"] = json.loads(payload) if payload else {}
            except ValueError:
                rec["payload"] = {"_raw": payload}
        elif payload is None:
            rec["payload"] = {}
        days.add(_local_day_of_ms(int(rec["start_utc_ms"])))
        buf.append(json.dumps(rec, ensure_ascii=False, sort_keys=True))
    data = ("\n".join(buf) + "\n").encode("utf-8") if buf else b""
    out.write_bytes(data)

    return {"path": out, "sha256": sha256_bytes(data), "bytes": len(data),
            "rows": len(buf), "days": days}


# ---------------------------------------------------------------- 切片明细

# 同窗事件缓存：(device, 窗口起点) -> (timeline, basis)。发布期间复用，避免逐片回查。
_TIMELINE_CACHE: dict[tuple[str, int], tuple[list[dict], str]] = {}


def _dev_clause(device: str, column: str = "device") -> str:
    """设备过滤 SQL 片段（含历史别名）。见 devices.sql_device_filter。"""
    from . import devices as _dv
    return _dv.sql_device_filter(device, column)[0]


def _dev_aliases(device: str) -> list[str]:
    from . import devices as _dv
    return _dv.query_aliases(device)


def _slice_rows(con, day_keys: list[str] | None) -> list[dict]:
    sql = ("SELECT s.slice_id, s.device, s.start_utc_ms, s.end_utc_ms, s.image_count, "
           "s.event_count, s.status, s.split_of, s.source_basis, r.model, r.overall_summary, "
           "r.structured_json, r.app_timeline_json, r.conflict_count "
           "FROM slice s LEFT JOIN slice_result r ON r.slice_id = s.slice_id "
           "ORDER BY s.start_utc_ms, s.device, s.slice_id")
    out = []
    for r in con.execute(sql):
        d = dict(r)
        d["day"] = _local_day_of_ms(int(d["start_utc_ms"]))
        if day_keys is not None and d["day"] not in day_keys:
            continue
        out.append(d)
    return out


def _parse_json(s):
    if not s:
        return None
    if isinstance(s, (dict, list)):
        return s
    try:
        return json.loads(s)
    except (TypeError, ValueError):
        return None


def _events_in_window(con, device: str, lo: int, hi: int) -> list[dict]:
    """片内事件直查（slicer 未就绪时的降级路径）。"""
    rows = con.execute(
        "SELECT start_utc_ms, end_utc_ms, kind, app, title, url, path FROM event "
        "WHERE " + _dev_clause(device) + " AND start_utc_ms<? AND COALESCE(end_utc_ms, start_utc_ms)>=? "
        "ORDER BY start_utc_ms, event_id", (device, hi, lo),
    ).fetchall()
    return [{"start": _hms(r[0]), "end": _hms(r[1] or r[0]), "app": r[3],
             "title": r[4], "url": r[5], "kind": r[2]} for r in rows]


def _norm_timeline(items: list) -> list[dict]:
    """统一展示形状：start/end 一律 HH:MM:SS。

    slicer.slice_events 给的是原始 UTC 列（start_utc_ms），dbview/app_timeline_json
    给的是已格式化的 start/end；两种都要能吃（契约未规定展示串）。
    """
    out: list[dict] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        t = dict(it)
        if not t.get("start") and t.get("start_utc_ms") is not None:
            t["start"] = _hms(int(t["start_utc_ms"]))
        if not t.get("end") and t.get("end_utc_ms") is not None:
            t["end"] = _hms(int(t["end_utc_ms"]))
        out.append(t)
    return out


def _slice_timeline(con, sl: dict) -> tuple[list[dict], str]:
    """DB 时间线：优先 slice_result.app_timeline_json，其次 slicer，最后直查 event。"""
    tl = _parse_json(sl.get("app_timeline_json"))
    if isinstance(tl, dict):
        tl = tl.get("timeline") or tl.get("items") or []
    if isinstance(tl, list) and tl:
        return _norm_timeline(tl), "slice_result"

    # ★ 性能：原实现每片回查一次窗口事件，8.6 万片 = 8.6 万次查询（实测发布 20+ 分钟未完）。
    #   同一 15 分钟窗内的父片与所有子片，其事件集合都等于该窗的事件集合，
    #   因此按 (device, 窗口起点) 缓存一次查询结果即可，语义不变。
    _cache_key = (sl["device"], int(sl["start_utc_ms"]) // 900_000)
    cached = _TIMELINE_CACHE.get(_cache_key)
    if cached is None:
        slice_events = None
        try:
            from .slicer import slice_events  # type: ignore[no-redef]
        except ImportError:
            slice_events = None
        got = None
        if slice_events is not None:
            try:
                got = slice_events(con, sl["slice_id"])
            except Exception:  # noqa: BLE001
                got = None
        if isinstance(got, list) and got:
            cached = (_norm_timeline(got), "slicer")
        else:
            cached = (_norm_timeline(_events_in_window(
                con, sl["device"], int(sl["start_utc_ms"]), int(sl["end_utc_ms"]))), "event")
        _TIMELINE_CACHE[_cache_key] = cached
    return cached


def _clip_timeline(timeline: list[dict], lo_ms: int, hi_ms: int) -> list[dict]:
    """把时间线裁剪到分片窗口内再展示。

    实测问题：slice_events 返回的是**事件原始区间**（这对 vision 是对的，需要完整上下文），
    但直接展示会让 00:00-00:15 这一片里出现 23:31:23 -> 00:58:49 的跨界段，
    同一段行为在所有相邻窗口里被重复列出。展示层必须按窗口裁剪。
    """
    from .tz import utc_ms_to_local as _u2l

    def to_ms(hms):
        if not hms or not isinstance(hms, str):
            return None
        parts = hms.split(":")
        if len(parts) != 3:
            return None
        try:
            h, m, s = (int(x) for x in parts)
        except ValueError:
            return None
        base = _u2l(lo_ms).replace(hour=0, minute=0, second=0, microsecond=0)
        cand = int(base.timestamp() * 1000) + ((h * 60 + m) * 60 + s) * 1000
        while cand < lo_ms - 12 * 3600000:
            cand += 86400000
        while cand >= lo_ms + 12 * 3600000:
            cand -= 86400000
        return cand

    out = []
    for it in timeline:
        s = to_ms(it.get("start"))
        e = to_ms(it.get("end"))
        if s is None:
            out.append(it)
            continue
        if e is None or e < s:
            e = s
        cs, ce = max(s, lo_ms), min(e, hi_ms)
        if ce <= cs and not (lo_ms <= s < hi_ms):
            continue
        clipped = dict(it)
        clipped["start"] = _hms(cs)
        clipped["end"] = _hms(max(cs, ce))
        out.append(clipped)
    return out


def _render_slice_md(sl: dict, timeline: list[dict], basis: str) -> str:
    day = sl["day"]
    start, end = _hm(sl["start_utc_ms"]), _hm(sl["end_utc_ms"])
    from . import devices as _dp
    try:
        _k = _dp.kind_of(sl["device"])
        # 用户 2026-10-22：界面上一律显示**设备真名**（D-001 / iQQQZ3 / iQQQ Neo11），
        # 不用 label 的「手机 1 / 手机 2」。与 daily.py、contract.device_label() 保持一致。
        dev_label = _dp.name_of(sl["device"]) if len(_dp.by_kind(_k)) > 1 else ("电脑" if _k == "pc" else "手机")
    except ValueError:
        dev_label = "电脑" if sl["device"] == "pc" else "手机"
    fm = ["---", f"date: {day}", "source: digital-life", "---", "",
          f"# {start}-{end} · {dev_label}", "", _MD_HEADER_NOTE, ""]
    meta = [
        f"- 设备：{dev_label}（{sl['device']}）",
        f"- 时段：{_hms(sl['start_utc_ms'])} - {_hms(sl['end_utc_ms'])}（本地 UTC+8）",
        f"- 截图 {int(sl.get('image_count') or 0)} 张，事件 {int(sl.get('event_count') or 0)} 条，"
        f"状态 {sl.get('status')}，基线 {sl.get('source_basis')}",
        f"- slice_id：{sl['slice_id']}" + (f"（二分子片，父片 {sl['split_of']}）" if sl.get("split_of") else ""),
    ]
    lines = fm + ["## 片元信息", ""] + meta + [""]

    lines += ["## DB 时间线（基线，R3）", "", f"> 来源：{basis}；应用与时间以 DB 为准，模型不得改写。", ""]
    if timeline:
        lines += ["| 开始 | 结束 | 应用 | 标题 |", "|---|---|---|---|"]
        for t in timeline:
            # 结束早于开始 = 跨午夜段；必须标出 +1 天，否则出现 07:57 -> 01:13 的歧义
            _s, _e = t.get("start") or "", t.get("end") or ""
            if _s and _e and _e < _s:
                _e = _e + " (+1天)"
            lines.append(f"| {_cell(_s)} | {_cell(_e)} | "
                         f"{_cell(t.get('app') or t.get('kind'))} | "
                         f"{_cell(t.get('title') or t.get('url'))} |")
    else:
        lines.append("- （片内无事件）")
    lines.append("")

    summary = (sl.get("overall_summary") or "").strip()
    lines += ["## 视觉总结", ""]
    if summary:
        lines += [summary, ""]
    else:
        lines.append("- （本片尚无视觉总结）")
        lines.append("")

    st = _parse_json(sl.get("structured_json"))
    if isinstance(st, dict) and st:
        ka = st.get("key_actions")
        if isinstance(ka, list) and ka:
            lines += ["## 关键操作", ""]
            for it in ka:
                if isinstance(it, str):
                    lines.append(f"- {it.strip()}")
                elif isinstance(it, dict):
                    head = it.get("time") or it.get("start") or ""
                    body = next((it[k] for k in ("detail", "text", "action", "title", "desc", "summary")
                                 if isinstance(it.get(k), str) and it[k].strip()), "")
                    if body:
                        lines.append(f"- {_cell(head)} {body.strip()}".strip())
            lines.append("")
        tags = st.get("image_tags")
        if isinstance(tags, list) and tags:
            lines += ["## 图像标签", "", "、".join(_cell(x) for x in tags if x), ""]

    if sl.get("conflict_count"):
        lines += [f"> 视觉 timeline 与 DB 基线不一致 {int(sl['conflict_count'])} 条"
                  f"（以 DB 为准，仅作可观测指标）。", ""]
    if sl.get("model"):
        lines += [f"> 视觉模型：{sl['model']}", ""]
    return "\n".join(lines).rstrip("\n") + "\n"


# ---------------------------------------------------------------- 日报

def _daily_text(con, day: str) -> str:
    """当日日报正文 —— **现算，不读库**（2026-10-24 用户裁决）。

    用户原话：「日报不需要存储到数据库，包括时段也是，本身就是基于数据库内容
    进行的导出」。

    所以日报正文的唯一来源是 render_narrative/render_daily 的即时输出；
    daily_summary 只留模型叙事原文（唯一不可重算的东西），不存渲染结果。
    这也顺带消除一个长期隐患：库里的 output_text 是历史快照，改了渲染代码
    却忘了重跑时，publish 会把旧正文搬进 vault（AGENTS.md §G.10）。
    """
    from .daily import build_daily

    res = build_daily(con, day, persist=False)
    return str((res or {}).get("output_text") or "").strip()


# ---------------------------------------------------------------- 主入口

def _resolve_days(con, day_keys, event_days: set[str]) -> list[str]:
    if day_keys:
        from .daily import normalize_date

        return sorted({normalize_date(d) for d in day_keys})
    days = set(event_days)
    for r in con.execute("SELECT start_utc_ms FROM slice"):
        days.add(_local_day_of_ms(int(r[0])))
    for r in con.execute("SELECT date_key FROM daily_summary"):
        days.add(str(r[0]))
    return sorted(days)


def publish_all(con, day_keys: list[str] | None = None, make_zip: bool = True) -> dict:
    """把当日生成物写进 vault，并产出 ndjson / manifest /（可选）zip 留存包。"""
    paths.ensure_dirs()
    exports = paths.exports_dir()
    exports.mkdir(parents=True, exist_ok=True)

    nd = _write_ndjson(con)
    days = _resolve_days(con, day_keys, nd["days"])
    sel = set(days) if day_keys else None

    files: list[dict] = []
    slice_written = 0
    daily_written = 0

    # 1) 10-时段/YYYY-MM-DD/HHMM-HHMM.md —— 每片一篇，同名整体覆盖
    rows = _slice_rows(con, sel)
    stems: dict[tuple[str, str], int] = {}
    for sl in rows:
        key = (sl["day"], f"{_hmm(sl['start_utc_ms'])}-{_hmm(sl['end_utc_ms'])}")
        stems[key] = stems.get(key, 0) + 1
    # 假设（G8，2026-10-21，用户未在时限内答复落盘范围）：
    #   默认只把「有视觉结果」的时段写成 md —— 否则未跑视觉时会产生 8.6 万篇
    #   「本片尚无视觉总结」的空壳，落进 Obsidian 等于污染 vault（S57 同类问题）。
    #   全量时段明细仍在 SQLite，需要时按日期查；如需全量落盘，显式设 DL_PUBLISH_ALL_SLICES=1。
    _all_slices = os.environ.get("DL_PUBLISH_ALL_SLICES") == "1"
    made_dirs: set = set()
    for sl in rows:
        if not _all_slices and not (sl.get("overall_summary") or "").strip():
            continue
        day = sl["day"]
        stem = f"{_hmm(sl['start_utc_ms'])}-{_hmm(sl['end_utc_ms'])}"
        if stems.get((day, stem), 0) > 1:  # pc 与 phone 落在同一时段 → 加设备后缀，避免互相覆盖
            stem = f"{stem}-{sl['device']}"
        # 片层 md 落 10-时段/片层/<日期>/ —— HTML 已改成一天一页（10-时段/<日期>.html），
        # 日期目录不再是网页的家，只留给 md，两者互不干扰。
        target = paths.LAYERED_DIR / day / f"{stem}.md"
        timeline, basis = _slice_timeline(con, sl)
        timeline = _clip_timeline(timeline, int(sl["start_utc_ms"]), int(sl["end_utc_ms"]))
        data = _render_slice_md(sl, timeline, basis).encode("utf-8")
        if target.parent not in made_dirs:   # mkdir 每片一次代价不小（8.6 万次系统调用）
            target.parent.mkdir(parents=True, exist_ok=True)
            made_dirs.add(target.parent)
        target.write_bytes(data)
        files.append({"path": _rel(target), "sha256": sha256_bytes(data), "bytes": len(data),
                      "kind": "slice", "date": day})
        slice_written += 1

    # 2) 20-日报/YYYY-MM-DD.md —— 机器总结，frontmatter 仅 date 与 source
    for day in days:
        text = _daily_text(con, day)
        if not text:
            continue
        data = (text + "\n").encode("utf-8")
        target = paths.DAILY_DIR / f"{day}.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        files.append({"path": _rel(target), "sha256": sha256_bytes(data), "bytes": len(data),
                      "kind": "daily", "date": day})
        daily_written += 1

    # 3) manifest.json
    counts = {
        "event": con.execute("SELECT COUNT(*) FROM event").fetchone()[0],
        "slice": con.execute("SELECT COUNT(*) FROM slice").fetchone()[0],
        "slice_result": con.execute("SELECT COUNT(*) FROM slice_result").fetchone()[0],
        # 日报数 = 实际落盘的文件数，不是 daily_summary 行数。
        # 日报已改为「不存渲染结果」，库里行数不再代表产出规模。
        "daily": daily_written,
        "ndjson_rows": nd["rows"],
    }
    sources = []
    for r in con.execute(
        "SELECT source_id, source, device, sha256, bytes, row_count, "
        "window_start_utc_ms, window_end_utc_ms FROM source_file "
        "ORDER BY source, device, source_id"
    ):
        sources.append({
            "source_id": r["source_id"], "source": r["source"], "device": r["device"],
            "sha256": r["sha256"], "bytes": int(r["bytes"] or 0),
            "row_count": int(r["row_count"] or 0),
            "window_start_utc_ms": r["window_start_utc_ms"],
            "window_end_utc_ms": r["window_end_utc_ms"],
        })

    manifest = {
        "generated_at": _now_iso(),
        "generator": "digital-life",
        "layer": "day",
        "days": days,
        "counts": counts,
        "sources": sources,
        "exports": [{"path": nd["path"].name, "sha256": nd["sha256"],
                     "bytes": nd["bytes"], "rows": nd["rows"]}],
        "files": sorted(files, key=lambda f: f["path"]),
        "summary": {"slice_files": slice_written, "daily_files": daily_written,
                    "files": len(files)},
    }
    mpath = paths.manifest_path()
    mpath.parent.mkdir(parents=True, exist_ok=True)
    mtext = json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=False) + "\n"
    mdata = mtext.encode("utf-8")
    mpath.write_bytes(mdata)

    # 4) zip：只含 ndjson + manifest（不打原图、不打 sqlite）
    zpath = None
    if make_zip:
        stamp = tz.utc_ms_to_local(int(time.time() * 1000)).strftime("%Y%m%d")
        zp = exports / f"digital-life-{stamp}.zip"
        with zipfile.ZipFile(zp, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.write(nd["path"], arcname="digital-life-events.ndjson")
            zf.write(mpath, arcname="manifest.json")
        zpath = zp

    return {
        "generated_at": manifest["generated_at"],
        "days": days,
        "counts": counts,
        "manifest": str(mpath),
        "manifest_sha256": sha256_bytes(mdata),
        "ndjson": str(nd["path"]),
        "ndjson_sha256": nd["sha256"],
        "ndjson_bytes": nd["bytes"],
        "ndjson_rows": nd["rows"],
        "zip": str(zpath) if zpath else None,
        "zip_sha256": sha256_file(zpath) if zpath else None,
        "files": manifest["files"],
        "summary": manifest["summary"],
        "sources": len(sources),
    }
