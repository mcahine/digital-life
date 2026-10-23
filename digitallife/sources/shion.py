"""shion（浏览器历史桌面端）备份适配器。

数据形态（实测 2026-10-21，00-Inbox/shion/）：
  shion-2024-12-27.zip   2.9MB  data.db 2.9MB   → 旧版，**不导**（新版是它的超集，C2）
  shion-2025-03-11.zip   7.4MB  data.db 7.4MB   → 只导这一版

data.db 表行数（实测）：history 28805 / domain 840 / dimension 5 / overview 3；
activity / moment / note / box / label / link / plan / program / remark 全为 0 行 → 跳过。
last_visited 已是 UTC epoch **毫秒**，不需要 tz 换算。
"""
from __future__ import annotations

import os
import sqlite3
import zipfile
from pathlib import Path
from typing import Iterator

from .. import paths
from ..clean import SourceReport, is_empty_event, normalize_event
from ..model import Event
from .base import Fingerprint, SourceAdapter, sha256_file

ROOT_NAME = "shion"
NEWEST_ZIP = "shion-2025-03-11.zip"
DB_MEMBER = "data.db"
_CHUNK = 1 << 22

#: 契约 §2.5：这些表实测 0 行；用 COUNT(*) 判空后跳过（未来非空也能容忍）
OPTIONAL_TABLES = ("activity", "moment", "note", "box", "label", "link", "plan",
                   "program", "remark", "overview", "dimension", "dimension_label",
                   "dimension_program")


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


class ShionSource(SourceAdapter):
    name = "shion"
    device = "pc-main"      # 历史浏览器记录，归属那台机器

    def __init__(self, root: Path | None = None) -> None:
        super().__init__(root)
        override = os.environ.get("DL_SHION_ZIP")
        if override:
            self.root = Path(override)
        elif self.root is None:
            self.root = paths.INBOX / ROOT_NAME / NEWEST_ZIP
        self._work = paths.work_dir() / "shion"

    # ---- 定位与解包 --------------------------------------------------

    def _zip_paths(self) -> list[Path]:
        p = Path(self.root) if self.root else None
        if p is None:
            return []
        if p.is_file():
            return [p]
        if p.is_dir():
            return sorted(p.glob("*.zip"))
        return []

    def _require_zip(self) -> Path:
        """只解新版（C2）。找不到新版但存在其它 zip 时明确报错，不静默降级。"""
        zips = self._zip_paths()
        for z in zips:
            if z.name == NEWEST_ZIP:
                return z
        if zips:
            raise FileNotFoundError(
                f"未找到新版 {NEWEST_ZIP}（目录里只有：{[z.name for z in zips]}）。"
                f"旧版是它的子集，不导；把 {NEWEST_ZIP} 放到 {paths.INBOX / ROOT_NAME}/，"
                f"或用环境变量 DL_SHION_ZIP 指向实际 zip。"
            )
        raise FileNotFoundError(
            f"shion 备份未找到（{self.root}）。"
            f"把 {NEWEST_ZIP} 放到 {paths.INBOX / ROOT_NAME}/，或用环境变量 DL_SHION_ZIP 指向实际 zip。"
        )

    def extract_db(self) -> Path:
        zip_path = self._require_zip()
        self._work.mkdir(parents=True, exist_ok=True)
        return _extract_member(zip_path, DB_MEMBER, self._work / DB_MEMBER)

    def fingerprint(self) -> list[Fingerprint]:
        zips = self._zip_paths()
        if not zips:
            return []
        newest = self._require_zip()
        fps: list[Fingerprint] = []
        for z in zips:
            st = z.stat()
            note = "只导这一版（C2）" if z.name == newest.name else "superseded（旧版，不导）"
            fps.append(Fingerprint(f"shion:{z.stem}", str(z), sha256_file(z),
                                   st.st_size, st.st_mtime, note))
        db = self.extract_db()
        s = db.stat()
        fps.append(Fingerprint("shion:db", str(db), sha256_file(db),
                               s.st_size, s.st_mtime, "history / domain"))
        return fps

    # ---- 事件 --------------------------------------------------------

    def iter_events(self, report: SourceReport) -> Iterator[Event]:
        db = self.extract_db()
        con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
        try:
            tables = {r[0] for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            for t in OPTIONAL_TABLES:
                if t in tables:
                    n = con.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
                    if n:
                        report.notes.append(f"表 {t} 非空（{n} 行），契约未定义映射，未导入")
            domains = {}
            if "domain" in tables:
                domains = {did: name for did, name in
                           con.execute("SELECT id, name FROM domain")}
            history = con.execute(
                "SELECT title, url, last_visited, domain_id, deleted_at FROM history "
                "ORDER BY last_visited").fetchall()
        finally:
            con.close()

        for title, url, last_visited, domain_id, deleted_at in history:
            report.rows_read += 1
            if deleted_at:
                report.drop("soft_deleted")
                continue
            if not (url or "").strip():
                report.drop("missing_url")
                continue
            if last_visited is None:
                report.drop("missing_last_visited")
                continue
            domain = domains.get(domain_id)
            ev = Event(device=self.device, source=self.name, kind="web",
                       start_utc_ms=int(last_visited),
                       title=(title or "").strip() or url,
                       url=url.strip(), app=domain, app_id=domain,
                       payload={"domain": domain} if domain else {})
            ev = normalize_event(ev)
            if is_empty_event(ev):
                report.drop("empty_event")
                continue
            report.keep()
            if report.window_start_utc_ms is None or ev.start_utc_ms < report.window_start_utc_ms:
                report.window_start_utc_ms = ev.start_utc_ms
            hi = ev.end_utc_ms or ev.start_utc_ms
            if report.window_end_utc_ms is None or hi > report.window_end_utc_ms:
                report.window_end_utc_ms = hi
            yield ev
