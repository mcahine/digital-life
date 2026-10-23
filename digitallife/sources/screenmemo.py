"""ScreenMemo（手机截屏记录）备份适配器。

数据形态（实测 2026-08-07 备份）：
  screen_memo_database_backup_*.zip
    ├── output/databases/screenshot_memo.db            480MB 主库：app_registry / segments /
    │                                                         segment_samples / segment_results /
    │                                                         daily_summaries / morning_insights / …
    └── output/databases/shards/<app_pkg>/<year>/*.db   42 个分片：16 个含 shots_YYYYMM，26 个
                                                        4KB settings.db（无 shots 表）

契约（interfaces.md §2.2）：每条 shots_* 行 → kind=screen；segments 仅在 app_packages 非空时产
kind=app；segment_samples 不单独产事件，只用于给 shots 反查 segment_id。
"""
from __future__ import annotations

import json
import os
import sqlite3
import zipfile
from pathlib import Path
from typing import Iterator

from .. import paths
from ..clean import SourceReport, is_empty_event, normalize_event
from ..model import Event
from .base import Fingerprint, SourceAdapter, sha256_file

ZIP_NAME = "screen_memo_database_backup_2026-08-07T22-07-00-855864.zip"
MAIN_MEMBER = "output/databases/screenshot_memo.db"
SHARD_PREFIX = "output/databases/shards/"
_CHUNK = 1 << 22

#: 契约 C7：这些表与「用过什么屏幕」无关，一律跳过
#: 截图采样间隔 ≥60s（segments.sample_interval_sec 实测 60）。
#: 合成 app 事件时，相邻两帧间隔在 2 倍采样间隔内就视为同一次使用。
SAMPLE_GAP_MS = 120_000

SKIP_TABLES = frozenset({
    "day_stats", "day_stats_meta", "totals", "app_stats", "search_docs", "search_index_state",
    "favorites", "shard_registry", "fts_registry", "android_metadata", "sqlite_sequence",
    "screenshot_path_lookup", "weekly_summaries",
})


def _extract_member(zip_path: Path, member: str, dest: Path) -> Path:
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


def _app_pkg_of(member: str) -> str:
    """output/databases/shards/<app_pkg>/… → <app_pkg>（目录名即应用包名，契约 §2.2）。"""
    rest = member[len(SHARD_PREFIX):]
    return rest.split("/", 1)[0]


class ScreenMemoSource(SourceAdapter):
    name = "screenmemo"
    # ★ 用户 2026-10-22 澄清：iQQQ Neo11 与 iQQQZ3 是**两台不同的手机**。
    # ScreenMemo 只装在 iQQQ Neo11 上，IQQZ3 在另一台 —— 两者不能合并到同一 device，
    # 否则时段页的设备 tab 会把两台机器的记录混成一份。
    device = "phone-2"      # 多手机时用 DL_DEVICE_SCREENMEMO 覆盖

    def __init__(self, root: Path | None = None) -> None:
        super().__init__(root)
        override = os.environ.get("DL_SCREENMEMO_ZIP")
        if override:
            self.root = Path(override)
        elif self.root is None:
            self.root = paths.INBOX / ZIP_NAME
        self._work = paths.work_dir() / "screenmemo"

    # ---- 定位与解包 --------------------------------------------------

    def _zip_path(self) -> Path | None:
        p = Path(self.root) if self.root else None
        return p if p is not None and p.is_file() else None

    def _require_zip(self) -> Path:
        p = self._zip_path()
        if p is None:
            raise FileNotFoundError(
                f"ScreenMemo 备份 zip 未找到（{self.root}）。"
                f"把 {ZIP_NAME} 放到 {paths.INBOX}，或用环境变量 DL_SCREENMEMO_ZIP 指向实际路径。"
            )
        return p

    def extract_db(self) -> Path:
        zip_path = self._require_zip()
        self._work.mkdir(parents=True, exist_ok=True)
        return _extract_member(zip_path, MAIN_MEMBER, self._work / "screenshot_memo.db")

    def shard_dbs(self) -> list[tuple[str, Path]]:
        """解出全部分片库并返回 [(app_pkg, path)]，按包名排序。"""
        zip_path = self._require_zip()
        out: list[tuple[str, Path]] = []
        with zipfile.ZipFile(zip_path) as z:
            members = sorted(n for n in z.namelist()
                             if n.startswith(SHARD_PREFIX) and n.endswith(".db"))
            for member in members:
                rel = member[len(SHARD_PREFIX):]
                dest = self._work / "shards" / rel
                out.append((_app_pkg_of(member), _extract_member(zip_path, member, dest)))
        return out

    def fingerprint(self) -> list[Fingerprint]:
        zip_path = self._zip_path()
        if zip_path is None:
            return []
        st = zip_path.stat()
        fps = [Fingerprint("screenmemo:zip", str(zip_path), sha256_file(zip_path),
                           st.st_size, st.st_mtime, "ScreenMemo 备份 zip（主库 + 16 个分片库）")]
        main = self.extract_db()
        s = main.stat()
        fps.append(Fingerprint("screenmemo:main", str(main), sha256_file(main),
                               s.st_size, s.st_mtime, "app_registry / segments / segment_samples"))
        return fps

    # ---- 事件 --------------------------------------------------------

    def iter_events(self, report: SourceReport) -> Iterator[Event]:
        main = self.extract_db()
        con = sqlite3.connect(f"file:{main.as_posix()}?mode=ro", uri=True)
        try:
            names = {r[0] for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            app_names = {pkg: name for pkg, name in con.execute(
                "SELECT app_package_name, app_name FROM app_registry")}

            # 分片目录名是包名的下划线变体（com.biji.getnotes → com_biji_getnotes），
            # 直接拿目录名查 app_registry 会全部落空，必须先建「下划线键 → 真包名」映射。
            pkg_by_key = {pkg.replace(".", "_"): pkg for pkg in app_names}

            # segment_samples 不产事件，只做 file_path → segment_id / app 反查（C6）
            sample_segment: dict[str, int] = {}
            sample_app: dict[str, str] = {}
            if "segment_samples" in names:
                for path, seg_id, pkg, app_name in con.execute(
                        "SELECT file_path, segment_id, app_package_name, app_name "
                        "FROM segment_samples"):
                    sample_segment[path] = seg_id
                    if app_name:
                        sample_app[path] = app_name

            # 文件路径自带真包名：/output/screen/com.biji.getnotes/2026-07/01/xxx.webp
            def _pkg_from_path(path: str) -> str | None:
                parts = path.split("/")
                return parts[parts.index("screen") + 1] if "screen" in parts[:6] else None

            # ★ 上游 AI 已经给绝大多数截图写好了中文画面描述（ai_image_meta），
            # 这是手机侧**唯一可用的观察来源**：截图本身是安卓内部路径，本机没有文件。
            # 实测 26,742 行中 99% 有 description，覆盖 2026-05-28 → 2026-08-07。
            ai_meta: dict[str, dict] = {}
            if "ai_image_meta" in names:
                cols = {r[1] for r in con.execute("PRAGMA table_info(ai_image_meta)")}
                want = ["file_path", "description"]
                if "tags_json" in cols:
                    want.append("tags_json")
                for row in con.execute("SELECT %s FROM ai_image_meta" % ", ".join(want)):
                    rec = dict(zip(want, row))
                    fp = (rec.get("file_path") or "").strip()
                    desc = (rec.get("description") or "").strip()
                    if not fp or not desc:
                        continue
                    item = {"description": desc}
                    raw_tags = rec.get("tags_json")
                    if raw_tags:
                        try:
                            parsed = json.loads(raw_tags)
                            if isinstance(parsed, list):
                                item["tags"] = [str(x) for x in parsed if str(x).strip()]
                        except (TypeError, ValueError):
                            pass
                    ai_meta[fp] = item

            segments = []
            if "segments" in names:
                n_seg = con.execute("SELECT COUNT(*) FROM segments").fetchone()[0]
                report.rows_read += n_seg
                for seg_id, start, end, pkgs in con.execute(
                        "SELECT id, start_time, end_time, app_packages FROM segments"):
                    if not (pkgs or "").strip():
                        report.drop("segment_without_app_packages")
                        continue
                    segments.append((seg_id, start, end, pkgs))
        finally:
            con.close()

        # segments → kind=app（契约 §2.2：仅当 app_packages 非空才产出，title 用 app 名）
        seg_events: list[Event] = []
        for seg_id, start, end, pkgs in segments:
            for pkg in [p.strip() for p in (pkgs or "").split(",") if p.strip()]:
                app_name = app_names.get(pkg)
                if not app_name:
                    report.drop("segment_unknown_app_package")
                    continue
                seg_events.append(Event(
                    device=self.device, source=self.name, kind="app",
                    start_utc_ms=int(start), end_utc_ms=int(end),
                    app=app_name, app_id=pkg, title=app_name,
                    payload={"segment_id": seg_id}))

        screens: list[Event] = []
        seen_paths: set[str] = set()
        for app_pkg, db_path in self.shard_dbs():
            con = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
            try:
                tables = [r[0] for r in con.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'shots%'")]
                shot_tables = [t for t in tables if t not in SKIP_TABLES and "_fts" not in t]
                for table in sorted(shot_tables):
                    cols = {r[1] for r in con.execute(f'PRAGMA table_info("{table}")')}
                    want = "id, file_path, capture_time"
                    if "ocr_text" in cols:
                        want += ", ocr_text"
                    if "page_url" in cols:
                        want += ", page_url"
                    if "is_deleted" in cols:
                        want += ", is_deleted"
                    for row in con.execute(f'SELECT {want} FROM "{table}"'):
                        report.rows_read += 1
                        rec = dict(zip([c.strip() for c in want.split(",")], row))
                        if rec.get("is_deleted"):
                            report.drop("soft_deleted")
                            continue
                        path = (rec.get("file_path") or "").strip()
                        if not path:
                            report.drop("missing_file_path")
                            continue
                        if path in seen_paths:
                            report.drop("duplicate_file_path")
                            continue
                        seen_paths.add(path)
                        payload: dict = {"ocr": bool((rec.get("ocr_text") or "").strip())}
                        ocr = (rec.get("ocr_text") or "").strip()
                        if ocr:
                            payload["ocr_text"] = ocr
                        if rec.get("page_url"):
                            payload["page_url"] = rec["page_url"]
                        seg_id = sample_segment.get(path)
                        if seg_id is not None:
                            payload["segment_id"] = seg_id
                        payload["shard"] = app_pkg
                        meta_item = ai_meta.get(path)
                        if meta_item:
                            payload["description"] = meta_item["description"]
                            if meta_item.get("tags"):
                                payload["tags"] = meta_item["tags"]
                        canon = pkg_by_key.get(app_pkg) or _pkg_from_path(path)
                        app_name = app_names.get(canon) if canon else None
                        if app_name is None:
                            app_name = sample_app.get(path)
                        if app_name is None:
                            report.drop("unknown_app_package")
                        screens.append(Event(
                            device=self.device, source=self.name, kind="screen",
                            start_utc_ms=int(rec["capture_time"]), path=path,
                            app=app_name, app_id=canon or app_pkg, payload=payload))
            finally:
                con.close()

        # ★ 用截图合成 kind=app 事件（2026-10-22 修复）。
        # 实测：主库 segments.app_packages **666 条全空**，所以上面那段一段也没产出过 ——
        # 结果是 phone-2 只有 screen 事件，dbview 的 apps/timeline 永远是空数组，
        # 模型看得到「画面里有什么」却看不出「在用什么应用」。
        # 而截图本身 100% 带 app 名（哔哩哔哩 79,258 / 阅读·A 4,661 / 起点读书 3,268 …），
        # 那就是最可靠的应用归属证据：把同一 app 的相邻截图连成一段 app 事件。
        #
        # 合并口径：同一 app、相邻两帧间隔 ≤ SAMPLE_GAP_MS 就连成一段。
        # ScreenMemo 的采样间隔是 60 秒（segments.sample_interval_sec 实测值），
        # 取 2 倍间隔作为容差，避免一次漏采就把同一次使用切成两段。
        screen_by_time = sorted(screens, key=lambda e: e.start_utc_ms)
        app_spans: list[list] = []      # [app, app_id, start, last_ts]
        for ev in screen_by_time:
            name = (ev.app or "").strip()
            if not name:
                continue
            ts = int(ev.start_utc_ms)
            last = app_spans[-1] if app_spans else None
            if (last is not None and last[0] == name
                    and ts - last[3] <= SAMPLE_GAP_MS):
                last[3] = ts
            else:
                app_spans.append([name, ev.app_id, ts, ts])
        screen_apps: list[Event] = []
        for name, app_id, start, last_ts in app_spans:
            # 单帧时长给一个采样间隔，两帧以上取首末跨度
            end = last_ts if last_ts > start else start + SAMPLE_GAP_MS // 2
            screen_apps.append(Event(
                device=self.device, source=self.name, kind="app",
                start_utc_ms=int(start), end_utc_ms=int(end),
                app=name, app_id=app_id or None, title=name,
                payload={"derived_from": "screen_samples", "samples": 1 if last_ts == start else 0}))
        if screen_apps:
            report.notes.append("由截图合成 %d 段应用事件" % len(screen_apps))

        all_events = seg_events + screen_apps + screens
        if all_events:
            report.window_start_utc_ms = min(e.start_utc_ms for e in all_events)
            report.window_end_utc_ms = max(e.end_utc_ms or e.start_utc_ms for e in all_events)

        for ev in sorted(all_events, key=lambda e: e.start_utc_ms):
            ev = normalize_event(ev)
            if is_empty_event(ev):
                report.drop("empty_event")
                continue
            report.keep()
            yield ev
