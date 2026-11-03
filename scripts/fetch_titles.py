#!/usr/bin/env python
"""批量抓取网页标题 -> 写进 link_title 表（不是磁盘缓存）。

用户第 3 条诉求（2026-10-25 原话）：
  「对于那些没有标题的链接，可以爬取链接获得标题，不是放到硬盘缓存，可以存入
   数据库当中。当然，没有爬取到域名，就原来的模样就好。」

为什么仍然分两步（抓取 vs 渲染）：
  建页要快且确定。联网会让 753 页从 6 分钟变成不可预估，还可能因为某个站反爬
  卡住整批。所以：
    1. 本脚本联网抓标题写库（可慢慢跑、可中断、可重跑）
    2. build_slice_html.py 只读库（纯本地，渲染期绝不联网）

=== 2026-11-02 重写：治「被封」，不是治症状 ===

上一轮实跑（37,070 条 / 9.1 小时 / 成功率 41.1%）暴露的不是代码 bug，是策略缺陷。
时序证据（决定性）：

    11-02 10 时  ->  77.4%
    11-02 11 时  ->  31.9%
    11-02 12 时  ->   0.3%     <- 几乎全灭
    11-02 13 时  ->  12.3%
    11-02 14 时  ->  57.7%     <- 冷却后恢复

bilibili 视频页返回 HTTP 412 + captcha（实测）。旧实现把 412 当成「这条 URL 的失败」
继续以同样速度打 —— 等于持续激怒对方。四处改动：

  1) 连接复用：4.05 s/条 -> 0.86 s/条（实测 4.7-12.5x）。
     旧实现每条 URL 新建 httpx.Client，TLS 握手全白付。
  2) 失败分类：412/429 -> blocked（熔断+冷却），404 -> dead（永不重试），
     超时 -> timeout（限次）。旧实现全塞 status='failed'，重跑时把 412 的又打一遍。
  3) 域名熔断：某域连续失败就只降这个域，不拖累其他域
     （实测失败 Top5 域名占 72.2%）。
  4) 落库修正：规范化组内**所有**变体一起落库。旧实现只写 members[0]，
     实测白抓了 7,452 个变体 —— 抓到标题了但没写进去，页面仍显示「域名·BV号」。

抓不到也落行（status='failed'）：否则每次建页都会重抓同一个抓不到的地址，
而「抓不到」本身是稳定结论（域名已死 / 需要登录 / 反爬）。failure_kind 记下理由。

用法::

    python scripts/fetch_titles.py --stat          # 只统计规模，不联网
    python scripts/fetch_titles.py --limit 200     # 先试 200 条
    python scripts/fetch_titles.py --workers 8     # 全量（硬上限 12）
    python scripts/fetch_titles.py --retry-failed  # 连之前失败的也重抓
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from digitallife import paths
from digitallife.ingest.schema import init_db
from digitallife.render import linktitle
from digitallife.render.linktitle import canon_url, needs_title

# 2026-11-02：canon_url 与 TRACK_PARAMS 已移入 linktitle（DL-23 单一出处）。
# 抓取侧去重与回填侧分组必须共用一个规范化实现，否则回填会把标题写到错组。
_TRACK_PARAMS = linktitle.TRACK_PARAMS

# 并发硬上限：实测上一轮 6 并发跑 9 小时就被 bilibili 全面封禁。
# 12 是「在没被封的前提下尽量快」的上界，再高就是自己找封。
MAX_WORKERS = 12


def open_db() -> sqlite3.Connection:
    """连库并确保 link_title 存在（DL-16：对旧库跑一次 init_db 是安全且必要的）。"""
    con = sqlite3.connect(str(paths.db_path()), timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout=30000")
    init_db(con)
    con.commit()
    return con


def collect(con: sqlite3.Connection, retry_failed: bool = False) -> list[tuple]:
    """返回 [(head_url, 原title, [组内全部url])]，只含缺可用标题的。

    判断走 linktitle 同一套函数 —— 抓取侧与渲染侧的「缺不缺标题」口径必须一致，
    否则会出现「脚本认为不缺、页面显示还是域名」这种自说自话。
    """
    rows = con.execute(
        "SELECT url, title FROM event "
        "WHERE kind='web' AND url IS NOT NULL AND url != '' "
        "GROUP BY url, title ORDER BY COUNT(*) DESC").fetchall()

    # 2026-11-02：不依赖调用方设过 row_factory —— collect() 曾被外部脚本以裸连接
    # 调用而抛 TypeError(tuple indices must be integers)。索引取值最稳。
    existing = {}
    for r in con.execute(
            "SELECT url, title, status, failure_kind FROM link_title"):
        existing[str(r[0])] = (str(r[1] or ""), str(r[2] or ""), str(r[3] or ""))

    by_url: dict[str, list[str]] = {}
    for r in rows:
        u = str(r["url"] or "").strip()
        if not u:
            continue
        by_url.setdefault(u, []).append(str(r["title"] or "").strip())

    seen: dict[str, str] = {}
    dead = 0
    for u, titles in by_url.items():
        # 口径：只要该 URL 有一条记录带可读标题，就不必联网。
        if any(t and not needs_title(t, u) for t in titles):
            continue
        t = next((x for x in titles if x), "")
        prev = existing.get(u)
        if prev:
            pt, st, fk = prev
            # 2026-11-02 修（验证器发现）：status=ok 一律跳过，不看 title 是否可用。
            # 旧写法 st=="ok" and pt and not needs_title(...) 有漏洞：
            # 若某行 ok 但 title 恰好被判 needs_title（例如标题就等于域名），
            # 它就会掉进待抓队列，于是每轮都被重抓。实测第 1 轮成功的 7 条
            # 在第 2 轮又被打了一遍 —— 这正是「断点续跑失效」的实质。
            # 口径：抓过且成功过就不再抓；标题不够好是渲染侧的降级问题，
            # 不是「该重抓」的理由。
            if st == "ok":
                continue
            if fk in (linktitle.KIND_DEAD, linktitle.KIND_NO_TITLE):
                dead += 1
                continue                      # 页面已死 / 本无标题，永不再碰
            if st == "failed" and not retry_failed and fk in ("", linktitle.KIND_TIMEOUT):
                continue                      # 旧失败记录，默认不重试
        seen[u] = t

    if dead:
        print("  跳过已判死/无标题：%d 条" % dead, flush=True)

    # 规范化去重：同一页面的不同追踪变体只抓一次，
    # 但**组内所有变体一起返回**（2026-11-02 修）。
    # 旧实现 out.append((members[0], ...)) 只返回第一条，抓到后也只落那一条，
    # 导致同组其他变体仍显示「域名·BV号」—— 实测白抓 7,452 个变体。
    groups: dict[str, list[str]] = {}
    for u in seen:
        groups.setdefault(canon_url(u), []).append(u)

    out: list[tuple] = []
    merged = 0
    for _c, members in groups.items():
        members.sort()
        out.append((members[0], seen[members[0]], list(members)))
        merged += len(members) - 1
    if merged:
        print("  规范化去重：%d 个 URL 合并为 %d 次抓取（省 %d 次）"
              % (len(seen), len(out), merged), flush=True)
    return out


def stat(con: sqlite3.Connection) -> dict:
    """规模统计（先算规模再决定跑不跑，不无脑全网抓）。"""
    rows = con.execute(
        "SELECT url, title FROM event WHERE kind='web' AND url IS NOT NULL AND url != '' "
        "GROUP BY url, title").fetchall()
    # 口径必须与 collect() 完全一致：按 URL 聚合全部 title，
    # 只要有一条不像域名就归为「有标题」。写两套口径会让 --stat 报的规模
    # 与实际要抓的条数对不上（实测曾报 100 vs 实抓 71）。
    urls: dict[str, list[str]] = {}
    for r in rows:
        u = str(r["url"] or "").strip()
        if not u:
            continue
        urls.setdefault(u, []).append(str(r["title"] or "").strip())
    hostlike = [u for u, ts in urls.items()
                if not any(t and not needs_title(t, u) for t in ts)]
    recs = con.execute(
        "SELECT status, failure_kind, COUNT(*) c FROM link_title "
        "GROUP BY status, failure_kind").fetchall()
    by_kind: dict[str, int] = {}
    for r in recs:
        k = str(r["failure_kind"] or "") or str(r["status"] or "")
        by_kind[k] = by_kind.get(k, 0) + int(r["c"])
    return {
        "distinct_url": len(urls),
        "has_title_url": len(urls) - len(hostlike),
        "hostlike_url": len(hostlike),
        "by_kind": by_kind,
        "todo": len(collect(con)),
    }


# ---------------------------------------------------------------- 健康自检（Strategy 层）
#
# 2026-11-02 用户要求「检测函数保持通用性」。分三层：
#   linkhealth      只判断（Probe -> Report，不认识业务）
#   linkprobes      只描述判据（口径单一出处）
#   HealthStrategy  本类，**读 Report.vetoes() 决定行动**（熔断/降速/钳并发）
# 分开的好处：改判据不影响行为，改行为不影响判据。

class HealthStrategy:
    """把检测结论翻译成动作。检测自己不做事。"""

    def __init__(self, guard=None, limiter=None):
        self.guard = guard
        self.limiter = limiter
        self.last = None
        self.actions: list = []

    def refresh(self, state):
        """跑一条检测，返回 Report（调用方可读、可落盘）。"""
        from digitallife.render import linkhealth, linkprobes
        rep = linkhealth.run(linkprobes.runtime_probes(state), fast=True)
        self.last = rep
        self.apply(rep)
        return rep

    def apply(self, rep) -> None:
        """按 veto 采取动作。每个动作都记进 self.actions，便于事后归因。"""
        v = rep.vetoes()
        if "rt.blocked_streak" in v and self.limiter is not None:
            before = self.limiter.cur
            self.limiter.slow_down()
            if self.limiter.cur != before:
                self.actions.append(("slow_down_streak", before, self.limiter.cur))
        if "rt.blocked_ratio" in v and self.limiter is not None:
            before = self.limiter.cur
            self.limiter.slow_down()
            if self.limiter.cur != before:
                self.actions.append(("slow_down_ratio", before, self.limiter.cur))
        if "rt.workers" in v and self.limiter is not None:
            if self.limiter.cur > MAX_WORKERS:
                self.actions.append(("clamp_workers", self.limiter.cur, MAX_WORKERS))
                self.limiter.cur = MAX_WORKERS


# ---------------------------------------------------------------- 域名熔断

class DomainGuard:
    """按域名维护失败状态：只惩罚出问题的站，不拖累其他站。

    2026-11-02 实测依据：失败 Top5 域名占全部失败 72.2%
    （bilibili 12,709 / google 1,922 / space.bilibili 891 / csdn 737 / jiqizhixin 541）。
    把这几家单独限速、其余照常跑 —— 旧实现一视同仁，被 bilibili 一家拖住全量。

    规则：
      连续失败 block_threshold 次 -> 该域熔断 cooldown 秒，期间跳过该域所有 URL
      收到 blocked(412/429) -> **立刻**熔断该域，不等累积到阈值
      dead/no_title 不计入 —— 那是页面级结论，不代表站点在拒绝我们
    """

    def __init__(self, block_threshold: int = 5, cooldown: float = 600.0):
        self.block_threshold = block_threshold
        self.cooldown = cooldown
        self._lock = threading.Lock()
        self._fail: dict[str, int] = defaultdict(int)
        self._until: dict[str, float] = {}

    def host(self, url: str) -> str:
        return linktitle.host_of(url) or "(unknown)"

    def is_open(self, url: str) -> bool:
        """该域当前是否可放行（熔断期返回 False）。"""
        h = self.host(url)
        with self._lock:
            until = self._until.get(h)
            if until is None:
                return True
            if time.time() >= until:
                self._until.pop(h, None)
                self._fail[h] = 0
                return True
            return False

    def record(self, url: str, kind: str) -> None:
        h = self.host(url)
        with self._lock:
            if kind == linktitle.KIND_OK:
                self._fail[h] = 0
                return
            if kind in (linktitle.KIND_DEAD, linktitle.KIND_NO_TITLE):
                return                  # 页面级结论，不动站点状态
            if kind == linktitle.KIND_BLOCKED:
                self._until[h] = time.time() + self.cooldown
                self._fail[h] = self.block_threshold
                return
            self._fail[h] += 1
            if self._fail[h] >= self.block_threshold:
                self._until[h] = time.time() + self.cooldown

    def snapshot(self) -> dict:
        with self._lock:
            now = time.time()
            return {"tripped": {h: int(u - now) for h, u in self._until.items() if u > now},
                    "fails": {h: n for h, n in self._fail.items() if n}}


class AdaptiveLimiter:
    """自适应并发：连续成功提速，连续失败降速，收到 412/429 立刻降。

    旧实现固定 --workers 6，对目标站是**恒定压力** —— 没有反馈回路，
    所以只会一路撞到被全部封禁为止。
    """

    def __init__(self, start: int = 4, lo: int = 1, hi: int = 12,
                 up_after: int = 20, down_after: int = 5):
        self.cur = max(lo, min(hi, start))
        self.lo, self.hi = lo, hi
        self.up_after = up_after
        self.down_after = down_after
        self._lock = threading.Lock()
        self._ok = 0
        self._fail = 0
        self.events: list[tuple] = []

    def report(self, good: bool) -> None:
        with self._lock:
            if good:
                self._ok += 1
                self._fail = 0
                if self._ok >= self.up_after and self.cur < self.hi:
                    self.cur += 1
                    self._ok = 0
                    self.events.append((time.time(), self.cur, "up"))
            else:
                self._fail += 1
                self._ok = 0
                if self._fail >= self.down_after and self.cur > self.lo:
                    self.cur = max(self.lo, self.cur - 2)
                    self._fail = 0
                    self.events.append((time.time(), self.cur, "down"))

    def slow_down(self) -> None:
        """收到 412/429 时立刻全局降速（不等连续失败累积）。"""
        with self._lock:
            if self.cur > self.lo:
                self.cur = max(self.lo, self.cur // 2)
                self.events.append((time.time(), self.cur, "blocked"))


# ---------------------------------------------------------------- 抓取主体

def _blocked_streak(kinds: dict) -> int:
    # 连续被拒次数。blocked 一出现就应触发熔断，故直接用其计数。
    return int(kinds.get(linktitle.KIND_BLOCKED, 0))


def _worker_factory():
    c = sqlite3.connect(str(paths.db_path()), timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA busy_timeout=30000")
    return c


def make_one(timeout: float, guard: DomainGuard):
    """造一个「抓一条并落库」的函数（闭包持有 guard，便于单测注入）。"""

    def one(item):
        head, _old, members = item
        if not guard.is_open(head):
            # 熔断期：不抓，也不写库（留待冷却后重跑）
            return head, "skip", 0.0
        t0 = time.time()
        title, kind = linktitle.fetch_detail(head, timeout=timeout)
        dt = time.time() - t0
        con = _worker_factory()
        try:
            if kind == linktitle.KIND_OK and title:
                # 组内**所有**变体一起落库（2026-11-02 修，旧实现只写 members[0]）
                for m in members:
                    linktitle.save(con, m, title, source="http", status="ok")
            else:
                for m in members:
                    linktitle.save(con, m, "", source="http", status="failed",
                                   failure_kind=kind)
            con.commit()
        finally:
            con.close()
        return head, kind, dt

    return one


def run_batch(todo, workers: int, timeout: float,
              guard: DomainGuard | None = None,
              limiter: AdaptiveLimiter | None = None,
              health: HealthStrategy | None = None,
              check_every: int = 25) -> tuple:
    """跑一批。返回 (ok, fail, skipped, kind_counts)。

    动态提交：不是一次性把所有 future 塞进池子，而是按 limiter 当前并发决定
    在飞数量 —— 否则「降并发」只是改了个变量，实际压力不变。
    """
    guard = guard or DomainGuard()
    lim = limiter or AdaptiveLimiter(start=workers, hi=MAX_WORKERS)
    hs = health or HealthStrategy(guard=guard, limiter=lim)
    kinds: dict[str, int] = defaultdict(int)
    ok = fail = skipped = done = 0
    lock = threading.Lock()
    t0 = time.time()
    total = len(todo)
    one = make_one(timeout, guard)

    in_flight = {}
    idx = 0
    with ThreadPoolExecutor(max_workers=max(1, MAX_WORKERS * 2)) as ex:
        while idx < len(todo) or in_flight:
            while idx < len(todo) and len(in_flight) < lim.cur:
                in_flight[ex.submit(one, todo[idx])] = todo[idx]
                idx += 1
            if not in_flight:
                break
            f = next(as_completed(list(in_flight)))
            in_flight.pop(f, None)
            try:
                _h, kind, _dt = f.result()
            except Exception as exc:  # noqa: BLE001  单条异常不得整批崩
                print("  [warn] %s" % str(exc)[:120], flush=True)
                with lock:
                    done += 1
                    fail += 1
                    kinds["error"] += 1
                continue
            with lock:
                done += 1
                kinds[kind] += 1
                if kind == linktitle.KIND_OK:
                    ok += 1
                    guard.record(_h, kind)
                    lim.report(True)
                elif kind == "skip":
                    skipped += 1
                else:
                    fail += 1
                    guard.record(_h, kind)
                    lim.report(False)
                    if kind == linktitle.KIND_BLOCKED:
                        lim.slow_down()
                # 2026-11-02：每 check_every 条跑一次运行期自检，
                # 由 HealthStrategy 读结论决定是否降并发/钳上界。
                # 检测与策略分层：这里只问「健康吗」，怎么处置由 Strategy 定。
                if done % check_every == 0 or done == total:
                    rep = hs.refresh({
                        "seen": done, "ok": ok, "fail": fail,
                        "blocked": kinds.get(linktitle.KIND_BLOCKED, 0),
                        "dead": kinds.get(linktitle.KIND_DEAD, 0),
                        "blocked_streak": _blocked_streak(kinds),
                        "cur_workers": lim.cur,
                    })
                    health_line = ""
                    if rep.failed or rep.warned:
                        bad_ids = [r.probe_id for r in rep.results
                                   if not r.passed]
                        health_line = " | 自检:" + ",".join(bad_ids)
                    print("  %d/%d ok=%d fail=%d skip=%d 并发=%d %.0fs%s"
                          % (done, total, ok, fail, skipped, lim.cur,
                             time.time() - t0, health_line), flush=True)
    return ok, fail, skipped, dict(kinds), lim, guard, hs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="只抓前 N 条（0 = 全部）")
    ap.add_argument("--workers", type=int, default=6,
                    help="起始并发（默认 6；硬上限 %d）" % MAX_WORKERS)
    ap.add_argument("--timeout", type=float, default=10.0, help="单条超时秒")
    ap.add_argument("--stat", action="store_true", help="只统计规模，不联网")
    ap.add_argument("--retry-failed", action="store_true", help="连上次失败的也重抓")
    ap.add_argument("--cooldown", type=float, default=600.0,
                    help="域名熔断冷却秒数（默认 600）")
    args = ap.parse_args()

    workers = max(1, min(MAX_WORKERS, args.workers))
    if workers != args.workers:
        print("  [note] --workers %d 超出上限，已钳到 %d" % (args.workers, workers),
              flush=True)

    con = open_db()
    try:
        s = stat(con)
        kinds = s["by_kind"]
        print("规模：distinct URL %d ｜ 无可用标题 %d ｜ 待抓 %d"
              % (s["distinct_url"], s["hostlike_url"], s["todo"]), flush=True)
        print("  库内分类：ok %d ｜ blocked %d ｜ dead %d ｜ timeout %d ｜ no_title %d ｜ 空(old) %d"
              % (kinds.get("ok", 0), kinds.get("blocked", 0), kinds.get("dead", 0),
                 kinds.get("timeout", 0), kinds.get("no_title", 0),
                 kinds.get("failed", 0)), flush=True)
        if args.stat:
            return 0

        todo = collect(con, retry_failed=args.retry_failed)
        if args.limit:
            todo = todo[: args.limit]
        n_urls = sum(len(x[2]) for x in todo)
        print("本次待抓 %d 组 / %d 个 URL（起始并发 %d，超时 %.0fs）"
              % (len(todo), n_urls, workers, args.timeout), flush=True)
        if not todo:
            return 0

        linktitle.load_from_db(con)
        ok, fail, skipped, kc, lim, guard, hs = run_batch(
            todo, workers, args.timeout,
            guard=DomainGuard(cooldown=args.cooldown),
            limiter=AdaptiveLimiter(start=workers, hi=MAX_WORKERS))

        print()
        print("完成：成功 %d / 失败 %d / 跳过(熔断) %d，成功率 %.1f%%"
              % (ok, fail, skipped, ok * 100.0 / max(1, ok + fail)), flush=True)
        print("  分类明细：%s" % kc, flush=True)
        snap = guard.snapshot()
        if snap["tripped"]:
            print("  熔断中的域名：%s" % snap["tripped"], flush=True)
        if lim.events:
            print("  并发调整次数：%d（末值 %d）" % (len(lim.events), lim.cur), flush=True)

        s2 = stat(con)
        k2 = s2["by_kind"]
        print("库内现状：ok %d ｜ blocked %d ｜ dead %d ｜ timeout %d ｜ no_title %d ｜ 仍待抓 %d"
              % (k2.get("ok", 0), k2.get("blocked", 0), k2.get("dead", 0),
                 k2.get("timeout", 0), k2.get("no_title", 0), s2["todo"]), flush=True)
        return 0
    finally:
        linktitle.close_client()
        con.close()


if __name__ == "__main__":
    raise SystemExit(main())
