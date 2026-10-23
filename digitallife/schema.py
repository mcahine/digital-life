"""DDL 与迁移。

S30：init_db 必须先 _migrate 补列、再 executescript(DDL) —— DDL 里的 CREATE INDEX
会引用新列，顺序颠倒会报 "no such column"。
"""
from __future__ import annotations

import sqlite3

SCHEMA_VERSION = 1

DDL = """
CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS source_file (
  source_id          TEXT PRIMARY KEY,
  source             TEXT NOT NULL,
  device             TEXT NOT NULL,
  path               TEXT NOT NULL,
  sha256             TEXT NOT NULL,
  bytes              INTEGER NOT NULL,
  mtime              REAL,
  window_start_utc_ms INTEGER,
  window_end_utc_ms   INTEGER,
  row_count          INTEGER NOT NULL DEFAULT 0,
  note               TEXT
);

CREATE TABLE IF NOT EXISTS event (
  event_id     TEXT PRIMARY KEY,
  device       TEXT NOT NULL,
  source       TEXT NOT NULL,
  kind         TEXT NOT NULL,
  start_utc_ms INTEGER NOT NULL,
  end_utc_ms   INTEGER,
  duration_s   INTEGER,
  app          TEXT,
  app_id       TEXT,
  title        TEXT,
  url          TEXT,
  path         TEXT,
  category     TEXT,
  payload      TEXT NOT NULL DEFAULT '{}',
  ingest_run   TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_event_time      ON event(start_utc_ms);
CREATE INDEX IF NOT EXISTS ix_event_dev_time  ON event(device, start_utc_ms);
CREATE INDEX IF NOT EXISTS ix_event_source    ON event(source);
CREATE INDEX IF NOT EXISTS ix_event_kind      ON event(kind);

CREATE VIRTUAL TABLE IF NOT EXISTS event_fts USING fts5(
  title, url, app, content='event', content_rowid='rowid'
);

CREATE TABLE IF NOT EXISTS capture_text (
  event_id   TEXT PRIMARY KEY,
  text       TEXT NOT NULL,
  engine     TEXT NOT NULL,
  model      TEXT,
  created_at INTEGER NOT NULL
);

CREATE VIRTUAL TABLE IF NOT EXISTS capture_fts USING fts5(
  text, content='capture_text', content_rowid='rowid'
);

CREATE TABLE IF NOT EXISTS slice (
  slice_id     TEXT PRIMARY KEY,
  device       TEXT NOT NULL,
  start_utc_ms INTEGER NOT NULL,
  end_utc_ms   INTEGER NOT NULL,
  image_count  INTEGER NOT NULL DEFAULT 0,
  event_count  INTEGER NOT NULL DEFAULT 0,
  status       TEXT NOT NULL DEFAULT 'pending',
  split_of     TEXT,
  source_basis TEXT NOT NULL DEFAULT 'db',
  created_at   INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_slice_time ON slice(start_utc_ms);
CREATE INDEX IF NOT EXISTS ix_slice_dev  ON slice(device, start_utc_ms);
CREATE INDEX IF NOT EXISTS ix_slice_of   ON slice(split_of);

CREATE TABLE IF NOT EXISTS slice_result (
  slice_id        TEXT PRIMARY KEY,
  model           TEXT,
  overall_summary TEXT,
  structured_json TEXT,
  app_timeline_json TEXT,
  conflict_count  INTEGER NOT NULL DEFAULT 0,
  raw_response    TEXT,
  created_at      INTEGER NOT NULL
);

CREATE VIRTUAL TABLE IF NOT EXISTS slice_fts USING fts5(
  overall_summary, content='slice_result', content_rowid='rowid'
);

CREATE TABLE IF NOT EXISTS daily_summary (
  date_key              TEXT PRIMARY KEY,
  model                 TEXT,
  output_text           TEXT,
  structured_json       TEXT,
  device_breakdown_json TEXT,
  created_at            INTEGER NOT NULL
);
"""

# 迁移：v1 → 更高版本时在此逐条追加。当前为初始版本，只记录版本号。
MIGRATIONS: dict[int, list[str]] = {}


def current_version(con: sqlite3.Connection) -> int:
    try:
        row = con.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
    except sqlite3.OperationalError:
        return 0
    if not row:
        return 0
    try:
        return int(row[0])
    except (TypeError, ValueError):
        return 0


def _migrate(con: sqlite3.Connection) -> None:
    """先补列/补表（此处为空，保留扩展位），再交给 DDL 建索引。"""
    ver = current_version(con)
    for target in sorted(MIGRATIONS):
        if target <= ver:
            continue
        for stmt in MIGRATIONS[target]:
            con.execute(stmt)


def init_db(con: sqlite3.Connection) -> None:
    """S30 顺序：先 _migrate，再 DDL，最后写版本号。"""
    _migrate(con)
    con.executescript(DDL)
    con.execute(
        "INSERT INTO meta(key, value) VALUES('schema_version', ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (str(SCHEMA_VERSION),),
    )
    con.commit()
