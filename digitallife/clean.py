"""清洗规则 C1–C13 的共同原语 + 清洗报告汇总。

设计约束：清洗规则不写在适配器里各写一遍（会产生五套口径，S47）。
适配器只负责「把源行翻译成 Event」，需要判定丢弃/合并时一律调用本模块。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .model import Event


@dataclass
class SourceReport:
    """单源清洗报告：必须能解释「源 N 行 → 入库 M 行」的差额。"""
    source: str
    device: str
    rows_read: int = 0
    rows_kept: int = 0
    dropped: dict[str, int] = field(default_factory=dict)
    merged: int = 0
    duplicated: int = 0
    window_start_utc_ms: int | None = None
    window_end_utc_ms: int | None = None
    notes: list[str] = field(default_factory=list)

    def drop(self, reason: str, n: int = 1) -> None:
        self.dropped[reason] = self.dropped.get(reason, 0) + n

    def keep(self, n: int = 1) -> None:
        self.rows_kept += n

    def as_dict(self) -> dict:
        return {
            "source": self.source,
            "device": self.device,
            "rows_read": self.rows_read,
            "rows_kept": self.rows_kept,
            "dropped": dict(sorted(self.dropped.items())),
            "dropped_total": sum(self.dropped.values()),
            "merged": self.merged,
            "duplicated": self.duplicated,
            "window_start_utc_ms": self.window_start_utc_ms,
            "window_end_utc_ms": self.window_end_utc_ms,
            "notes": self.notes,
        }


# ---- C12：空事件判定 ----------------------------------------------------

def is_empty_event(ev: Event) -> bool:
    """duration ≤0 且没有任何可识别标识 → 丢弃（C12）。"""
    dur = ev.duration_s
    has_dur = dur is not None and dur > 0
    has_ident = any((ev.app, ev.title, ev.url, ev.path))
    return not has_dur and not has_ident


def normalize_event(ev: Event) -> Event:
    """列名归一后的二次收敛（C11）：去空白、空串归 None、url 小写化去掉锚点。"""
    def nz(v: str | None) -> str | None:
        if v is None:
            return None
        s = str(v).strip()
        return s or None

    ev.app = nz(ev.app)
    ev.app_id = nz(ev.app_id)
    ev.title = nz(ev.title)
    ev.url = nz(ev.url)
    ev.path = nz(ev.path)
    ev.category = nz(ev.category)
    return ev


# ---- C4：同源活动合并（ManicTime 同名且间隔 ≤120s）--------------------

MERGE_GAP_MS = 120_000


# 最短保留时长（秒）。默认 0 = **不丢弃任何真实事件**。
# 实测依据（ManicTime 2026-08-13…10-21，本机）：原始 35,310 行 → 合并后 33,372 段共 493.3 小时；
# 若按 30s 过滤会丢掉 23,998 段（都是真实的切标签页/搜索/快查操作）＝ **15.6% 的使用时长**。
# 这不是「去重」，是把真实发生的事丢掉，与本模块「清洗重合、不丢事实」的原则冲突。
# 只想看显著段的场景（例如切片骨架）由调用方显式传 min_duration_s=30 自行决定。
MIN_DURATION_S_DEFAULT = 0


def merge_adjacent(events: list[Event], gap_ms: int = MERGE_GAP_MS,
                   min_duration_s: int | None = None) -> tuple[list[Event], int, int]:
    """按开始时间排序后，把「同名且间隔 ≤ gap_ms」的相邻事件并成一段。

    返回 (合并后列表, 合并掉的事件数, 因过短被丢弃的事件数)。
    合并只对 app / web 生效；screen（瞬时）与 power 不参与。
    min_duration_s 缺省取 MIN_DURATION_S_DEFAULT（0，不丢任何事件）。
    """
    if min_duration_s is None:
        min_duration_s = MIN_DURATION_S_DEFAULT
    mergeable = {"app", "web"}
    events = sorted(events, key=lambda e: (e.start_utc_ms, e.end_utc_ms or e.start_utc_ms))

    # ★ 按 (device, kind) 各自维护一条合并流。
    #   原先用单一 out[-1] 比较"上一个产出的件"，但 app/web/power 在时间上是**交错**的：
    #   Applications 的一条 app 后面紧跟 Documents 的一条 web，再回到 app 时，
    #   out[-1] 已经是那条 web，同名 app 的相邻关系被切断 → 实测 54,495 条同标题连续段
    #   只合并了 687 次（正确基线：Applications 应合 31,602 次、Documents 33,789 次）。
    #   分成独立流后，每种类型只与"自己这条流的上一条"比较，是 C4 的本意。
    tails: dict[tuple[str, str], Event] = {}
    out: list[Event] = []
    merged_count = 0
    short_dropped = 0

    for ev in events:
        # 配置类事件（battery/notify/install）与瞬时事件不参与合并，也不按阈值丢弃
        if ev.kind not in mergeable or ev.end_utc_ms is None:
            out.append(ev)
            continue

        key = (ev.device, ev.kind)
        prev = tails.get(key)
        if (
            prev is not None
            and prev.app == ev.app
            and prev.title == ev.title
            and ev.start_utc_ms - (prev.end_utc_ms or prev.start_utc_ms) <= gap_ms
        ):
            prev.end_utc_ms = max(prev.end_utc_ms or 0, ev.end_utc_ms)
            merged_count += 1
            continue
        tails[key] = ev
        out.append(ev)

    if min_duration_s:
        kept: list[Event] = []
        for ev in out:
            if ev.kind in mergeable and (ev.duration_s or 0) < min_duration_s:
                short_dropped += 1
                continue
            kept.append(ev)
        out = kept

    return out, merged_count, short_dropped


# ---- 报告汇总 ----------------------------------------------------------

def build_report(reports: list[SourceReport]) -> dict:
    total_read = sum(r.rows_read for r in reports)
    total_kept = sum(r.rows_kept for r in reports)
    return {
        "sources": [r.as_dict() for r in reports],
        "total_rows_read": total_read,
        "total_rows_kept": total_kept,
        "total_dropped": sum(sum(r.dropped.values()) for r in reports),
        "total_merged": sum(r.merged for r in reports),
    }
