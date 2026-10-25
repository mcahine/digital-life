#!/usr/bin/env python
"""批量抓取网页标题，写进 tmp/.titlecache.json 供时段页使用。

为什么单独一个脚本、不放在渲染里：
  建页要快且确定。联网会把 753 页从 6 分钟变成不可预估，还可能因为
  某个站反爬而卡住整批。所以拆成两步：
    1. 本脚本联网抓标题（可以慢慢跑、可以中断、可重跑）
    2. build_slice_html.py 只读缓存（纯本地，快且稳定）

用法::

    python scripts/fetch_titles.py --limit 200      # 先试 200 条
    python scripts/fetch_titles.py                  # 抓全部缺失的
    python scripts/fetch_titles.py --workers 8      # 并发

只抓「标题只是域名」的那些 URL —— 已经有真实标题的行不需要联网。
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from digitallife import paths  # noqa: E402
from slice_pages import linktitle  # noqa: E402


def collect() -> list[tuple[str, str]]:
    """返回 [(url, 原 title)]，只含「title 是域名形态且缓存里没有」的。"""
    con = sqlite3.connect(paths.db_path())
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT url, title, COUNT(*) n FROM event "
        "WHERE kind='web' AND url IS NOT NULL AND url != '' "
        "GROUP BY url, title ORDER BY n DESC"
    ).fetchall()
    con.close()

    seen: dict[str, str] = {}
    for r in rows:
        u = str(r["url"] or "").strip()
        t = str(r["title"] or "").strip()
        if not u or u in seen:
            continue
        if not linktitle.is_hostlike(t, u):
            continue          # 已有真实标题，不必联网
        if linktitle.cached(u):
            continue          # 缓存已有
        seen[u] = t
    return list(seen.items())


def one(item: tuple[str, str]) -> tuple[str, str, bool]:
    url, _old = item
    t = linktitle.fetch(url)
    if t and not linktitle.is_hostlike(t, url):
        linktitle.put(url, t)
        return url, t, True
    return url, "", False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()

    todo = collect()
    if args.limit:
        todo = todo[: args.limit]
    print("待抓标题 %d 条（并发 %d）" % (len(todo), args.workers), flush=True)
    if not todo:
        return 0

    ok = fail = 0
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        for i, f in enumerate(as_completed([ex.submit(one, it) for it in todo]), 1):
            url, title, good = f.result()
            if good:
                ok += 1
            else:
                fail += 1
            if i % 25 == 0:
                print("  %d/%d ok=%d fail=%d %.0fs" % (
                    i, len(todo), ok, fail, time.time() - t0), flush=True)
    print("完成：成功 %d / 失败 %d，耗时 %.1f 分钟"
          % (ok, fail, (time.time() - t0) / 60), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
