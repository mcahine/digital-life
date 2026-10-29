#!/usr/bin/env python
"""批量抓取网页标题 → 写进 **link_title 表**（不是磁盘缓存）。

用户第 3 条诉求（2026-10-25 原话）：
  「对于那些没有标题的链接，可以爬取链接获得标题，不是放到硬盘缓存，可以存入
   数据库当中。当然，没有爬取到域名，就原来的模样就好。」

改前：缓存落 tmp/.titlecache.json。tmp 被 .gitignore 排除、不进备份、
清一次就全丢；而建页读的正是那份文件，丢了就"标题又变回域名了"。

为什么仍然分成两步（抓取 vs 渲染）：
  建页要快且确定。联网会把 753 页从 6 分钟变成不可预估，还可能因为某个站反爬
  卡住整批。所以：
    1. 本脚本联网抓标题写库（可以慢慢跑、可以中断、可重跑）
    2. build_slice_html.py 只**读库**（纯本地，快且稳定，渲染期绝不联网）

抓不到也落行（status='failed'）：否则每次建页都会重抓同一个抓不到的地址，
而"抓不到"本身是稳定结论（域名已死 / 需要登录 / 反爬）。

用法::

    python scripts/fetch_titles.py --stat         # 只统计规模，不联网
    python scripts/fetch_titles.py --limit 200    # 先试 200 条
    python scripts/fetch_titles.py --workers 8    # 全量，8 并发
    python scripts/fetch_titles.py --retry-failed # 连之前失败的也重抓

只抓「标题只是域名」的那些 URL —— 已经有真实标题的行不需要联网。
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# T1 (2026-10-25): sys.path.insert removed -- package is installable via pip install -e .
# T1 (2026-10-25): sys.path.insert removed -- package is installable via pip install -e .

from digitallife import paths  # noqa: E402
from digitallife.ingest.schema import init_db  # noqa: E402
from digitallife.render import linktitle  # noqa: E402


def open_db() -> sqlite3.Connection:
    """连库并确保 link_title 存在（DL-16：对旧库跑一次 init_db 是安全且必要的）。"""
    con = sqlite3.connect(str(paths.db_path()), timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout=30000")
    init_db(con)
    con.commit()
    return con


def collect(con: sqlite3.Connection, retry_failed: bool = False) -> list[tuple[str, str]]:
    """返回 [(url, 原 title)]，只含「title 是域名形态且库里没有可用标题」的。

    判断走 linktitle 的同一套函数 —— 抓取侧与渲染侧的"缺不缺标题"口径必须一致，
    否则会出现「脚本认为不缺、页面显示还是域名」这种自说自话。
    """
    rows = con.execute(
        "SELECT url, title, COUNT(*) n FROM event "
        "WHERE kind='web' AND url IS NOT NULL AND url != '' "
        "GROUP BY url, title ORDER BY n DESC"
    ).fetchall()

    existing = {}
    for r in con.execute("SELECT url, title, status FROM link_title"):
        existing[str(r["url"])] = (str(r["title"] or ""), str(r["status"] or ""))

    # **先按 URL 聚合全部 title 再判断**：同一个 URL 在 event 表里可能有多条记录，
    # 标题有的好有的只有域名。若逐行判断后直接 continue，一个"第一行是真标题、
    # 第二行是域名"的 URL 会被误判成待抓（实测 todo 122 vs 真实缺口 100，多出的 22 条
    # 全属这一类）。口径：**只要该 URL 有一条像样的标题，就不需要联网**。
    by_url: dict[str, list[str]] = {}
    for r in rows:
        u = str(r["url"] or "").strip()
        if not u:
            continue
        by_url.setdefault(u, []).append(str(r["title"] or "").strip())

    seen: dict[str, str] = {}
    for u, titles in by_url.items():
        # 判据用 needs_title（含「title 就是 URL 本身」这一大类）。
        # 只用 is_hostlike 会漏掉 47,586 个 URL —— 实测全库无可读标题的是
        # 47,657 个，而纯域名口径只有 71 个（2026-10-25）。
        # 口径：**只要该 URL 有一条记录带可读标题，就不必联网**。
        if any(t and not linktitle.needs_title(t, u) for t in titles):
            continue
        t = next((x for x in titles if x), "")
        prev = existing.get(u)
        if prev:
            pt, st = prev
            if st == "ok" and pt and not linktitle.needs_title(pt, u):
                continue      # 库里已有可用标题
            if st == "failed" and not retry_failed:
                continue      # 上次已判定抓不到，不重复打扰
        seen[u] = t
    return list(seen.items())


def stat(con: sqlite3.Connection) -> dict:
    """规模统计（先算规模再决定跑不跑，不无脑全网抓）。"""
    rows = con.execute(
        "SELECT url, title FROM event WHERE kind='web' AND url IS NOT NULL AND url != '' "
        "GROUP BY url, title").fetchall()
    # 口径必须与 collect() 完全一致：**按 URL 聚合全部 title**，
    # 只要有一条不像域名就归为「有标题」。写两套口径会让 --stat 报的规模
    # 与实际要抓的条数对不上（实测曾报 100 vs 实抓 71）。
    urls: dict[str, list[str]] = {}
    for r in rows:
        u = str(r["url"] or "").strip()
        if not u:
            continue
        urls.setdefault(u, []).append(str(r["title"] or "").strip())
    hostlike = [u for u, ts in urls.items()
                if not any(t and not linktitle.needs_title(t, u) for t in ts)]
    recs = con.execute("SELECT status, COUNT(*) c FROM link_title GROUP BY status").fetchall()
    return {
        "distinct_url": len(urls),
        "has_title_url": len(urls) - len(hostlike),
        "hostlike_url": len(hostlike),
        "db_ok": next((int(r["c"]) for r in recs if r["status"] == "ok"), 0),
        "db_failed": next((int(r["c"]) for r in recs if r["status"] == "failed"), 0),
        "todo": len(collect(con)),
    }


def one(factory, item: tuple[str, str], timeout: float) -> tuple[str, str, bool]:
    """抓一条并落库。**失败也落 status='failed'**。"""
    url, _old = item
    t = linktitle.fetch(url, timeout=timeout)
    con = factory()
    try:
        if t and not linktitle.needs_title(t, url):
            linktitle.save(con, url, t, source="http", status="ok")
            con.commit()
            return url, t, True
        linktitle.save(con, url, "", source="http", status="failed")
        con.commit()
        return url, "", False
    finally:
        con.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="只抓前 N 条（0 = 全部）")
    ap.add_argument("--workers", type=int, default=6, help="并发数（默认 6，别开大）")
    ap.add_argument("--timeout", type=float, default=6.0, help="单条超时秒")
    ap.add_argument("--stat", action="store_true", help="只统计规模，不联网")
    ap.add_argument("--retry-failed", action="store_true", help="连上次失败的也重抓")
    args = ap.parse_args()

    con = open_db()
    try:
        s = stat(con)
        print("规模：distinct URL %d ｜ 无可用标题 %d ｜ 库内 ok %d / failed %d ｜ 待抓 %d"
              % (s["distinct_url"], s["hostlike_url"], s["db_ok"], s["db_failed"], s["todo"]),
              flush=True)
        if args.stat:
            return 0

        todo = collect(con, retry_failed=args.retry_failed)
        if args.limit:
            todo = todo[: args.limit]
        print("本次待抓 %d 条（并发 %d，单条超时 %.0fs）" % (len(todo), args.workers, args.timeout),
              flush=True)
        if not todo:
            return 0

        linktitle.load_from_db(con)      # 把已有标题装进内存，save() 会同步更新
        ok = fail = 0
        t0 = time.time()

        def factory():
            c = sqlite3.connect(str(paths.db_path()), timeout=30)
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA busy_timeout=30000")
            return c

        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            futs = [ex.submit(one, factory, it, args.timeout) for it in todo]
            for i, f in enumerate(as_completed(futs), 1):
                try:
                    _url, _title, good = f.result()
                except Exception as exc:  # noqa: BLE001  单条异常不得整批崩
                    print("  [warn] %s" % str(exc)[:120], flush=True)
                    fail += 1
                    continue
                if good:
                    ok += 1
                else:
                    fail += 1
                if i % 25 == 0:
                    print("  %d/%d ok=%d fail=%d %.0fs" % (
                        i, len(todo), ok, fail, time.time() - t0), flush=True)

        print("完成：成功 %d / 失败 %d，耗时 %.1f 分钟；成功率 %.1f%%"
              % (ok, fail, (time.time() - t0) / 60,
                 (ok * 100.0 / max(1, ok + fail))), flush=True)
        s2 = stat(con)
        print("库内现状：ok %d / failed %d / 仍待抓 %d"
              % (s2["db_ok"], s2["db_failed"], s2["todo"]), flush=True)
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    raise SystemExit(main())
