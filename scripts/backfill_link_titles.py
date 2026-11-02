#!/usr/bin/env python
"""回填同规范化组内的标题 —— 零网络。

背景（2026-11-02 实测）：
  上一轮 fetch_titles.py 的 collect() 做了规范化去重，但**只抓组成员第一条、
  也只落库第一条**：

      out.append((members[0], seen[members[0]]))   # 同组其他变体被丢弃

  后果：抓了 39,855 组，落库 39,855 条，同组另外 7,452 个变体查不到记录，
  渲染时退化成「域名 · BV号」。这不是抓取失败，是落库策略不完整。

本脚本做的事：
  对每个规范化组，若组内任一变体已有可用标题（status='ok'），
  把该标题落给组内**所有**变体。**不联网**。

用法::

    python scripts/backfill_link_titles.py --stat     # 只统计，不写
    python scripts/backfill_link_titles.py            # 真回填
    python scripts/backfill_link_titles.py --dry-run  # 打印将要写的行，不提交
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

from digitallife import paths
from digitallife.ingest.schema import init_db
from digitallife.render import linktitle
from digitallife.render.linktitle import canon_url, needs_title


def open_db() -> sqlite3.Connection:
    con = sqlite3.connect(str(paths.db_path()), timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout=30000")
    init_db(con)
    con.commit()
    return con


def plan(con: sqlite3.Connection) -> tuple[list[tuple[str, str]], int]:
    """返回 ([(url, 要写的 title), ...], 规范化组数)。

    **按「规范化组」为最小单位**：组内只要有一条 status='ok' 且标题可用，
    就把该标题写给它组内**所有**缺可用标题的成员。零网络。

    2026-11-02 修 bug：早先实现用 title 作字典键，**两个不同组碰巧标题相同
    （如都是「备案」）会互相覆盖**，实测只写 721 条而应有 1,489 条。
    现在改用 list 累积，不再以 title 为键。
    """
    rows = con.execute(
        "SELECT url, title FROM event "
        "WHERE kind='web' AND url IS NOT NULL AND url != '' "
        "GROUP BY url, title").fetchall()

    by_url: dict[str, list[str]] = {}
    for r in rows:
        u = str(r["url"] or "").strip()
        if not u:
            continue
        by_url.setdefault(u, []).append(str(r["title"] or "").strip())

    # 库内已有记录（任何 status）。failed 行 title 为空，**不能当回填源**。
    existing: dict[str, tuple[str, str]] = {}
    for r in con.execute("SELECT url, title, status FROM link_title"):
        existing[str(r["url"])] = (str(r["title"] or ""), str(r["status"] or ""))

    def usable(u: str) -> str:
        """该 URL 当前是否已有可用标题（库里 ok 行 或 event 自带）。"""
        prev = existing.get(u)
        if prev and prev[1] == "ok" and prev[0] and not needs_title(prev[0], u):
            return prev[0]
        return ""

    groups: dict[str, list[str]] = {}
    for u in by_url:
        groups.setdefault(canon_url(u), []).append(u)

    out: list[tuple[str, str]] = []
    for _c, members in groups.items():
        good = ""
        for m in members:
            good = usable(m)
            if good:
                break
        if not good:
            continue                        # 组内没有任何可用标题，需联网
        for m in members:
            if usable(m):
                continue                    # 已有可用标题，不必写
            # event 自身带了可读标题的也跳过（渲染已能用）
            if any(t and not needs_title(t, m) for t in by_url.get(m, [])):
                continue
            out.append((m, good))
    return out, len(groups)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stat", action="store_true", help="只统计，不写库")
    ap.add_argument("--dry-run", action="store_true", help="打印样本，不提交")
    ap.add_argument("--limit", type=int, default=0, help="只处理前 N 组")
    args = ap.parse_args()

    con = open_db()
    try:
        todo, n_groups = plan(con)
        n_urls = len(todo)
        print("规范化组总数        : %d" % n_groups)
        print("待回填的 URL        : %d" % n_urls)
        print()

        if args.stat:
            for i, (u, t) in enumerate(todo[:5], 1):
                print("  样本 %d: %s" % (i, u[:88]))
                print("           <- %r" % t[:52])
            if not todo:
                print("  （没有可零成本回填的 URL）")
            return 0

        linktitle.load_from_db(con)
        done = 0
        skipped = 0
        for u, t in todo:
            if args.limit and done >= args.limit:
                break
            try:
                linktitle.save(con, u, t, source="backfill", status="ok")
                done += 1
            except sqlite3.Error as exc:
                print("  [warn] %s: %s" % (u[:60], exc), flush=True)
                skipped += 1
            if done % 500 == 0:
                con.commit()
                print("  已回填 %d / %d" % (done, n_urls), flush=True)
        if args.dry_run:
            con.rollback()
            print("dry-run：已回滚，未写库（计划回填 %d 条）" % done)
        else:
            con.commit()
            print("回填完成：写入 %d 条，跳过 %d 条" % (done, skipped))
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    raise SystemExit(main())
