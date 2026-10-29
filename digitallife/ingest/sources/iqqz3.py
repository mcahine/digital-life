"""IQQZ3 / App Usage（Android 使用记录 CSV 导出）适配器。

数据形态（实测 2026-10-21）：
  00-Inbox/IQQZ3 Appusage/
    2025-01-05 … 2025-04-30/          108 个顶层日期目录
    daily/2025-05-01 … 2026-09-26/    361 个 daily 子目录（与顶层日期区间不重叠，接续）
    *.zip                             108 个与同名目录内容相同的副本 → 只校验 sha256，不读内容（C1）

每个日期目录内 9 类 CSV。**文件布局是三段式**（实测，全部 4221 个 CSV 同构）：

    <表头>
    <数据行 …>
    <空行>                     ← 分隔线，其后全是页脚，必须整段丢弃
    <副表头 / 历史标题行>      例如 'Date','Usage time' 或 '活动历史, 2025年1月4日'
    <副表数据 / 空行>
    'Created by App Usage on …'

只截到第一个空行为止，否则页脚会被当成数据行（实测会多出 2626 条假数据）。
"""
from __future__ import annotations

import csv
import hashlib
import os
from datetime import datetime
from pathlib import Path
from typing import Iterable, Iterator

from ... import paths
from ..clean import SourceReport, is_empty_event, normalize_event
from ..model import Event
from ..tz import local_to_utc_ms, parse_flexible, parse_hms_to_seconds
from .base import Fingerprint, SourceAdapter, sha256_file

ROOT_NAME = "IQQZ3 Appusage"

#: 契约 §2.3：这三类无可用记录
SKIP_KINDS = ("Place", "CheckDevice", "DailyUsage")

#: 各类 CSV 的期望表头（用于发现格式漂移；不匹配时记 note 并跳过该文件）
HEADERS = {
    "Activity": ("App name", "Date", "Time", "Duration"),
    "Usage": ("App name", "Usage time", "Access count"),
    "Battery": ("Date", "Time", "Battery(%)", "Note"),
    "Notification": ("App name", "Count"),
    "Install": ("App name", "Version", "Version Code", "Type", "Date", "Time"),
    "App": ("App name", "Package name", "App version", "App version code",
            "Updated time", "Category", "Installed"),
}
#: 每类 CSV 的数据行最少列数
MIN_COLS = {"Activity": 4, "Usage": 3, "Battery": 4, "Notification": 2,
            "Install": 6, "App": 2}


def _dir_digest(files: Iterable[Path]) -> str:
    """目录内容摘要（文件名+大小+mtime）：469 个目录不必读正文即可反映变化。"""
    h = hashlib.sha256()
    for f in sorted(files):
        st = f.stat()
        h.update(f"{f.name}|{st.st_size}|{st.st_mtime_ns}".encode("utf-8"))
    return h.hexdigest()


def _day_of(dirname: str) -> int:
    """'2025-01-05' → 当天 00:00 本地时间的 UTC ms。"""
    return local_to_utc_ms(datetime.strptime(dirname, "%Y-%m-%d"))


def _blank(row: list[str]) -> bool:
    return not row or not any(c.strip() for c in row)


def _read_csv(path: Path) -> tuple[list[str], list[list[str]]]:
    """返回 (表头, 数据行)。数据行 = 表头之后、第一个空行之前的所有非空行。"""
    with open(path, encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.reader(fh))
    if not rows:
        return [], []
    header = [c.strip() for c in rows[0]]
    data: list[list[str]] = []
    for row in rows[1:]:
        if _blank(row):
            break                      # 分隔线之后是页脚，整段丢弃
        data.append(row)
    return header, data


def _check_header(kind: str, path: Path, header: list[str], report: SourceReport) -> bool:
    want = HEADERS.get(kind)
    if want is None:
        return True
    if tuple(header[:len(want)]) == want:
        return True
    report.notes.append(f"表头不符，跳过：{path.parent.name}/{path.name} 实际={header[:len(want)]}")
    return False


class Iqqz3Source(SourceAdapter):
    name = "iqqz3"
    device = "phone-1"      # 多手机时用 DL_DEVICE_IQQZ3 覆盖到 phone-2 ...

    def __init__(self, root: Path | None = None) -> None:
        super().__init__(root)
        override = os.environ.get("DL_IQQZ3_ROOT")
        if override:
            self.root = Path(override)
        elif self.root is None:
            self.root = paths.INBOX / ROOT_NAME

    # ---- 定位 --------------------------------------------------------

    def _require_root(self) -> Path:
        root = Path(self.root) if self.root else None
        if root is None or not root.is_dir():
            raise FileNotFoundError(
                f"IQQZ3 导出目录未找到（{self.root}）。"
                f"把 '{ROOT_NAME}' 放到 {paths.INBOX}，或用环境变量 DL_IQQZ3_ROOT 指向实际路径。"
            )
        return root

    def day_dirs(self) -> list[Path]:
        """顶层日期目录 + daily/ 子目录，按名字（=日期）排序。"""
        root = self._require_root()
        out = [p for p in root.iterdir() if p.is_dir() and p.name != "daily" and p.name[:2] == "20"]
        daily = root / "daily"
        if daily.is_dir():
            out += [p for p in daily.iterdir() if p.is_dir() and p.name[:2] == "20"]
        return sorted(out, key=lambda p: p.name)

    def zip_files(self) -> list[Path]:
        return sorted(self._require_root().glob("*.zip"))

    def fingerprint(self) -> list[Fingerprint]:
        fps: list[Fingerprint] = []
        for d in self.day_dirs():
            files = list(d.glob("*.csv"))
            st = d.stat()
            fps.append(Fingerprint(f"iqqz3:dir:{d.name}", str(d), _dir_digest(files),
                                   sum(f.stat().st_size for f in files), st.st_mtime,
                                   f"日期目录（{len(files)} 个 CSV）"))
        # C1：zip 与同名目录内容相同，只登记 sha256 供审计，不读内容
        for z in self.zip_files():
            st = z.stat()
            fps.append(Fingerprint(f"iqqz3:zip:{z.stem}", str(z), sha256_file(z),
                                   st.st_size, st.st_mtime, "同名目录的重复副本（不读）"))
        return fps

    # ---- 事件 --------------------------------------------------------

    def iter_events(self, report: SourceReport) -> Iterator[Event]:
        days = self.day_dirs()
        if not days:
            raise FileNotFoundError(f"{self._require_root()} 下没有任何日期目录")

        # ★ 一天可能有多个快照（实测 68/395 天有 2-3 个，如 13-00-00 与 19-59-45）。
        #   原实现 `by_kind[parts[2]] = f` 会让后一个快照**整体覆盖**前一个，
        #   实测丢 Install 244 行、Notification 99 行（用户指出「缺少很多」时发现的）。
        #   改为按 kind 收集**全部**快照路径，逐份读取；同一事件用 event_id 去重。
        batches: list[tuple[Path, dict[str, list[Path]]]] = []
        for day in days:
            by_kind: dict[str, list[Path]] = {}
            for f in sorted(day.glob("*.csv")):
                parts = f.name.split("_")
                if f.name.startswith("AUM_V4_") and len(parts) >= 3:
                    by_kind.setdefault(parts[2], []).append(f)
            batches.append((day, by_kind))

        # C11：先扫 App CSV 建立 package → 显示名 映射，供所有 CSV 归一
        app_map: dict[str, str] = {}
        for _day, by_kind in batches:
            for app_csv in by_kind.get("App") or []:
                header, data = _read_csv(app_csv)
                if not _check_header("App", app_csv, header, report):
                    continue
                report.rows_read += len(data)
                report.drop("app_csv_mapping_only", len(data))
                for row in data:
                    if len(row) < 2:
                        report.drop("short_row")
                        continue
                    name, pkg = row[0].strip(), row[1].strip()
                    if name and pkg:
                        app_map.setdefault(pkg, name)

        seen: set[str] = set()
        out: list[Event] = []
        zero_duration = 0

        def emit(ev: Event) -> None:
            if ev.event_id in seen:
                report.duplicated += 1
                return
            seen.add(ev.event_id)
            out.append(ev)

        for day, by_kind in batches:
            day_ms = _day_of(day.name)
            for kind, paths in sorted(by_kind.items()):
                if kind == "App":
                    continue                       # 已在映射轮计入
                for path in paths:                  # ★ 逐个快照读，不再只读最后一个
                    header, data = _read_csv(path)
                    if kind in SKIP_KINDS:
                        report.rows_read += len(data)
                        report.drop(f"skip_{kind.lower()}", len(data))
                        continue
                    handler = getattr(self, f"_kind_{kind.lower()}", None)
                    if handler is None:
                        report.rows_read += len(data)
                        report.drop(f"unknown_csv_{kind.lower()}", len(data))
                        continue
                    if not _check_header(kind, path, header, report):
                        report.rows_read += len(data)
                        report.drop(f"header_mismatch_{kind.lower()}", len(data))
                        continue
                    zero_duration += handler(day.name, day_ms, data, app_map, report, emit)

        if zero_duration:
            report.notes.append(
                f"{zero_duration} 条 Activity 记录原始时长为 0（通知发布/屏幕开关的瞬时点），"
                f"保留为 end=start 的瞬时事件，未丢弃")

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

    # ---- 各类 CSV（返回零时长条数，供 note 汇总）------------------------

    def _kind_activity(self, day_name, day_ms, rows, app_map, report, emit) -> int:
        zero = 0
        for row in rows:
            report.rows_read += 1
            if len(row) < MIN_COLS["Activity"]:
                report.drop("short_row")
                continue
            name, date, time, duration = (c.strip() for c in row[:4])
            try:
                start = local_to_utc_ms(parse_flexible(f"{date} {time}"))
            except ValueError:
                report.drop("bad_timestamp")
                continue
            secs = parse_hms_to_seconds(duration)
            if secs == 0:
                zero += 1
            emit(Event(device=self.device, source=self.name, kind="app",
                       start_utc_ms=start, end_utc_ms=start + secs * 1000,
                       app=app_map.get(name, name), app_id=name,
                       title=app_map.get(name, name),
                       payload={"granularity": "activity", "day_dir": day_name,
                                "raw_app": name, "raw_duration": duration}))
        return zero

    def _kind_usage(self, day_name, day_ms, rows, app_map, report, emit) -> int:
        for idx, row in enumerate(rows):
            report.rows_read += 1
            if len(row) < MIN_COLS["Usage"]:
                report.drop("short_row")
                continue
            name, usage, count = (c.strip() for c in row[:3])
            if not name:
                report.drop("missing_app_name")
                continue
            secs = parse_hms_to_seconds(usage)
            if secs <= 0:
                report.drop("zero_duration")
                continue
            # 契约 §2.3：Usage 无开始时间 → 当天 00:00 + 序号错开（秒级，保证主键稳定）
            start = day_ms + idx * 1000
            try:
                count_n = int(count)
            except ValueError:
                count_n = None
            app = app_map.get(name, name)
            emit(Event(device=self.device, source=self.name, kind="app",
                       start_utc_ms=start, end_utc_ms=start + secs * 1000,
                       app=app, app_id=name, title=app,
                       payload={"granularity": "daily", "day_dir": day_name,
                                "access_count": count_n, "raw_app": name,
                                "raw_usage": usage}))
        return 0

    def _kind_battery(self, day_name, day_ms, rows, app_map, report, emit) -> int:
        for row in rows:
            report.rows_read += 1
            if len(row) < MIN_COLS["Battery"]:
                report.drop("short_row")
                continue
            date, time, pct, note = (c.strip() for c in row[:4])
            try:
                start = local_to_utc_ms(parse_flexible(f"{date} {time}"))
            except ValueError:
                report.drop("bad_timestamp")
                continue
            try:
                level = int(pct)
            except ValueError:
                report.drop("bad_battery_level")
                continue
            emit(Event(device=self.device, source=self.name, kind="battery",
                       start_utc_ms=start, title=note, category=note,
                       payload={"battery_pct": level, "day_dir": day_name}))
        return 0

    def _kind_notification(self, day_name, day_ms, rows, app_map, report, emit) -> int:
        for row in rows:
            report.rows_read += 1
            if len(row) < MIN_COLS["Notification"]:
                report.drop("short_row")
                continue
            name, count = row[0].strip(), row[1].strip()
            if not name:
                report.drop("missing_app_name")
                continue
            try:
                n = int(count)
            except ValueError:
                report.drop("bad_count")
                continue
            if n <= 0:
                report.drop("zero_count")
                continue
            app = app_map.get(name, name)
            # 契约 §2.3：Notification 无时间列 → start = 当天 00:00
            emit(Event(device=self.device, source=self.name, kind="notify",
                       start_utc_ms=day_ms, app=app, app_id=name, title=app,
                       payload={"count": n, "day_dir": day_name}))
        return 0

    def _kind_install(self, day_name, day_ms, rows, app_map, report, emit) -> int:
        for row in rows:
            report.rows_read += 1
            if len(row) < MIN_COLS["Install"]:
                report.drop("short_row")
                continue
            name, version, code, typ, date, time = (c.strip() for c in row[:6])
            if not name:
                report.drop("missing_app_name")
                continue
            try:
                start = local_to_utc_ms(parse_flexible(f"{date} {time}"))
            except ValueError:
                report.drop("bad_timestamp")
                continue
            app = app_map.get(name, name)
            emit(Event(device=self.device, source=self.name, kind="install",
                       start_utc_ms=start, app=app, app_id=name,
                       title=f"{typ} {version}".strip(), category=typ,
                       payload={"version": version, "version_code": code,
                                "type": typ, "day_dir": day_name}))
        return 0
