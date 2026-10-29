"""DDL 与迁移。

S30：init_db 必须先 _migrate 补列、再 executescript(DDL) —— DDL 里的 CREATE INDEX
会引用新列，顺序颠倒会报 "no such column"。
"""
from __future__ import annotations

import sqlite3

SCHEMA_VERSION = 2

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

-- 链接标题（v2）。**表体只用下面这一个常量**，DDL 与 MIGRATIONS 都引用它，
-- 不许各写一遍字面（写两遍 = 靠人记一致性，迟早漂移）。
"""

# 链接标题表（2026-10-25 用户要求入库，替代 tmp/.titlecache.json 磁盘缓存）。
# 为什么要入库：磁盘缓存不进 git、不进备份，换机/清 tmp 就全丢。
# status='failed' 也要落行 —— 否则每次建页都会重抓同一个抓不到的地址。
# ⚠️ 这里是**唯一**的表体字面：DDL 与 MIGRATIONS 都引用它。
_LINK_TITLE_DDL = (
    "CREATE TABLE IF NOT EXISTS link_title ("
    "  url        TEXT PRIMARY KEY,"
    "  title      TEXT NOT NULL DEFAULT '',"
    "  source     TEXT NOT NULL DEFAULT 'http',"   # http | window | raw | manual
    "  status     TEXT NOT NULL DEFAULT 'ok',"     # ok | failed
    "  fetched_at INTEGER NOT NULL DEFAULT 0)"
)

DDL = (
    DDL
    + _LINK_TITLE_DDL + ";\n"
    + "CREATE INDEX IF NOT EXISTS ix_link_title_status ON link_title(status);\n"
)

# 迁移：v1 → 更高版本时在此逐条追加。
#
# v2（2026-10-25）：新增 link_title 表。
# **为什么不能只靠 DDL 里的 CREATE TABLE IF NOT EXISTS**（S4）：
#   DDL 只在新库里"顺便"建出来；老库（912 MB，不可能重建）里如果 DDL 执行失败
#   或被人为跳过，表就不存在，而读路径会**静默**走降级——表现是"标题又变回域名了"，
#   没有任何报错。显式迁移把"建表"变成一个**同步执行的、可断言的**动作。
# 顺序由 init_db 保证：先 _migrate（补表）→ 再 executescript(DDL)（建索引，S30）。
MIGRATIONS: dict[int, list[str]] = {
    2: [_LINK_TITLE_DDL],
}


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
    """先补列/补表，再交给 DDL 建索引（S30）。

    开头先保证 meta 存在：**全新库第一次 init_db 时 meta 还没建**，
    此刻 current_version() 只能靠 OperationalError 兜底返回 0；一旦将来某条迁移
    语句需要读 meta（例如按版本号决定补哪一列），就会在建库这一步直接崩。
    这里显式建一次，把「元表必然存在」变成 _migrate 自己的前置条件，
    而不是依赖下游函数的异常处理。注意：建表本身在 DDL 里也有，
    两处都是 IF NOT EXISTS，重复无害；这里只为**时序**。
    """
    con.execute("CREATE TABLE IF NOT EXISTS meta ("
                "  key TEXT PRIMARY KEY, value TEXT NOT NULL)")
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
