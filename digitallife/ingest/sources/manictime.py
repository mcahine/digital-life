"""ManicTime 备份（zip）适配器。

数据形态（实测 2026-10-21 备份）：
  ManicTimeBackup_*.zip
    ├── ManicTimeReports.db   385MB  Ar_Activity 四条时间线（ReportId 1=Tags/2=ComputerUsage/3=Applications/4=Documents）
    ├── ManicTimeCore.db      110MB  只有 Cli_EcId_* 变更日志（3 列，无正文）→ 只取 Document 表元数据
    └── Screenshots/YYYY-MM-DD/YYYY-MM-DD_HH-MM-SS_<tzoffset>_*.webp   55527 张

时间口径：一律用 *UtcTime 列（字符串 "YYYY-MM-DD HH:MM:SS"，UTC），不走 tz.local_to_utc_ms。
截图文件名里是**本地时间**，按 LOCAL_TZ 换算。
"""
from __future__ import annotations

import bisect
import os
import sqlite3
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from ... import paths
from ..clean import SourceReport, is_empty_event, merge_adjacent, normalize_event
from ..model import Event
from ..tz import local_to_utc_ms, parse_local
from .base import Fingerprint, SourceAdapter, sha256_file

ZIP_NAME = "ManicTimeBackup_2026-10-21.zip"
REPORTS_MEMBER = "ManicTimeReports.db"
CORE_MEMBER = "ManicTimeCore.db"
SHOTS_PREFIX = "Screenshots/"

#: 截图归属窗口：前后各 120 秒内最近的 app 事件（契约 §2.1）
NEAR_MS = 120_000

_CHUNK = 1 << 22


def _extract_member(zip_path: Path, member: str, dest: Path) -> Path:
    """流式解包单个成员；目标已存在且大小一致即复用（幂等，不重复解包）。"""
    with zipfile.ZipFile(zip_path) as z:
        info = z.getinfo(member)
        if dest.exists() and dest.stat().st_size == info.file_size:
            return dest
        dest.parent.mkdir(parents=True, exist_ok=True)
        part = dest.with_name(dest.name + ".part")
        with z.open(member) as src, open(part, "wb") as out:
            while True:
                buf = src.read(_CHUNK)
                if not buf:
                    break
                out.write(buf)
        part.replace(dest)
    return dest


def _utc_ms(value: str) -> int:
    """'2024-12-31 08:59:51'（UTC）→ epoch ms。"""
    text = (value or "").strip()
    if not text:
        raise ValueError("empty ManicTime utc timestamp")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        dt = datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def _shot_utc_ms(rel_path: str) -> int | None:
    """Screenshots/2026-09-17/2026-09-17_00-00-43_08-00_...webp → 本地串 → UTC ms。"""
    stem = rel_path.rsplit("/", 1)[-1]
    parts = stem.split("_")
    if len(parts) < 2:
        return None
    try:
        return local_to_utc_ms(parse_local(parts[0] + " " + parts[1].replace("-", ":")))
    except ValueError:
        return None


class ManicTimeSource(SourceAdapter):
    name = "manictime"
    device = "pc-main"      # 多电脑时用 DL_DEVICE_MANICTIME 覆盖到 pc-2 ...

    def __init__(self, root: Path | None = None) -> None:
        super().__init__(root)
        override = os.environ.get("DL_MANICTIME_ZIP")
        if override:
            self.root = Path(override)
        elif self.root is None:
            self.root = paths.INBOX / ZIP_NAME
        self._work = paths.work_dir() / "manictime"

    # ---- 定位与解包 --------------------------------------------------

    def _zip_path(self) -> Path | None:
        p = Path(self.root) if self.root else None
        return p if p is not None and p.is_file() else None

    def _require_zip(self) -> Path:
        p = self._zip_path()
        if p is None:
            raise FileNotFoundError(
                f"ManicTime 备份 zip 未找到（{self.root}）。"
                f"把 {ZIP_NAME} 放到 {paths.INBOX}，或用环境变量 DL_MANICTIME_ZIP 指向实际路径。"
            )
        return p

    def extract_db(self) -> tuple[Path, Path]:
        """流式解包 Reports/Core 两个库到 work_dir；已存在则复用。"""
        zip_path = self._require_zip()
        self._work.mkdir(parents=True, exist_ok=True)
        reports = _extract_member(zip_path, REPORTS_MEMBER, self._work / REPORTS_MEMBER)
        core = _extract_member(zip_path, CORE_MEMBER, self._work / CORE_MEMBER)
        return reports, core

    def fingerprint(self) -> list[Fingerprint]:
        zip_path = self._zip_path()
        if zip_path is None:
            return []
        st = zip_path.stat()
        out = [Fingerprint("manictime:zip", str(zip_path), sha256_file(zip_path),
                           st.st_size, st.st_mtime, "ManicTime 备份 zip（含 55527 张 webp）")]
        reports, core = self.extract_db()
        for source_id, path, note in (
            ("manictime:reports", reports, "Ar_Activity 四条时间线"),
            ("manictime:core", core, "仅取 Document 表元数据（Cli_EcId_* 无正文）"),
        ):
            s = path.stat()
            out.append(Fingerprint(source_id, str(path), sha256_file(path),
                                   s.st_size, s.st_mtime, note))
        return out

    # ---- 事件 --------------------------------------------------------

    def iter_events(self, report: SourceReport) -> Iterator[Event]:
        reports_db, _core_db = self.extract_db()
        con = sqlite3.connect(f"file:{reports_db.as_posix()}?mode=ro", uri=True)
        try:
            groups = {
                cid: (name, key)
                for cid, name, key in con.execute(
                    "SELECT CommonId, Name, [Key] FROM Ar_CommonGroup")
            }
            total_rows = con.execute("SELECT COUNT(*) FROM Ar_Activity").fetchone()[0]
            tags_rows = con.execute(
                "SELECT COUNT(*) FROM Ar_Activity WHERE ReportId=1").fetchone()[0]
            report.rows_read += total_rows
            report.drop("tags_report", tags_rows)

            events: list[Event] = []
            app_spans: list[tuple[int, int, str]] = []
            rows = con.execute(
                "SELECT ReportId, Name, CommonGroupId, StartUtcTime, EndUtcTime "
                "FROM Ar_Activity WHERE ReportId IN (2,3,4) ORDER BY ReportId, StartUtcTime")
            for report_id, name, group_id, start_utc, end_utc in rows:
                try:
                    start = _utc_ms(start_utc)
                    end = _utc_ms(end_utc)
                except ValueError:
                    report.drop("bad_timestamp")
                    continue
                if report_id == 2:                       # ComputerUsage: Active/Away/Session lock
                    events.append(Event(device=self.device, source=self.name, kind="power",
                                        start_utc_ms=start, end_utc_ms=end,
                                        title=name, category=name))
                elif report_id == 3:                     # Applications（带窗口标题）
                    gname, gkey = groups.get(group_id, (None, None))
                    ev = Event(device=self.device, source=self.name, kind="app",
                               start_utc_ms=start, end_utc_ms=end,
                               app=gname or name, app_id=gkey, title=name)
                    events.append(ev)
                    if gname:
                        app_spans.append((start, end, gname))
                else:                                    # Documents: URL / 文件路径
                    text = name or ""
                    low = text.lower()
                    if low.startswith("http://") or low.startswith("https://"):
                        gname, _gkey = groups.get(group_id, (None, None))
                        events.append(Event(device=self.device, source=self.name, kind="web",
                                            start_utc_ms=start, end_utc_ms=end,
                                            app=gname, title=text, url=text,
                                            payload={"domain": gname} if gname else {}))
                    else:
                        events.append(Event(device=self.device, source=self.name, kind="app",
                                            start_utc_ms=start, end_utc_ms=end,
                                            title=text, path=text))
        finally:
            con.close()

        # C4：只对活动事件做相邻合并；screen 是瞬时快照，混进去会把合并流切断
        activity = [e for e in (normalize_event(e) for e in events) if not is_empty_event(e)]
        merged, merged_count, short_dropped = merge_adjacent(activity)
        report.merged += merged_count
        report.drop("too_short", short_dropped)

        # 截图（zip 内相对路径，不落盘）
        zip_path = self._require_zip()
        app_spans.sort()
        span_starts = [s for s, _e, _a in app_spans]
        shots = 0
        screens: list[Event] = []
        with zipfile.ZipFile(zip_path) as z:
            for info in z.infolist():
                rel = info.filename
                if not rel.startswith(SHOTS_PREFIX) or rel.endswith("/"):
                    continue
                shots += 1
                start = _shot_utc_ms(rel)
                if start is None:
                    report.drop("bad_shot_name")
                    continue
                screens.append(Event(device=self.device, source=self.name, kind="screen",
                                     start_utc_ms=start, path=rel,
                                     app=self._nearest_app(span_starts, app_spans, start)))
        report.rows_read += shots

        all_events = merged + screens
        if all_events:
            report.window_start_utc_ms = min(e.start_utc_ms for e in all_events)
            report.window_end_utc_ms = max(e.end_utc_ms or e.start_utc_ms for e in all_events)

        for ev in sorted(all_events, key=lambda e: e.start_utc_ms):
            report.keep()
            yield ev

    @staticmethod
    def _nearest_app(starts: list[int], spans: list[tuple[int, int, str]], ts: int) -> str | None:
        """±120s 内最近的 app 事件（优先命中区间包含，其次按起点距离）。"""
        if not starts:
            return None
        i = bisect.bisect_left(starts, ts)
        best: tuple[int, str] | None = None
        for j in (i - 1, i):
            if 0 <= j < len(spans):
                s, e, app = spans[j]
                if s <= ts <= e:
                    return app
                dist = abs(s - ts)
                if dist <= NEAR_MS and (best is None or dist < best[0]):
                    best = (dist, app)
        return best[1] if best else None
