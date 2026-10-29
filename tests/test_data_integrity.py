"""DL-2 / DL-3 / DL-4 / DL-7 的回归锚点（从 smoke.py 迁入，T2）。

这四条都是**曾经静默丢数据**的坑，每条都写过真实损失数字。
迁进 pytest 的目的是让它们能「单独跑、秒级反馈」，而不是挤在 smoke 的 1,400 行里。
"""
from __future__ import annotations

import pytest

from digitallife.ingest.clean import merge_adjacent, MIN_DURATION_S_DEFAULT
from digitallife.domain.devices import by_kind, name_of, sql_device_filter, query_aliases
from digitallife.ingest.model import Event


def ev(dev, start, dur_ms, app="X", title="t", kind="app", **kw):
    return Event(device=dev, source="manictime", kind=kind,
                 start_utc_ms=start, end_utc_ms=start + dur_ms,
                 app=app, title=title, **kw)


# ---------------------------------------------------------------- DL-2 幂等键

def test_dedupe_key_includes_title():
    """DL-2：幂等键必须包含 title。

    能抓住的真实回归：从 key 里删掉 title → 同秒不同标题被合并。
    历史损失：漏掉电量字段，静默丢 11,075 行。
    """
    a = ev("pc-main", 1_700_000_000_000, 60_000, title="t1")
    b = ev("pc-main", 1_700_000_000_000, 60_000, title="t2")
    assert a.event_id != b.event_id


def test_dedupe_key_includes_device():
    """DL-2：设备不同 → event_id 不同（否则跨设备事件互相覆盖）。"""
    a = ev("pc-main", 1_700_000_000_000, 60_000)
    b = ev("phone-1", 1_700_000_000_000, 60_000)
    assert a.event_id != b.event_id


def test_dedupe_key_ignores_payload():
    """DL-2：无关 payload 变化不产生新事件（保持幂等）。"""
    a = ev("pc-main", 1_700_000_000_000, 60_000, payload={"x": 1})
    b = ev("pc-main", 1_700_000_000_000, 60_000, payload={"x": 2})
    assert a.event_id == b.event_id


def test_dedupe_key_includes_battery_field():
    """DL-2：电量字段参与幂等键。

    能抓住的真实回归：把 battery 分支从 dedupe_key 里去掉 → 同秒同标题不同电量被合并，
    曾静默丢 11,075 行。

    ⚠️ 字段名是 `battery_pct`（model.py:67 真值），不是 payload 里随便一个 key。
    """
    a = Event(device="phone-1", source="screenmemo", kind="battery",
              start_utc_ms=1_700_000_000_000, end_utc_ms=None,
              payload={"battery_pct": 10})
    b = Event(device="phone-1", source="screenmemo", kind="battery",
              start_utc_ms=1_700_000_000_000, end_utc_ms=None,
              payload={"battery_pct": 90})
    assert a.event_id != b.event_id


# ---------------------------------------------------------------- DL-3 相邻合并分流

def test_merge_adjacent_splits_by_device_kind():
    """DL-3：merge_adjacent 必须按 (device, kind) 分流后再合并。

    能抓住的真实回归：对混合流做相邻合并，合并次数从 46,665 掉到 687（差 67 倍），
    表现为日报时间线大幅缩短。注入「去掉 groupby 键」→ 本用例必须变红。
    """
    base = 1_700_000_000_000
    # 构造：pc-main 两段 app，中间夹 phone-1 一段 app，时间上首尾相接（间隔 0）。
    # 若不按 (device, kind) 分流，三段会被并成一段且 device 取第一条 = 跨设备吞并。
    rows = [
        ev("pc-main", base, 60_000, title="a"),
        ev("phone-1", base + 60_000, 60_000, title="a"),
        ev("pc-main", base + 120_000, 60_000, title="a"),
    ]
    merged, mcount, _ = merge_adjacent(rows)
    # 分流后的正确结果（实测真值，不是我先验猜测）：
    #   pc-main 两段首尾相接（base 0-60s, base 120-180s 之间被 phone 占住但 pc 自己连续）
    #   → 按 (device,kind) 分流后 pc-main 并成 **1 段 0-180s**；phone-1 独立 1 段 → 共 2 段。
    # 若**不**分流（同一混合流两两合并）：三段会跨设备首尾吞并成 1 段，
    # 且 device 取第一条 → phone-1 那 60 秒的归属被抹掉。判据就是「设备数」与「总段数」。
    assert len(merged) == 2, "实得 %d 段（不分流会成 1 段）" % len(merged)
    assert {r.device for r in merged} == {"pc-main", "phone-1"}, \
        "合并不得跨设备吞并，实得 %r" % ({r.device for r in merged},)
    # 再钉死时长：pc-main 那一段必须是完整的 180 秒（跨设备分流没切断它）
    pc = [r for r in merged if r.device == "pc-main"][0]
    assert (pc.end_utc_ms - pc.start_utc_ms) == 180_000, \
        "pc-main 应为连续 180s，实得 %d" % (pc.end_utc_ms - pc.start_utc_ms)


def test_merge_adjacent_merges_same_stream():
    """DL-3 正向：同设备同 kind 且首尾相接 → 必须合并成 1 段。"""
    base = 1_700_000_000_000
    rows = [ev("pc-main", base, 60_000, title="a"),
            ev("pc-main", base + 60_000, 60_000, title="a")]
    merged, _, _ = merge_adjacent(rows)
    assert len(merged) == 1


# ---------------------------------------------------------------- DL-4 最短时长默认值

def test_min_duration_default_is_zero():
    """DL-4：MIN_DURATION_S_DEFAULT 必须为 0。

    能抓住的真实回归：默认值改成 30 秒 → 丢 23,998 段 = 15.6% 真实时长。
    """
    assert MIN_DURATION_S_DEFAULT == 0


def test_short_event_not_dropped_by_default():
    """DL-4：默认调用不得丢掉短事件。"""
    base = 1_700_000_000_000
    rows = [ev("pc-main", base, 1_000, title="1秒事件")]
    merged, _, dropped = merge_adjacent(rows)
    assert len(merged) == 1
    assert dropped == 0


def test_short_event_dropped_only_when_explicit():
    """DL-4：需要粗粒度时由**调用方显式传** min_duration_s。"""
    base = 1_700_000_000_000
    rows = [ev("pc-main", base, 1_000, title="1秒事件")]
    _, _, dropped = merge_adjacent(rows, min_duration_s=30)
    assert dropped == 1


# ---------------------------------------------------------------- DL-7 设备过滤

def test_sql_device_filter_includes_legacy_aliases():
    """DL-7：按设备过滤必须走 sql_device_filter，且包含旧别名。

    能抓住的真实回归：写死 device = ? → 设备 id 从 pc/phone 改成 pc-main/phone-1 时，
    9 处精确匹配（daily×4 / slicer×3 / vision×1 / publish×2）全部静默返回空。
    """
    frag, params = sql_device_filter("pc-main", "s.device")
    assert "IN (" in frag, "必须是 IN (?...) 形式，实得 %r" % frag
    assert "pc-main" in params
    assert "pc" in params, "旧别名 pc 必须包含，实得 %r" % (params,)


def test_query_aliases_cover_bare_values():
    """DL-7：query_aliases 必须把 pc-main 解析成含裸值 pc 的集合（只读兼容）。"""
    al = query_aliases("pc-main")
    assert "pc-main" in al and "pc" in al


def test_legacy_bare_device_resolves_to_registered():
    """DL-7/DL-21：旧裸值 pc / phone 必须能解析到注册 id 与真名。"""
    assert name_of("pc") == "D-001"
    assert name_of("phone") == "iQQQZ3"


def test_by_kind_returns_device_objects():
    """DL-21 前置：by_kind 返回的是 Device 对象（不是字符串 id）。

    ⚠️ 这一点很关键：`len(by_kind(kind)) > 1` 曾被当成「要不要折叠成电脑/手机」的判据，
    而它返回 Device 元组。改造后该判据已删除（一律用 name_of），本用例只锁返回形状，
    防止有人再把它当 id 列表用（那样 `name_of(dev_obj)` 会静默返回错误值）。
    """
    pcs = by_kind("pc")
    assert len(pcs) == 1
    assert pcs[0].device_id == "pc-main"
    assert pcs[0].name == "D-001"
    assert len(by_kind("phone")) == 2
