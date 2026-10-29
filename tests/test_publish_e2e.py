"""publish 的端到端产物断言（T2，testing.md 标注的「最大空白」）。

为什么这块是空白：`publish.py` 575 行零覆盖，而它产出的 md/html/manifest 是**最终交付物**。
改错了代码，smoke 可能仍绿（它验的是函数返回值），但用户打开文件发现内容不对。

原则（S21）：断言必须**直接消费真实产物**，不得在测试里重建路径或内容。
本文件用**临时库 + 临时 vault**，跑一次真 publish，然后验产物字节与结构。
"""
from __future__ import annotations

import re
import sqlite3

import pytest


@pytest.fixture
def pub_env(tmp_path, monkeypatch):
    """最小可发布环境：临时库 + 临时 vault 目录。

    为什么不碰真 vault（S16）：publish 会整体覆盖目标文件，
    指向真 vault 就等于拿 753 篇交付物做试验。
    """
    db = tmp_path / "test.sqlite"
    vault = tmp_path / "vault"
    (vault / "20-日报").mkdir(parents=True)
    (vault / "10-时段").mkdir(parents=True)
    (vault / "_index").mkdir(parents=True)

    monkeypatch.setenv("DL_DB", str(db))
    monkeypatch.setenv("DL_WORK_DIR", str(tmp_path / "work"))

    from digitallife import paths
    monkeypatch.setattr(paths, "VAULT", vault, raising=False)
    monkeypatch.setattr(paths, "DAILY_DIR", vault / "20-日报", raising=False)
    monkeypatch.setattr(paths, "SLICE_DIR", vault / "10-时段", raising=False)
    monkeypatch.setattr(paths, "INDEX_DIR", vault / "_index", raising=False)

    from digitallife.ingest.schema import init_db
    con = sqlite3.connect(str(db), check_same_thread=False)
    con.row_factory = sqlite3.Row
    init_db(con)
    yield con, vault
    con.close()


def _seed(con, day="2026-10-20"):
    """造一天的最小事件流：一台 PC，两条 app 事件 + 一段 power。"""
    from digitallife.ingest.model import Event
    from digitallife.ingest.store import upsert_events
    from digitallife.ingest.tz import local_to_utc_ms, parse_flexible

    d0 = local_to_utc_ms(parse_flexible(day + " 09:00:00"))
    hh = 3_600_000
    upsert_events(con, [
        Event(device="pc-main", source="manictime", kind="app",
              start_utc_ms=d0, end_utc_ms=d0 + 2 * hh, app="Obsidian", title="笔记"),
        Event(device="pc-main", source="manictime", kind="power",
              start_utc_ms=d0, end_utc_ms=d0 + 2 * hh, title="Active"),
    ], "run-test")
    con.commit()
    return day


def test_publish_writes_daily_md_with_contract_structure(pub_env):
    """产物级：publish 必须落一份**符合数据侧契约**的日报 md。

    能抓住的真实回归：
      · 六节缺一（旧实现「某节无数据就整节消失」，753 篇出现 26 种结构）
      · 设备节标题退回折叠式「## 电脑使用」（N5 用户裁决要 D-001）
      · 残留已废弃的 ## 今天 / ## 展开看全量
    """
    con, vault = pub_env
    day = _seed(con)
    from digitallife.publish import publish_all

    publish_all(con, day_keys=[day], make_zip=False)

    md = vault / "20-日报" / (day + ".md")
    assert md.exists(), "publish 没有产出日报 md"
    text = md.read_text(encoding="utf-8")

    # ① 设备节标题用真名（N5 / DL-21）
    assert "## D-001 使用" in text, text[:300]
    assert "## 电脑使用" not in text

    # ② 六个固定 ### 齐全且顺序固定（数据侧 §D.1 硬约束 2）
    got = [l.strip() for l in text.splitlines() if l.strip().startswith("### ")]
    six = ["### 应用", "### 网站", "### 专注段", "### 时间线", "### 离开时段", "### 视觉总结"]
    assert got[:6] == six, got

    # ③ 无废弃二级节
    assert "## 今天" not in text
    assert "## 展开看全量" not in text

    # ④ 禁止省略号 Rolodex（数据侧 §D.3）
    assert "……" not in text


def test_publish_manifest_counts_match_db(pub_env):
    """产物级：manifest.json 的 counts 必须与库内真值一致。

    能抓住的真实回归：发布与库不同步（改了库但 manifest 还是旧数，/api/dl/stats 会撒谎）。
    """
    import json
    con, vault = pub_env
    day = _seed(con)
    from digitallife.publish import publish_all

    publish_all(con, day_keys=[day], make_zip=False)

    mpath = vault / "_index" / "manifest.json"
    assert mpath.exists(), "publish 没有产出 manifest.json"
    manifest = json.loads(mpath.read_text(encoding="utf-8"))

    # 键名以真值为准：counts = {event, slice, slice_result, daily, ndjson_rows}
    # （单数 event，不是 events —— 我第一版猜错了，KeyError 抓出来的）
    real = con.execute("SELECT COUNT(*) FROM event").fetchone()[0]
    assert manifest["counts"]["event"] == real, (manifest["counts"], real)
    assert manifest["counts"]["daily"] == 1, manifest["counts"]
    # 交叉验证：ndjson_rows 必须与 event 同数（否则导出漏行）
    assert manifest["counts"]["ndjson_rows"] == real, manifest["counts"]


def test_publish_is_idempotent_on_same_input(pub_env):
    """产物级：同一输入连跑两次，日报 md **逐字节相同**。

    能抓住的真实回归：产物里混进了运行时刻/随机序，导致每次 publish 都产生 diff，
    无法用「产物 hash 不变」证明重构没改行为（T3/T4/N6 全靠它）。
    """
    import hashlib
    con, vault = pub_env
    day = _seed(con)
    from digitallife.publish import publish_all

    publish_all(con, day_keys=[day], make_zip=False)
    md = vault / "20-日报" / (day + ".md")
    h1 = hashlib.sha256(md.read_bytes()).hexdigest()

    publish_all(con, day_keys=[day], make_zip=False)
    h2 = hashlib.sha256(md.read_bytes()).hexdigest()
    assert h1 == h2, "两次 publish 的日报 md 不一致（%s vs %s）" % (h1[:8], h2[:8])
