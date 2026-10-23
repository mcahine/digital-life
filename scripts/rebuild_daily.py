"""批量重生成所有日层总结（cli 的 daily 只接 --date，没有 --all）。

为什么要有 --workers：`build_daily` 单天 1.4s、753 天串行 18 分钟，而机器有 8 核却
只用了 1 核。实测 4 进程 0.73s/天（2.0×），全部零失败。瓶颈从来不是 SQL —— 那 60 倍
退化（逐片查库）2026-10-23 已修，现在是纯 CPU 的字符串处理与归并。

每个进程开自己的连接（DL-1 之外的另一条铁律：sqlite 连接不跨线程/进程共享），
并设 busy_timeout 让并发写排队而不是直接报 database is locked。

用法：
  python scripts/rebuild_daily.py                  # 串行
  python scripts/rebuild_daily.py --workers 4      # 4 进程（推荐）
  python scripts/rebuild_daily.py --limit 30 --workers 4
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from digitallife import paths  # noqa: E402
from digitallife.daily import build_daily  # noqa: E402


def _one(day: str) -> tuple[str, bool, str]:
    """重建一天并提交。返回 (日期, 是否成功, 失败原因)。必须是模块级函数才可 pickle。"""
    con = sqlite3.connect(paths.db_path(), timeout=30, check_same_thread=False)
    con.row_factory = sqlite3.Row
    try:
        con.execute("PRAGMA busy_timeout=30000")
        build_daily(con, day)
        con.commit()
        return (day, True, "")
    except Exception as exc:  # noqa: BLE001
        con.rollback()
        return (day, False, "%s: %s" % (type(exc).__name__, str(exc)[:90]))
    finally:
        con.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=1,
                    help="并发进程数；1 = 串行。实测 4 进程约 2 倍")
    args = ap.parse_args()

    con = sqlite3.connect(paths.db_path())
    con.row_factory = sqlite3.Row
    days = [r["date_key"] for r in con.execute(
        "SELECT date_key FROM daily_summary ORDER BY date_key")]
    con.close()
    if args.limit:
        days = days[: args.limit]
    print("待重生成: %d 天（并发 %d）" % (len(days), args.workers), flush=True)

    ok = fail = 0
    t0 = time.time()
    if args.workers <= 1:
        for i, d in enumerate(days, 1):
            _d, good, err = _one(d)
            if good:
                ok += 1
            else:
                fail += 1
                print("  [%d/%d] %s FAIL %s" % (i, len(days), d, err), flush=True)
            if i % 100 == 0:
                print("  ... %d/%d，耗时 %.0fs" % (i, len(days), time.time() - t0), flush=True)
    else:
        from concurrent.futures import ProcessPoolExecutor
        with ProcessPoolExecutor(max_workers=args.workers) as ex:
            for i, (d, good, err) in enumerate(ex.map(_one, days), 1):
                if good:
                    ok += 1
                else:
                    fail += 1
                    print("  [%d/%d] %s FAIL %s" % (i, len(days), d, err), flush=True)
                if i % 100 == 0:
                    print("  ... %d/%d，耗时 %.0fs" % (i, len(days), time.time() - t0), flush=True)

    el = time.time() - t0
    print("完成: 成功 %d / 失败 %d，耗时 %.1f 分钟（%.2fs/天）"
          % (ok, fail, el / 60, el / max(len(days), 1)))
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
