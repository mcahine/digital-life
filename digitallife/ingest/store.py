"""SQLite 读写层 —— 连接、幂等写入、查询。

S12：连接统一走 connect()（check_same_thread=False），FastAPI 同步端点也安全。
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .model import EVENT_COLUMNS, Event
from ..paths import db_path, ensure_dirs
from .schema import init_db


def connect(path: str | Path | None = None) -> sqlite3.Connection:
    p = Path(path) if path else db_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(p), check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    return con


def open_db(path: str | Path | None = None) -> sqlite3.Connection:
    """连接 + 建表（首次调用即完成 schema 初始化）。"""
    ensure_dirs()
    con = connect(path)
    init_db(con)
    return con


_INSERT = (
    "INSERT INTO event(" + ", ".join(EVENT_COLUMNS) + ", ingest_run) "
    "VALUES(" + ", ".join(["?"] * (len(EVENT_COLUMNS) + 1)) + ") "
    "ON CONFLICT(event_id) DO UPDATE SET "
    "end_utc_ms=excluded.end_utc_ms, duration_s=excluded.duration_s, "
    "app=excluded.app, app_id=excluded.app_id, title=excluded.title, "
    "url=excluded.url, path=excluded.path, category=excluded.category, "
    "payload=excluded.payload, ingest_run=excluded.ingest_run"
)


def upsert_events(con: sqlite3.Connection, events: list[Event], ingest_run: str) -> int:
    """批量写入；event_id 相同即覆盖（幂等）。返回写入条数。"""
    if not events:
        return 0
    rows = [e.as_row() + (ingest_run,) for e in events]
    con.executemany(_INSERT, rows)
    return len(rows)


def record_source_file(con: sqlite3.Connection, **kw) -> None:
    cols = ("source_id", "source", "device", "path", "sha256", "bytes", "mtime",
            "window_start_utc_ms", "window_end_utc_ms", "row_count", "note")
    con.execute(
        "INSERT INTO source_file(" + ", ".join(cols) + ") VALUES(" + ", ".join(["?"] * len(cols)) + ") "
        "ON CONFLICT(source_id) DO UPDATE SET "
        "sha256=excluded.sha256, bytes=excluded.bytes, mtime=excluded.mtime, "
        "window_start_utc_ms=excluded.window_start_utc_ms, "
        "window_end_utc_ms=excluded.window_end_utc_ms, "
        "row_count=excluded.row_count, note=excluded.note",
        tuple(kw.get(c) for c in cols),
    )


def count_events(con: sqlite3.Connection, source: str | None = None) -> int:
    if source:
        return con.execute("SELECT COUNT(*) FROM event WHERE source=?", (source,)).fetchone()[0]
    return con.execute("SELECT COUNT(*) FROM event").fetchone()[0]


def count_kind(con: sqlite3.Connection, source: str) -> dict[str, int]:
    rows = con.execute(
        "SELECT kind, COUNT(*) FROM event WHERE source=? GROUP BY kind", (source,)
    ).fetchall()
    return {r[0]: r[1] for r in rows}


def time_span(con: sqlite3.Connection, source: str | None = None) -> tuple[int | None, int | None]:
    sql = "SELECT MIN(start_utc_ms), MAX(COALESCE(end_utc_ms, start_utc_ms)) FROM event"
    args: tuple = ()
    if source:
        sql += " WHERE source=?"
        args = (source,)
    lo, hi = con.execute(sql, args).fetchone()
    return lo, hi


def events_between(con: sqlite3.Connection, lo_ms: int, hi_ms: int,
                   device: str | None = None, kinds: tuple[str, ...] | None = None) -> list[sqlite3.Row]:
    sql = ("SELECT * FROM event WHERE start_utc_ms < ? AND COALESCE(end_utc_ms, start_utc_ms) >= ?")
    args: list = [hi_ms, lo_ms]
    if device:
        # 库内同一台设备可能以旧值（pc/phone）或注册 id（pc-main/phone-1）存在，
        # 查询两者都认，否则改造后历史数据整体查不到（实测日报变「当日无数据」）。
        from ..domain import devices as _dv
        aliases = _dv.query_aliases(device)
        sql += " AND device IN (" + ",".join(["?"] * len(aliases)) + ")"
        args.extend(aliases)
    if kinds:
        sql += " AND kind IN (" + ",".join(["?"] * len(kinds)) + ")"
        args.extend(kinds)
    sql += " ORDER BY start_utc_ms"
    return con.execute(sql, args).fetchall()


def upsert_capture_text(con: sqlite3.Connection, event_id: str, text: str,
                        engine: str, model: str | None, now_ms: int) -> None:
    con.execute(
        "INSERT INTO capture_text(event_id, text, engine, model, created_at) VALUES(?,?,?,?,?) "
        "ON CONFLICT(event_id) DO UPDATE SET text=excluded.text, engine=excluded.engine, "
        "model=excluded.model, created_at=excluded.created_at",
        (event_id, text, engine, model, now_ms),
    )


def search_events(con: sqlite3.Connection, q: str, limit: int = 20) -> list[dict]:
    """event_fts 检索（title/url/app）。空查询返回空，不做全表扫描。"""
    q = (q or "").strip()
    if not q:
        return []
    rows = con.execute(
        "SELECT e.event_id, e.device, e.source, e.kind, e.start_utc_ms, e.end_utc_ms, "
        "e.app, e.title, e.url FROM event_fts f JOIN event e ON e.rowid = f.rowid "
        "WHERE event_fts MATCH ? ORDER BY rank LIMIT ?",
        (q, limit),
    ).fetchall()
    return [dict(r) for r in rows]


def rebuild_fts(con: sqlite3.Connection) -> None:
    con.execute("INSERT INTO event_fts(event_fts) VALUES('rebuild')")
    con.execute("INSERT INTO capture_fts(capture_fts) VALUES('rebuild')")
    con.execute("INSERT INTO slice_fts(slice_fts) VALUES('rebuild')")
