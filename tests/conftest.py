"""pytest 公共 fixture。

铁律（testing.md §3.2 / S16）：**任何碰数据库的用例必须用临时库**，
绝不允许 DL_DB 指向 .data 里那个 912 MB 真库。

T1（2026-10-25）：本文件**不再需要 sys.path.insert**。
  `pyproject.toml` 已可 `pip install -e .`，`digitallife` 与 `slice_pages` 都是正式包；
  在仓库根跑 `pytest` 时 rootdir 自动进 sys.path，pytest 也能找到 tests/。
  实测证据：`python tmp/t1_count_syspath.py` → 真实语句 0 处。
"""
from __future__ import annotations

import sqlite3

import pytest


@pytest.fixture
def tmpdb(tmp_path, monkeypatch):
    """每个用例一个全新空库。绝不用真库（S16）。"""
    db = tmp_path / "test.sqlite"
    monkeypatch.setenv("DL_DB", str(db))
    monkeypatch.setenv("DL_WORK_DIR", str(tmp_path / "work"))

    from digitallife.ingest.schema import init_db
    con = sqlite3.connect(str(db), check_same_thread=False)
    con.row_factory = sqlite3.Row
    init_db(con)
    yield con
    con.close()
