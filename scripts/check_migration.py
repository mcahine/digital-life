#!/usr/bin/env python
"""schema 迁移验证 —— 在真实库的副本上跑迁移，证明不破坏数据。

## 为什么要有它

`init_db()` 对旧库做 ALTER TABLE 补列 / 建表。这东西改的是**生产库**，
一旦写错就是数据事故（DL-16 / S30 的由来）。所以必须有办法**先在副本上验证**：

    备份 (Connection.backup, DL-1 禁止 cp)
      -> 在副本上跑 init_db
      -> 断言 event/slice/slice_result 计数一条不少
      -> 断言 schema_version 达标、索引仍在、integrity_check == ok

## 用户明确要求（2026-11-03）

「别给我本地留一大堆缓存垃圾」。历史上 `tmp/a2_migrate_test.py` 用固定路径
把 870 MB 的副本写到项目 tmp/，中途中断就永久残留（实测 tmp 从 13 MB 涨到 883 MB）。
本脚本三条防线：

  1. **默认写系统临时目录**（tempfile.gettempdir()），不污染项目
  2. **try/finally 保证清理**，即使中途抛异常 / Ctrl-C 也不留副本
  3. `--keep <path>` 才保留（显式要求才写盘，且给出路径供你自己管）

## 用法

    python scripts/check_migration.py              # 验证后自动清理
    python scripts/check_migration.py --keep d:/x.sqlite   # 保留副本
    python scripts/check_migration.py --quiet      # 只出结论

退出码：0 = 迁移安全；1 = 有断言不通过；2 = 环境/备份失败。
"""
from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from digitallife import paths
from digitallife.ingest.schema import SCHEMA_VERSION, current_version, init_db


def _pid_alive(pid: int) -> bool:
    """该 PID 是否还有活着的进程。跨平台、不引新依赖。"""
    if pid <= 0:
        return False
    try:
        if os.name == "nt":
            # Windows：tasklist 输出里出现该 PID 即认为活着。
            # 不用 OpenProcess 是为了避免 ctypes 依赖与权限噪声。
            out = subprocess.run(
                ["tasklist", "/FI", "PID eq %d" % pid, "/NH"],
                capture_output=True, text=True, timeout=10,
                errors="replace").stdout or ""
            return str(pid) in out
        os.kill(pid, 0)          # POSIX：只探活不发信号
        return True
    except (OSError, subprocess.SubprocessError):
        return False


def _sweep_stale(max_age_s: float = 600) -> int:
    """清理**上一次被强杀**留下的陈旧副本。

    为什么必须要有：`finally` 挡不住 `kill -9`（实测：8 秒后 SIGKILL，
    留下 617 MB 临时文件）。Linux 的 /tmp 可能开机清，但 Windows 的
    %TEMP% 不会自动清 —— 不加这层，强杀几次就是几个 GB。

    只删自己命名空间（dl-migrate-*）且超过 max_age_s 的文件，
    **不碰别人的临时文件**（S41）。
    """
    import glob
    d = tempfile.gettempdir()
    removed = 0
    now = time.time()
    me = os.getpid()
    for pat in ("dl-migrate-*.sqlite", "dl-migrate-*.sqlite-wal",
                "dl-migrate-*.sqlite-shm", "dl-migrate-*.sqlite-journal"):
        for f in glob.glob(os.path.join(d, pat)):
            try:
                # 文件名里带 PID（dl-migrate-<pid>-xxxx.sqlite）：
                # 同 PID 说明是**本进程**留下的空文件，直接删；
                # 异 PID 则看年龄，超过阈值才认为对方已死。
                name = os.path.basename(f)
                parts = name.split("-")
                owner = None
                for p in parts[2:]:
                    if p.isdigit():
                        owner = int(p)
                        break
                if owner == me or owner is None:
                    os.unlink(f)
                    removed += 1
                    continue
                # 关键判据：**属主进程还在不在**，而不是文件多老。
                # Windows 的 %TEMP% 不会自动清；只看年龄会让强杀残留白占
                # 一小时（实测 870 MB x N）。进程已死就立刻可删。
                if not _pid_alive(owner):
                    os.unlink(f)
                    removed += 1
                    continue
                # 进程还活着：可能正在写，只在明显陈旧时才动
                if now - os.path.getmtime(f) < max_age_s:
                    continue
                os.unlink(f)
                removed += 1
            except OSError:
                pass
    return removed


def _rm(target: Path) -> None:
    """删掉 sqlite 的主文件与 -wal/-shm 三个伴生文件。"""
    for suffix in ("", "-wal", "-shm", "-journal"):
        f = Path(str(target) + suffix)
        try:
            if f.exists():
                f.unlink()
        except OSError as exc:
            print("  [warn] 清理失败 %s: %s" % (f.name, exc), file=sys.stderr)


def snapshot(con: sqlite3.Connection) -> dict:
    """采集迁移前真值（只读）。"""
    return {
        "ver": current_version(con),
        "event": con.execute("SELECT COUNT(*) FROM event").fetchone()[0],
        "slice": con.execute("SELECT COUNT(*) FROM slice").fetchone()[0],
        "slice_result": con.execute("SELECT COUNT(*) FROM slice_result").fetchone()[0],
        "tables": sorted(r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")),
    }


def verify(src_path: Path, dst_path: Path, quiet: bool = False) -> int:
    """在 dst_path 上跑迁移并验证。**调用方负责清理 dst_path。**"""
    print("真库: %s (%.1f MB)" % (src_path, src_path.stat().st_size / 1048576))
    print("副本: %s" % dst_path)

    # 只为读，真库绝不可写（用 mode=ro 从根上禁止）
    src = sqlite3.connect("file:%s?mode=ro" % str(src_path).replace(os.sep, "/"),
                          uri=True, timeout=30)
    src.row_factory = sqlite3.Row
    before = snapshot(src)
    if not quiet:
        print("迁移前: ver=%s event=%d slice=%d slice_result=%d"
              % (before["ver"], before["event"], before["slice"],
                 before["slice_result"]))

    # DL-1：必须用 backup()，禁止 cp（cp 会拿到撕裂的 WAL 状态）
    t0 = time.time()
    dst = sqlite3.connect(str(dst_path))
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    size_mb = dst_path.stat().st_size / 1048576
    if not quiet:
        print("备份完成: %.1f MB / %.1fs" % (size_mb, time.time() - t0))

    # 在副本上跑迁移
    con = sqlite3.connect(str(dst_path), timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout=30000")
    try:
        init_db(con)
        con.commit()

        after_ver = current_version(con)
        n_ev = con.execute("SELECT COUNT(*) FROM event").fetchone()[0]
        n_sl = con.execute("SELECT COUNT(*) FROM slice").fetchone()[0]
        n_sr = con.execute("SELECT COUNT(*) FROM slice_result").fetchone()[0]
        has_lt = bool(con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='link_title'"
        ).fetchone())
        idx = sorted(r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'ix_%'"))
        integrity = con.execute("PRAGMA integrity_check").fetchone()[0]
        lt_cols = [r[1] for r in con.execute("PRAGMA table_info(link_title)")] if has_lt else []
    finally:
        con.close()

    # 断言（计数一条不能少 —— 迁移只该改结构，不该动数据）
    fails = []

    def ck(name, cond, detail=""):
        mark = "PASS" if cond else "FAIL"
        print("  %-4s %s%s" % (mark, name, ("  " + str(detail)) if not cond else ""))
        if not cond:
            fails.append(name)

    ck("schema_version 达到 %d" % SCHEMA_VERSION, after_ver >= SCHEMA_VERSION,
       "实测 %s" % after_ver)
    ck("event 计数不变", n_ev == before["event"],
       "%d -> %d" % (before["event"], n_ev))
    ck("slice 计数不变", n_sl == before["slice"],
       "%d -> %d" % (before["slice"], n_sl))
    ck("slice_result 计数不变", n_sr == before["slice_result"],
       "%d -> %d" % (before["slice_result"], n_sr))
    ck("link_title 表存在", has_lt)
    ck("integrity_check == ok", integrity == "ok", integrity)
    ck("索引 ix_* 仍在", len(idx) > 0, "找到 %d 个" % len(idx))
    if has_lt:
        ck("link_title 有新列 failure_kind", "failure_kind" in lt_cols, lt_cols)

    print()
    if fails:
        print("结论: 迁移不安全，%d 项不通过 -> %s" % (len(fails), ", ".join(fails)))
        return 1
    print("结论: 迁移安全（计数守恒 / 结构达标 / 完整性 ok）")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", metavar="PATH",
                    help="保留副本到该路径（默认写系统临时目录并在结束后删除）")
    ap.add_argument("--quiet", action="store_true", help="只输出断言结果")
    args = ap.parse_args()

    src = Path(str(paths.db_path()))
    if not src.exists():
        print("真库不存在: %s" % src, file=sys.stderr)
        return 2

    # 先清上一次强杀留下的残骸（本次跑之前，避免越积越多）
    n_swept = _sweep_stale()
    if n_swept:
        print("[note] 清理了上次中断遗留的 %d 个临时文件" % n_swept)

    cleanup = False
    if args.keep:
        dst = Path(args.keep)
        dst.parent.mkdir(parents=True, exist_ok=True)
        _rm(dst)
    else:
        # 系统临时目录：不在项目内，中断了也不污染工作区
        fd, tmp_name = tempfile.mkstemp(
            prefix="dl-migrate-%d-" % os.getpid(), suffix=".sqlite")
        os.close(fd)
        dst = Path(tmp_name)
        cleanup = True
        _rm(dst)          # mkstemp 建了空文件，backup 前先删掉

    try:
        return verify(src, dst, quiet=args.quiet)
    except KeyboardInterrupt:
        print("\n中断", file=sys.stderr)
        return 2
    except Exception as exc:
        print("验证失败: %s: %s" % (type(exc).__name__, exc), file=sys.stderr)
        return 2
    finally:
        if cleanup:
            _rm(dst)
            print("已清理副本: %s" % dst)
        else:
            print("副本保留在: %s（%.1f MB，用 --keep 指定的，请自行清理）"
                  % (dst, dst.stat().st_size / 1048576 if dst.exists() else 0))


if __name__ == "__main__":
    raise SystemExit(main())