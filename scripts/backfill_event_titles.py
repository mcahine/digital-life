"""扩展回填：event 表自带的可读标题也回填进 link_title（零网络）。

修正我此前的口径错误（2026-11-02 验证器第二轮）：
  旧口径只认 link_title.status='ok' 才算「已有标题」，
  但 event 表里该 URL 往往自带真标题（浏览器历史带下来的），
  例如 bilibili 视频页：
      url   = https://www.bilibili.com/video/BV18h4y1H7tq/?spm_id_from=...
      title = '鸭 王 争 爸 3'
  渲染 resolve() 第③级就用它，所以页面看着正常 —— 但**没落进 link_title**。
  任务书说的「同组其他变体没落库」正是这一类：32,024 个（含 bilibili 8,649）。

本脚本把这些 event 标题回填进 link_title：
  - 零网络
  - 回填后 resolve() 走 'db' 来源而非 'raw'，且抓取侧不会再重复抓
  - source 记为 'event' 以示来源，便于审计

用法::

    python scripts/backfill_event_titles.py --stat
    python scripts/backfill_event_titles.py
    python scripts/backfill_event_titles.py --bilibili-only   # 只补 bilibili
"""
from __future__ import annotations

import argparse
import sqlite3
import sys

from digitallife import paths
from digitallife.ingest.schema import init_db
from digitallife.render import linktitle
from digitallife.render.linktitle import needs_title


def open_db() -> sqlite3.Connection:
    con = sqlite3.connect(str(paths.db_path()), timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout=30000")
    init_db(con)
    con.commit()
    return con


def plan(con: sqlite3.Connection, bilibili_only: bool = False):
    """返回 [(url, 可回填标题)]。

    判据：event 表里该 URL 自带可读标题（非域名形态、非 URL 本身），
    且 link_title 里没有 status='ok' 的行。
    """
    rows = con.execute(
        "SELECT url, title FROM event "
        "WHERE kind='web' AND url IS NOT NULL AND url != ''").fetchall()

    by_url: dict[str, list[str]] = {}
    for r in rows:
        u = str(r[0] or "").strip()
        if not u:
            continue
        by_url.setdefault(u, []).append(str(r[1] or "").strip())

    ok_urls = {str(r[0]) for r in con.execute(
        "SELECT url FROM link_title WHERE status='ok' AND title!=''")}

    out = []
    for u, titles in by_url.items():
        if u in ok_urls:
            continue
        if bilibili_only and "bilibili" not in u:
            continue
        # 取第一条可读标题
        good = ""
        for t in titles:
            if t and not needs_title(t, u):
                good = t
                break
        if good:
            out.append((u, good))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stat", action="store_true", help="只统计")
    ap.add_argument("--bilibili-only", action="store_true", help="只补 bilibili")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    con = open_db()
    try:
        todo = plan(con, bilibili_only=args.bilibili_only)
        n_bili = sum(1 for u, _ in todo if "bilibili" in u)
        print("可回填（event 自带可读标题、link_title 无 ok 行）: %d" % len(todo))
        print("  其中 bilibili 系: %d" % n_bili)
        print()

        if args.stat:
            for u, t in todo[:5]:
                print("  %r" % t[:56])
                print("     %s" % u[:84])
            return 0

        if args.limit:
            todo = todo[: args.limit]

        linktitle.load_from_db(con)
        done = 0
        for u, t in todo:
            linktitle.save(con, u, t, source="event", status="ok")
            done += 1
            if done % 2000 == 0:
                con.commit()
                print("  已回填 %d / %d" % (done, len(todo)), flush=True)
        con.commit()
        print("回填完成：写入 %d 条" % done)

        # 幂等检查
        left = plan(con, bilibili_only=args.bilibili_only)
        print("再查剩余: %d（应为 0）" % len(left))
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    raise SystemExit(main())
