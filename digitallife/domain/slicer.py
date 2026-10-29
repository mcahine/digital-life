"""时间切片 —— 全局 UTC 时间轴上的 15 分钟窗。

时间轴是唯一主轴：窗边界对齐到本地时区的 :00/:15/:30/:45（UTC+8 无夏令时，
所以这也同时对齐 UTC 的 :00/:15/:30/:45），device 只是 WHERE 维度，不是分区。
因此 pc 与 phone 同一天切出的窗边界必然可比（逐毫秒相同）。

三条硬约束：
1. 只对有事件的窗建片 —— 空窗不建（否则 2558 个窗里会塞进上千空片）。
2. 片内截图数 > MAX_IMAGES 时递归二分；子片 split_of = 父片 slice_id，
   父片自身也落库（status='split'）。
3. slice_id = sha1(f"{device}|{start_utc_ms}|{end_utc_ms}")[:16]，只由三元组决定，
   可稳定重算；同一窗口重跑不会产生新片。

已知边界：二分只在能把每片截图数真正降下来时进行。若某窗内的截图时间戳完全相同
（例如上游把一批图都记成同一毫秒），二分不会减少任何一片的图数，此时保留父片为叶子，
其 image_count 会超过 MAX_IMAGES —— 宁可显式承载，也不制造同内容的子孙片。
上游按时间戳唯一性产事件时不会走到这里。

幂等：先算后写。若本次算出的片与库里已有行完全一致（片集合/边界/截图数/事件数/split_of），
则不写库、不 bump created_at，也不复活下游已改写的 status。
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from datetime import timedelta

from ..ingest import tz
from .. import paths

WINDOW_MIN = 15
MAX_IMAGES = 24
WINDOW_MS = WINDOW_MIN * 60 * 1000

STATUS_PENDING = "pending"
STATUS_SPLIT = "split"
SOURCE_BASIS = "db"


def slice_id_for(device: str, start_utc_ms: int, end_utc_ms: int) -> str:
    raw = f"{device}|{int(start_utc_ms)}|{int(end_utc_ms)}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _now_ms() -> int:
    return int(time.time() * 1000)


def _day_window(date_key: str) -> tuple[int, int]:
    start = tz.parse_local(f"{date_key.strip()} 00:00:00")
    return tz.local_to_utc_ms(start), tz.local_to_utc_ms(start + timedelta(days=1))


def _window_bounds(lo: int, hi: int) -> tuple[int, int]:
    """含事件的日窗口 → 首尾对齐的窗边界（均为 15 分钟整数倍）。"""
    a = (lo // WINDOW_MS) * WINDOW_MS
    b = -((-hi) // WINDOW_MS) * WINDOW_MS
    return int(a), int(b)


def _windows_of_day(day_rows: list[sqlite3.Row], dlo: int, dhi: int) -> list[tuple[int, int]]:
    """当天事件的 15 分钟窗（对齐网格，不越过当天边界）。"""
    if not day_rows:
        return []
    lo = min(int(r["start_utc_ms"]) for r in day_rows)
    hi = max(int(r["end_utc_ms"]) if r["end_utc_ms"] is not None else int(r["start_utc_ms"])
             for r in day_rows)
    a, b = _window_bounds(lo, hi)
    b = min(b, dhi)
    return [(w, w + WINDOW_MS) for w in range(a, b, WINDOW_MS)]


def _dev_clause(device: str, column: str = "device") -> str:
    """设备过滤 SQL 片段（含历史别名）。见 devices.sql_device_filter。"""
    from . import devices as _dv
    return _dv.sql_device_filter(device, column)[0]


def _dev_aliases(device: str) -> list[str]:
    from . import devices as _dv
    return _dv.query_aliases(device)


def _active_days(con: sqlite3.Connection, device: str) -> list[str]:
    """只返回**有实质事件**的本地日期（YYYY-MM-DD），按升序。

    为什么按天而不是按全量跨度枚举窗口：全量跨度 753 天 × 96 窗 = 72,294 个窗，
    每窗都要一次区间查询（实测单窗 1.2s，加索引后 0.2s）→ 20 小时/半小时，均不可接受。
    而真正有数据的天只占一部分，按天切窗后每窗查询的扫描量也落在当天范围内。
    """
    rows = con.execute(
        "SELECT DISTINCT date(start_utc_ms / 1000, 'unixepoch', '+8 hours') AS d "
        "FROM event WHERE " + _dev_clause(device) + " AND kind != 'power' ORDER BY d",
        tuple(_dev_aliases(device)),
    ).fetchall()
    return [r[0] for r in rows if r[0]]


def _fetch_windows(con: sqlite3.Connection, device: str, day_key: str | None) -> list[tuple[int, int]]:
    """返回待处理的 15 分钟窗。day_key 缺省 = 该设备所有「有实质事件」的日。"""
    days = [day_key] if day_key else _active_days(con, device)
    out: list[tuple[int, int]] = []
    for d in days:
        lo, hi = _day_window(d)
        row = con.execute(
            "SELECT MIN(start_utc_ms), MAX(COALESCE(end_utc_ms, start_utc_ms)) "
            "FROM event WHERE " + _dev_clause(device) + " AND start_utc_ms >= ? AND start_utc_ms < ?",
            (*_dev_aliases(device), lo, hi),
        ).fetchone()
        if not row or row[0] is None:
            continue
        a, b = _window_bounds(int(row[0]), int(row[1]))
        # 单日窗口不越过当天边界（避免把次日的事件算进今天）
        b = min(b, hi)
        out.extend((w, w + WINDOW_MS) for w in range(a, b, WINDOW_MS))
    return out


# 半开区间归属：事件落在 [lo,hi) 内。结尾正好压在 lo 上的事件归前一窗（避免跨窗重复计数），
# 但瞬时时点正好在 lo 上的事件（end==start）必须留下。
_OVERLAP = "start_utc_ms < ? AND (COALESCE(end_utc_ms, start_utc_ms) > ? OR start_utc_ms = ?)"


def _window_rows(con: sqlite3.Connection, device: str, lo: int, hi: int) -> list[sqlite3.Row]:
    return con.execute(
        "SELECT * FROM event WHERE " + _dev_clause(device) + " AND " + _OVERLAP + " ORDER BY start_utc_ms",
        (device, hi, lo, lo),
    ).fetchall()


def _span(row: sqlite3.Row) -> tuple[int, int]:
    s = int(row["start_utc_ms"])
    e = int(row["end_utc_ms"]) if row["end_utc_ms"] is not None else s
    return s, max(s, e)


def _has_content(rows: list[sqlite3.Row]) -> bool:
    """power 只是电源标记（Active/Away/Session lock），单独占一个窗等于空片 —— 不建片。
    有 app/web/screen 等实质事件才算这个窗「有事件」。"""
    return any(r["kind"] != "power" for r in rows)


def _hits(span: tuple[int, int], lo: int, hi: int) -> bool:
    """事件是否落在半开区间 [lo,hi) 内 —— 与 _OVERLAP 的 SQL 谓词同口径，不可各写一套。"""
    s, e = span
    return s < hi and (e > lo or s == lo)


def _partition(rows: list[sqlite3.Row], lo: int, mid: int, hi: int) -> tuple[list, list]:
    """按同一半开规则二分：落在 mid 上的瞬时事件归右片，跨 mid 的段两片都留（context 用）。"""
    left = [r for r in rows if _hits(_span(r), lo, mid)]
    right = [r for r in rows if _hits(_span(r), mid, hi)]
    return left, right


def _img_count(rows: list[sqlite3.Row]) -> int:
    return sum(1 for r in rows if r["kind"] == "screen")


def _make_row(device: str, rows: list[sqlite3.Row], lo: int, hi: int,
              split_of: str | None) -> dict:
    # 片边界始终取窗口边界（顶层 = 15 分钟网格，子片 = 二分网格），
    # 不取事件实测起止 —— 否则片会脱离 :00/:15/:30/:45 主轴，pc/phone 无法并列。
    s, e = int(lo), int(hi)
    return {
        "slice_id": slice_id_for(device, s, e),
        "device": device,
        "start_utc_ms": s,
        "end_utc_ms": e,
        "image_count": _img_count(rows),
        "event_count": len(rows),
        "split_of": split_of,
        "status": STATUS_PENDING,
    }


def _plan_rows(device: str, rows: list[sqlite3.Row], lo: int, hi: int,
               split_of: str | None, out: list[dict]) -> None:
    """递归落片：截图超限则父片 status='split' 并二分，否则 status='pending'。

    二分只在「真能减少每片截图数」时进行。全部截图挤在同一毫秒时（零长度事件），
    二分只会把同一批图反复归到一侧 —— 那是伪装成递归的死循环。此时保留父片为叶子
    （image_count 允许 > MAX_IMAGES，见模块注释），不制造同内容的子孙片。
    """
    if not _has_content(rows):
        return
    own = _make_row(device, rows, lo, hi, split_of)
    imgs = own["image_count"]
    if imgs > MAX_IMAGES:
        s, e = own["start_utc_ms"], own["end_utc_ms"]
        mid = (s + e) // 2
        left, right = _partition(rows, s, mid, e)
        sides = [(left, s, mid, _has_content(left)), (right, mid, e, _has_content(right))]
        live = [x for x in sides if x[3]]
        if live and max(_img_count(x[0]) for x in live) < imgs:
            own["status"] = STATUS_SPLIT
            out.append(own)
            for part, plo, phi, ok in sides:
                if ok:
                    _plan_rows(device, part, plo, phi, own["slice_id"], out)
            return
    out.append(own)


def _rows_of_day(con: sqlite3.Connection, device: str, lo: int, hi: int) -> list[sqlite3.Row]:
    """一次取回当天全部事件（含跨界段），供内存中按窗归并。

    为什么按天整取：逐窗查询是 O(天数 × 96) 次区间扫描（实测 phone 428 天 = 4.1 万次），
    单次 0.2–1.2s，合计数小时。按天整取后每天只查一次，实测单天 1.4s / 1,500 行，
    428 天约 10 分钟；再在内存里按 [lo,hi) 判定归属，语义与逐窗 SQL 完全一致（_hits 同口径）。
    """
    # 区间相交判定必须是 start < hi AND (end > lo 或 瞬时点落在 [lo,hi) 内)。
    # ★ 不能把 "end_utc_ms IS NULL" 单独作为一个 OR 分支：截图事件全部 end=NULL，
    #   那样每一页都会把**所有**截图捞进来（实测单天 218,356 行 ≈ phone 全部 30.6 万）。
    return con.execute(
        "SELECT * FROM event WHERE device = ? AND start_utc_ms < ? "
        "AND (end_utc_ms > ? OR (end_utc_ms IS NULL AND start_utc_ms >= ?) OR start_utc_ms = ?) "
        "ORDER BY start_utc_ms",
        (device, hi, lo, lo, lo),
    ).fetchall()


def build_slices(con: sqlite3.Connection, device: str, day_key: str | None = None) -> list[dict]:
    """按 15 分钟窗切片并落库；返回本次窗口集合的片（父片 + 子片）。"""
    planned: list[dict] = []
    days = [day_key] if day_key else _active_days(con, device)
    for d in days:
        dlo, dhi = _day_window(d)
        day_rows = _rows_of_day(con, device, dlo, dhi)
        if not day_rows:
            continue
        spans = [(_span(r), r) for r in day_rows]
        for lo, hi in _windows_of_day(day_rows, dlo, dhi):
            rows = [r for sp, r in spans if _hits(sp, lo, hi)]
            if not rows:
                continue
            _plan_rows(device, rows, lo, hi, None, planned)

    ids = [p["slice_id"] for p in planned]
    # SQLite 的变量上限是 999（SQLITE_MAX_VARIABLE_NUMBER），片数上万时一次性 IN 会
    # 抛 "too many SQL variables"（实测 phone 一段就崩）。分批查，每批不超上限。
    existing = {}
    CHUNK = 500
    for i in range(0, len(ids), CHUNK):
        part = ids[i:i + CHUNK]
        marks = ",".join(["?"] * len(part))
        for r in con.execute(f"SELECT * FROM slice WHERE slice_id IN ({marks})", part):
            existing[r["slice_id"]] = r

    regenerated = False
    for p in planned:
        old = existing.get(p["slice_id"])
        if old is not None and (
            old["start_utc_ms"] == p["start_utc_ms"]
            and old["end_utc_ms"] == p["end_utc_ms"]
            and old["image_count"] == p["image_count"]
            and old["event_count"] == p["event_count"]
            and (old["split_of"] or None) == p["split_of"]
        ):
            continue
        regenerated = True
        break

    if regenerated:
        now = _now_ms()
        for p in planned:
            con.execute(
                "INSERT INTO slice(slice_id, device, start_utc_ms, end_utc_ms, image_count, "
                "event_count, status, split_of, source_basis, created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(slice_id) DO UPDATE SET "
                "device=excluded.device, start_utc_ms=excluded.start_utc_ms, "
                "end_utc_ms=excluded.end_utc_ms, image_count=excluded.image_count, "
                "event_count=excluded.event_count, split_of=excluded.split_of, "
                "source_basis=excluded.source_basis, "
                "status=CASE WHEN slice.status='pending' THEN excluded.status ELSE slice.status END",
                (p["slice_id"], p["device"], p["start_utc_ms"], p["end_utc_ms"],
                 p["image_count"], p["event_count"], p["status"], p["split_of"],
                 SOURCE_BASIS, now),
            )
        _drop_stale_children(con, planned, set(ids))
        con.commit()

    return planned


def _drop_stale_children(con: sqlite3.Connection, planned: list[dict], keep: set[str]) -> None:
    """清掉本次重算窗口里不再存在的子片（仅限无 slice_result 的行，不误伤下游产出）。"""
    parents = {p["slice_id"] for p in planned if p["status"] == STATUS_SPLIT}
    if not parents or not keep:
        return
    # 同上：父片数量可能上万，IN 的占位符必须分批（SQLITE_MAX_VARIABLE_NUMBER=999）
    stale: list[str] = []
    parent_ids = sorted(parents)
    CHUNK = 500
    for i in range(0, len(parent_ids), CHUNK):
        part = parent_ids[i:i + CHUNK]
        marks = ",".join(["?"] * len(part))
        stale.extend(
            r[0] for r in con.execute(
                f"SELECT slice_id FROM slice WHERE split_of IN ({marks})", part
            ) if r[0] not in keep
        )
    for sid in stale:
        has_result = con.execute(
            "SELECT 1 FROM slice_result WHERE slice_id = ?", (sid,)
        ).fetchone()
        if not has_result:
            con.execute("DELETE FROM slice WHERE slice_id = ?", (sid,))


def slice_events(con: sqlite3.Connection, slice_id: str) -> list[dict]:
    """片内事件（供 vision 与 publish 消费）。时间仍为 UTC ms，展示时再转本地。"""
    row = con.execute(
        "SELECT device, start_utc_ms, end_utc_ms FROM slice WHERE slice_id = ?", (slice_id,)
    ).fetchone()
    if row is None:
        raise ValueError(f"未知 slice_id: {slice_id!r} —— 先用 build_slices() 建片")
    out: list[dict] = []
    for r in con.execute(
        "SELECT * FROM event WHERE device = ? AND " + _OVERLAP + " ORDER BY start_utc_ms",
        (row["device"], row["end_utc_ms"], row["start_utc_ms"], row["start_utc_ms"]),
    ).fetchall():
        try:
            payload = json.loads(r["payload"] or "{}")
        except json.JSONDecodeError:
            payload = {}
        out.append({
            "event_id": r["event_id"], "device": r["device"], "source": r["source"],
            "kind": r["kind"], "start_utc_ms": r["start_utc_ms"],
            "end_utc_ms": r["end_utc_ms"], "duration_s": r["duration_s"],
            "app": r["app"], "app_id": r["app_id"], "title": r["title"],
            "url": r["url"], "path": r["path"], "category": r["category"],
            "payload": payload,
        })
    return out
