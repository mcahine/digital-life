"""Tai（桌面应用使用统计）CSV 适配器。

数据形态（实测 2026-10-21，00-Inbox/Tai/）：
  Tai数据(...)-时段.csv        2281 行  时段,应用,描述,时长,分类
  Tai数据(...)-每日.csv         755 行  日期,应用,描述,时长,分类   → 只做交叉校验，不产事件（C8）
  Tai网页统计数据(...).csv     13287 行  时间,标题,网址,时长
  两个同名 .xlsx                          → 不读（无第三方依赖，且与 CSV 同源）

时间格式 "12/31/2024 16:00:00"（月/日/年）→ tz.parse_flexible；时长单位是**秒**，不是 HH:MM:SS。
"""
from __future__ import annotations

import csv
import os
from pathlib import Path
from typing import Iterator

from .. import paths
from ..clean import SourceReport, is_empty_event, normalize_event
from ..model import Event
from ..tz import local_to_utc_ms, parse_flexible
from .base import Fingerprint, SourceAdapter, sha256_file

ROOT_NAME = "Tai"
PERIOD_SUFFIX = "-时段.csv"
DAILY_SUFFIX = "-每日.csv"
WEB_MARK = "网页统计"

PERIOD_HEADER = ("时段", "应用", "描述", "时长", "分类")
WEB_HEADER = ("时间", "标题", "网址", "时长")


def _rows(path: Path) -> list[list[str]]:
    with open(path, encoding="utf-8-sig", newline="") as fh:
        return [r for r in csv.reader(fh)]


class TaiSource(SourceAdapter):
    name = "tai"
    device = "pc-main"      # 历史应用统计，归属那台机器

    def __init__(self, root: Path | None = None) -> None:
        super().__init__(root)
        override = os.environ.get("DL_TAI_ROOT")
        if override:
            self.root = Path(override)
        elif self.root is None:
            self.root = paths.INBOX / ROOT_NAME

    # ---- 定位 --------------------------------------------------------

    def _require_root(self) -> Path:
        root = Path(self.root) if self.root else None
        if root is None or not root.is_dir():
            raise FileNotFoundError(
                f"Tai 导出目录未找到（{self.root}）。"
                f"把 '{ROOT_NAME}' 放到 {paths.INBOX}，或用环境变量 DL_TAI_ROOT 指向实际路径。"
            )
        return root

    def period_csv(self) -> Path:
        for p in sorted(self._require_root().glob("*.csv")):
            if p.name.endswith(PERIOD_SUFFIX):
                return p
        raise FileNotFoundError(f"{self._require_root()} 下没有 '*{PERIOD_SUFFIX}'")

    def web_csv(self) -> Path | None:
        for p in sorted(self._require_root().glob("*.csv")):
            if WEB_MARK in p.name and not p.name.endswith(DAILY_SUFFIX):
                return p
        return None

    def daily_csv(self) -> Path | None:
        """只用于交叉校验，不产事件（C8）。"""
        for p in sorted(self._require_root().glob("*.csv")):
            if p.name.endswith(DAILY_SUFFIX):
                return p
        return None

    def fingerprint(self) -> list[Fingerprint]:
        root = self._require_root()
        fps: list[Fingerprint] = []
        for p in sorted(root.glob("*.csv")):
            st = p.stat()
            note = ""
            if p.name.endswith(DAILY_SUFFIX):
                note = "每日汇总：仅交叉校验，不产事件（C8）"
            elif p.name.endswith(PERIOD_SUFFIX):
                note = "时段明细：kind=app"
            elif WEB_MARK in p.name:
                note = "网页统计：kind=web"
            fps.append(Fingerprint(f"tai:{p.stem}", str(p), sha256_file(p),
                                   st.st_size, st.st_mtime, note))
        return fps

    # ---- 事件 --------------------------------------------------------

    def iter_events(self, report: SourceReport) -> Iterator[Event]:
        seen: set[str] = set()
        out: list[Event] = []

        def emit(ev: Event) -> None:
            if ev.event_id in seen:
                report.duplicated += 1
                return
            seen.add(ev.event_id)
            out.append(ev)

        # 时段：kind=app
        path = self.period_csv()
        rows = _rows(path)
        if rows and tuple(c.strip() for c in rows[0][:5]) != PERIOD_HEADER:
            raise ValueError(f"{path.name} 表头不符，期望 {PERIOD_HEADER}，实际 {rows[0][:5]}")
        for row in rows[1:]:
            report.rows_read += 1
            if len(row) < 5:
                report.drop("short_row")
                continue
            when, exe, desc, secs, category = (c.strip() for c in row[:5])
            try:
                start = local_to_utc_ms(parse_flexible(when))
            except ValueError:
                report.drop("bad_timestamp")
                continue
            try:
                dur = int(float(secs))
            except ValueError:
                report.drop("bad_duration")
                continue
            if dur <= 0:
                report.drop("zero_duration")
                continue
            emit(Event(device=self.device, source=self.name, kind="app",
                       start_utc_ms=start, end_utc_ms=start + dur * 1000,
                       app=desc or exe, app_id=exe, title=desc or exe,
                       category=category or None,
                       payload={"raw_time": when, "duration_s": dur}))

        # 网页：kind=web
        zero_instant = 0
        web = self.web_csv()
        if web is not None:
            wrows = _rows(web)
            if wrows and tuple(c.strip() for c in wrows[0][:4]) != WEB_HEADER:
                raise ValueError(f"{web.name} 表头不符，期望 {WEB_HEADER}，实际 {wrows[0][:4]}")
            for row in wrows[1:]:
                report.rows_read += 1
                if len(row) < 4:
                    report.drop("short_row")
                    continue
                when, title, url, secs = (c.strip() for c in row[:4])
                try:
                    start = local_to_utc_ms(parse_flexible(when))
                except ValueError:
                    report.drop("bad_timestamp")
                    continue
                try:
                    dur = int(float(secs))
                except ValueError:
                    report.drop("bad_duration")
                    continue
                if dur < 0:
                    report.drop("negative_duration")
                    continue
                if not url:
                    report.drop("missing_url")
                    continue
                # 实测 1615 行时长为 0：那是「访问过但停留不到 1 秒」的真实记录，
                # 不是坏数据，保留为瞬时访问（end=start），不静默丢弃。
                if dur == 0:
                    zero_instant += 1
                emit(Event(device=self.device, source=self.name, kind="web",
                           start_utc_ms=start, end_utc_ms=start + dur * 1000,
                           title=title or url, url=url, category="web",
                           payload={"raw_time": when, "duration_s": dur}))

        if zero_instant:
            report.notes.append(
                f"{zero_instant} 条网页记录原始时长为 0（访问不足 1 秒），"
                f"保留为 end=start 的瞬时访问，未丢弃")

        # C8：每日汇总只做交叉校验，不产事件
        daily = self.daily_csv()
        if daily is not None:
            drows = _rows(daily)
            data = [r for r in drows[1:] if len(r) >= 5]
            report.rows_read += len(data)
            report.drop("daily_summary_crosscheck_only", len(data))
            total_daily = 0
            for r in data:
                try:
                    total_daily += int(float(r[3]))
                except ValueError:
                    pass
            period_total = sum(e.duration_s or 0 for e in out if e.kind == "app")
            report.notes.append(
                f"每日汇总交叉校验：每日合计 {total_daily}s vs 时段合计 {period_total}s"
                f"（{daily.name}）")

        if out:
            report.window_start_utc_ms = min(e.start_utc_ms for e in out)
            report.window_end_utc_ms = max(e.end_utc_ms or e.start_utc_ms for e in out)

        for ev in sorted(out, key=lambda e: (e.start_utc_ms, e.kind)):
            ev = normalize_event(ev)
            if is_empty_event(ev):
                report.drop("empty_event")
                continue
            report.keep()
            yield ev
