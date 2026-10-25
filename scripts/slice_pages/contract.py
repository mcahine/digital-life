"""时段网页的页面契约（主代理所有，页面模块只读）。

一次生成产出 **一天一个 HTML**（2026-10-22 用户裁决，推翻前一天的 9 页包）：

    04-Areas/数字人生/10-时段/YYYY-MM-DD.html

页面由四个 section 组成（时间线 / 应用 / 网站 / 视觉），section 内部按设备用 tab 切换。
片层 md 单独落 10-时段/片层/YYYY-MM-DD/。

数据全部来自数据库（event / slice / slice_result / daily_summary），网页只是渲染层：
**没有第二个数据源，也没有中间 JSON**。删掉那个 html 重跑即可重建。

section 模块必须提供：

    ANCHOR = "apps"                  # 锚点 id（目录浮钮跳转用）
    LABEL  = "应用"                  # section 标题
    def panel(pid, ctx, hidden) -> (str, int)   # (HTML 片段, 行数)
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from digitallife import devices as dv
from digitallife import tz


def day_range_ms(date_key: str) -> tuple[int, int]:
    """本地自然日 [00:00, 次日 00:00) 的 UTC 毫秒。"""
    from datetime import datetime, timedelta
    d0 = datetime.strptime(date_key, "%Y-%m-%d")
    return tz.local_to_utc_ms(d0), tz.local_to_utc_ms(d0 + timedelta(days=1))


def device_label(device: str) -> str:
    """设备显示名：直接给真实设备名（D-001 / iQQQZ3）。

    用户 2026-10-22：「电脑和手机完全没必要，就展示设备名就好，手机就是 iQQQZ3，
    电脑是 D-001」。所以这里返回设备注册里的 name，不再按 kind 折叠成「电脑/手机」。
    设备名没登记时退回 device_id。
    """
    try:
        return dv.name_of(device) or device
    except (ValueError, AttributeError):
        return device


def load_slices(con: sqlite3.Connection, date_key: str, device: str) -> list[dict]:
    """当日该设备的全部片 + 视觉结果（按开始时间排序）。

    summary 为空字符串 = 该片没有可用总结；present 区分「跑了但内容空」与「压根没跑」。
    structured 是原始 JSON 文本（解析交给消费方，缺字段要容忍）。
    """
    lo, hi = day_range_ms(date_key)
    # 一次性取出本日所有「带画面描述」的 screen 事件起始时间（毫秒）。
    # 条件尽量走 ix_event_time 索引：先按时间范围，再筛 kind。
    _desc_ts = [
        row[0] for row in con.execute(
            "SELECT start_utc_ms FROM event "
            "WHERE start_utc_ms >= ? AND start_utc_ms < ? AND kind='screen' "
            "AND payload LIKE '%\"description\"%'",
            (lo, hi))
    ]
    where, params = dv.sql_device_filter(device, "s.device")
    rows = con.execute(
        "SELECT s.slice_id, s.start_utc_ms, s.end_utc_ms, s.image_count, s.event_count, "
        "       r.overall_summary, r.structured_json, r.model, r.conflict_count, "
        "       s.status, s.split_of "
        "FROM slice s LEFT JOIN slice_result r ON r.slice_id = s.slice_id "
        "WHERE s.start_utc_ms >= ? AND s.start_utc_ms < ? AND " + where + " "
        "ORDER BY s.start_utc_ms",
        (lo, hi, *params),
    ).fetchall()
    out: list[dict] = []
    for r in rows:
        # 该片到底有没有画面描述 —— 真值在事件层 payload.description。
        # 用**一次性批量**查出的时间点集合判断，绝不逐片查库：
        # 逐片查会退化成「每片扫一遍全表 screen 事件」，实测单日 load_slices 要 70–103 秒
        # （EXPLAIN 显示走的是 ix_event_kind，没用上时间索引）。
        _ss, _ee = int(r["start_utc_ms"]), int(r["end_utc_ms"])
        _has_desc = any(_ss <= t < _ee for t in _desc_ts)
        out.append({
            "slice_id": r["slice_id"],
            "start_utc_ms": r["start_utc_ms"],
            "end_utc_ms": r["end_utc_ms"],
            "start": tz.utc_ms_to_local(r["start_utc_ms"]).strftime("%H:%M:%S"),
            "end": tz.utc_ms_to_local(r["end_utc_ms"]).strftime("%H:%M:%S"),
            "image_count": int(r["image_count"] or 0),
            "event_count": int(r["event_count"] or 0),
            "summary": (r["overall_summary"] or "").strip(),
            "structured": r["structured_json"],
            "model": r["model"] or "",
            "conflict_count": int(r["conflict_count"] or 0),
            "present": r["model"] is not None,
            "has_description": _has_desc,
            # 父子片关系（2026-10-24）。父片 image_count 超上限（24）会被递归二分，
            # 只有叶子片跑视觉。页面必须区分，否则父片会显示成
            # 「未跑视觉模型 / 本片尚无视觉总结」—— 那是误导：它其实被切分了。
            "status": (r["status"] or ""),
            "split_of": (r["split_of"] or ""),
        })
    return out



@dataclass
class Ctx:
    """一次渲染需要的全部输入（多设备一次性装好，页面各取所需）。

    structures[dev] = dbview.build_day_structure(...) 的原样返回
      （apps / timeline / focus_blocks / away / screens / totals / switch_count）
    slices[dev]     = 当日该设备的全部片 + 视觉结果（见 load_slices）
    device          = 本页针对的设备 id；跨设备页（视觉总结）为 ""
    """

    date_key: str
    con: sqlite3.Connection
    devices: list[str] = field(default_factory=list)          # 当日有数据的设备
    structures: dict = field(default_factory=dict)            # dev -> structure
    slices: dict = field(default_factory=dict)                # dev -> [slice]
    device: str = ""                                          # 本页设备

    # ---- 常用快捷方式

    @property
    def structure(self) -> dict:
        return self.structures.get(self.device) or {}

    @property
    def dev_slices(self) -> list[dict]:
        return self.slices.get(self.device) or []

    @property
    def dev_label(self) -> str:
        return device_label(self.device) if self.device else "全部设备"

    @property
    def has_data(self) -> bool:
        st = self.structure
        return bool(st.get("timeline") or st.get("apps"))

    def all_slices(self) -> list[dict]:
        """跨设备：每片补上 dev 字段，按开始时间排序。"""
        out: list[dict] = []
        for dev in self.devices:
            for s in self.slices.get(dev) or []:
                out.append(dict(s, dev=dev, dev_label=device_label(dev)))
        out.sort(key=lambda x: (x.get("start_utc_ms") or 0))
        return out


PC_DEVICE = "pc-main"
PHONE_DEVICE = "phone-1"
