"""链接标题链路的**判据单一出处**（2026-11-02）。

用户要求：「检测函数保持通用性」。内核在 linkhealth.py，本文件只是**描述**：
把「缺不缺标题」「缺口有多大」「失败怎么分类」这些量的定义写在这**一处**，
脚本、测试、体检命令、交付报告都引用它，不再各算一套。

## 为什么必须单一出处（这轮验收的实打实教训）

同一件事被算出过五个数：
    12,377 / 39,283 / 7,452 / 897 / 32,024
根因不是算错，是**口径定义散落在各处**：
  - 有的只查 link_title（漏掉 event 自带标题的 32,024 条 → 得出「上限 897」的错误结论）
  - 有的把「有行」当「有可用标题」（库内 23,257 行 failed/title 空 → 算大缺口）
  - 有的按 event.title 当 URL 集、有的按 event.url（两集合差 22,000 条）

## 本文件定义的口径（**唯一权威**）

「可用标题」= 下列任一成立：
    a) link_title 有该 url 且 status='ok' 且 title 非空且不像域名
    b) event 表该 url 自带可读 title（resolve() 第③级就用它）
    c) 同规范化组内已有 a 或 b（回填可借用）

「未落库」= 该 url 在 link_title 里**没有任何行**（不含回填过的）

「真缺口」= 既无可用标题 a、b，组内也没有 → 渲染必定退化成「域名·路径」，
          且只能联网解决（回填救不了）

问题：任务书要求的两条口径必须都能算出来，见 PROBE_* 注释。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from digitallife.render import linktitle as LT
from digitallife.render.linkhealth import (
    AtLeast, AtMost, Between, Probe, Truthy,
)


# ============================================================ 口径函数（唯一定义）

def distinct_web_urls(con) -> set:
    """web URL 全集（渲染实际读的 event.url 口径）。"""
    return {str(r[0]) for r in con.execute(
        "SELECT DISTINCT url FROM event WHERE kind='web' "
        "AND url IS NOT NULL AND url != ''")}


def distinct_web_titles(con) -> set:
    """任务书验收用的 event.title 口径（52,698 的来源）。"""
    return {str(r[0]) for r in con.execute(
        "SELECT DISTINCT title FROM event WHERE kind='web' "
        "AND title LIKE 'http%'")}


def link_title_urls(con) -> set:
    """link_title 里**有任何行**的 url（不看 status）。"""
    return {str(r[0]) for r in con.execute("SELECT url FROM link_title")}


def ok_title_urls(con) -> set:
    """link_title 里 status='ok' 且 title 非空 的 url。"""
    return {str(r[0]) for r in con.execute(
        "SELECT url FROM link_title WHERE status='ok' AND title != ''")}


def event_readable(con) -> dict:
    """event 表里自带可读标题的 {url: title}。**这是第二轮漏掉的那一维。**"""
    by_url: dict = {}
    for r in con.execute(
            "SELECT url, title FROM event WHERE kind='web' "
            "AND url IS NOT NULL AND url != ''"):
        u = str(r[0] or "").strip()
        if not u:
            continue
        by_url.setdefault(u, []).append(str(r[1] or "").strip())
    out = {}
    for u, ts in by_url.items():
        for t in ts:
            if t and not LT.needs_title(t, u):
                out[u] = t
                break
    return out


def effective_titled(con) -> set:
    """**真正有可用标题**的 url 集合 = ok 行 ∪ event 自带可读标题。"""
    return ok_title_urls(con) | set(event_readable(con))


def true_gap(con) -> list:
    """真缺口：既无可用标题，组内兄弟也没有 → 必定退化成「域名·路径」。

    这类只能联网解决，回填救不了。
    """
    urls = distinct_web_urls(con)
    titled = effective_titled(con)
    # 组内兄弟
    groups: dict = {}
    for u in urls | titled:
        groups.setdefault(LT.canon_url(u), []).append(u)
    titled_groups = {LT.canon_url(u) for u in titled}
    return [u for u in urls
            if u not in titled and LT.canon_url(u) not in titled_groups]


def gap_by_host(con, n: int = 12) -> dict:
    """真缺口按域名分布 —— 用来回答「该修哪一环」。"""
    from collections import Counter
    from urllib.parse import urlsplit
    c: Counter = Counter()
    for u in true_gap(con):
        try:
            h = urlsplit(u).netloc.lower()
        except Exception:
            h = "?"
        c[h[4:] if h.startswith("www.") else h] += 1
    return dict(c.most_common(n))


def failure_kinds(con) -> dict:
    """link_title 的失败分类计数（failure_kind 列）。"""
    out: dict = {}
    for r in con.execute(
            "SELECT COALESCE(NULLIF(failure_kind,''), status) k, COUNT(*) c "
            "FROM link_title GROUP BY k"):
        out[str(r[0])] = int(r[1])
    return out


def blocked_hosts(con, n: int = 10) -> dict:
    """被拒（blocked）的域名 top N —— 直接指向「哪个站把我们挡了」。"""
    from collections import Counter
    from urllib.parse import urlsplit
    c: Counter = Counter()
    for r in con.execute(
            "SELECT url FROM link_title WHERE failure_kind='blocked'"):
        try:
            h = urlsplit(str(r[0])).netloc.lower()
        except Exception:
            h = "?"
        c[h[4:] if h.startswith("www.") else h] += 1
    return dict(c.most_common(n))


def schema_version(con):
    try:
        row = con.execute(
            "SELECT value FROM meta WHERE key='schema_version'").fetchone()
        return int(row[0]) if row else 0
    except sqlite3.OperationalError:
        return 0


# ============================================================ Probe 描述
# 新增一类检查 = 往这个列表加一条，**不改检测器本体**。

def all_probes() -> list:
    return [
        # ---- 完整性：库结构对不对 ----
        Probe(
            id="schema.version", title="schema 版本已达 v3（含 failure_kind）",
            category="integrity", measure=lambda c: schema_version(c),
            expect=AtLeast(3), severity="fail", slow=False,
            hint="跑 python -m digitallife.cli init_db 或对旧库调 init_db()"),

        Probe(
            id="link_title.has_failure_kind", title="link_title 有 failure_kind 列",
            category="integrity",
            measure=lambda c: "failure_kind" in {
                r[1] for r in c.execute("PRAGMA table_info(link_title)")},
            expect=Truthy(), severity="fail", slow=False,
            hint="schema v3 迁移未应用"),

        # ---- 覆盖：缺口有多大 ----
        Probe(
            id="coverage.link_title_rows", title="link_title 总行数",
            category="coverage",
            measure=lambda c: c.execute(
                "SELECT COUNT(*) FROM link_title").fetchone()[0],
            expect=AtLeast(1), severity="info", slow=False,
            hint="库为空，先跑一次抓取或回填"),

        Probe(
            id="coverage.ok_title_urls", title="有可用标题的 url 数（ok 行）",
            category="coverage",
            measure=lambda c: len(ok_title_urls(c)),
            expect=AtLeast(1), severity="info", slow=False,
            hint="ok 行太少，说明抓取/回填都没跑"),

        Probe(
            id="coverage.event_readable", title="event 自带可读标题的 url 数",
            category="coverage", measure=lambda c: len(event_readable(c)),
            expect=AtLeast(1), severity="info", slow=True,
            hint="这一维决定「零成本回填」的量，漏了会低估可回填上限"),

        Probe(
            id="coverage.unpersisted_A", title="未落库（任务书 event.title 口径）",
            category="coverage",
            measure=lambda c: len(distinct_web_titles(c) - link_title_urls(c)),
            expect=AtLeast(0), severity="info", slow=True,
            hint="任务书验收：12,377 -> 4,925。剩余部分只能联网"),

        Probe(
            id="coverage.unpersisted_B", title="未落库（渲染 event.url 口径）",
            category="coverage",
            measure=lambda c: len(distinct_web_urls(c) - link_title_urls(c)),
            expect=AtLeast(0), severity="info", slow=True,
            hint="渲染实际读的口径，与 A 差一层"),

        Probe(
            id="coverage.true_gap", title="真缺口（回填救不了、只能联网）",
            category="coverage", measure=lambda c: len(true_gap(c)),
            expect=AtLeast(0), severity="warn", slow=True,
            hint="见 coverage.true_gap.top_host —— 哪个域名占大头就等谁解封"),

        # ---- 可达性：抓取还通不通 ----
        Probe(
            id="reach.blocked_ratio", title="blocked 占比（被拒样本 / 全部样本）",
            category="reachability",
            measure=lambda c: _blocked_ratio(c),
            expect=AtMost(0.5), severity="warn", slow=False,
            hint="blocked 占比高 = 站点在拒绝我们，应等冷却而非继续打"),

        Probe(
            id="reach.dead_ratio", title="dead 占比（页面已死 / 全部样本）",
            category="reachability",
            measure=lambda c: _kind_ratio(c, "dead"),
            expect=AtMost(0.9), severity="info", slow=False,
            hint="dead 占比极高说明链接大面积失效，属正常历史数据"),

        # ---- 策略：机制是否在工作 ----
        Probe(
            id="policy.backfill_rows", title="回填写入的行数",
            category="policy",
            measure=lambda c: c.execute(
                "SELECT COUNT(*) FROM link_title "
                "WHERE source IN ('backfill','event')").fetchone()[0],
            expect=AtLeast(0), severity="info", slow=False,
            hint="0 表示没跑回填，等于放弃了零成本收益"),

        Probe(
            id="integrity.attempts_capped", title="attempts 超限的行数（应极少）",
            category="integrity",
            measure=lambda c: c.execute(
                "SELECT COUNT(*) FROM link_title WHERE attempts > 10").fetchone()[0],
            expect=AtMost(50), severity="warn", slow=False,
            hint="attempts 一直涨说明同一批 URL 被反复重抓 —— 断点续跑失效"),
    ]


def _blocked_ratio(con) -> float:
    kinds = failure_kinds(con)
    tot = sum(kinds.values())
    return round(kinds.get("blocked", 0) / tot, 4) if tot else 0.0


def _kind_ratio(con, kind: str) -> float:
    kinds = failure_kinds(con)
    tot = sum(kinds.values())
    return round(kinds.get(kind, 0) / tot, 4) if tot else 0.0

# ============================================================ 运行期探针
# 跑批过程中用：不查库，只吃本次运行的实时计数（零 IO）。
# 与上面 all_probes() 共享同一套 Expect 判据形式 —— 判据口径不分裂。

def runtime_probes(state) -> list:
    """把一次跑批的实时状态包装成 Probe 列表。

    state 是一个 dict（由调用方每次刷新）：
        seen ok fail skipped blocked dead timeout no_title
        cur_workers blocked_streak elapsed_s
    """
    def g(k, d=0):
        return lambda _ctx: state.get(k, d)

    return [
        Probe(id="rt.blocked_streak", title="连续被拒次数（触发熔断）",
              category="reachability", measure=g("blocked_streak"),
              expect=AtMost(3), severity="warn", slow=False,
              hint="连续被拒说明该域正在挡我们，应冷却而不是继续打"),

        Probe(id="rt.blocked_ratio", title="本轮被拒占比",
              category="reachability", measure=lambda _c: round(
                  state.get("blocked", 0) / max(1, state.get("seen", 1)), 4),
              expect=AtMost(0.3), severity="warn", slow=False,
              hint="占比高 = 整体被限流，应降并发"),

        Probe(id="rt.workers", title="当前并发",
              category="policy", measure=g("cur_workers"),
              expect=Between(1, 12), severity="fail", slow=False,
              hint="并发越界：>12 是上次被封的直接原因"),

        Probe(id="rt.dead_ratio", title="本轮 dead 占比",
              category="reachability", measure=lambda _c: round(
                  state.get("dead", 0) / max(1, state.get("seen", 1)), 4),
              expect=AtMost(0.9), severity="info", slow=False,
              hint="dead 高属正常（历史失效链接），不代表机制坏了"),

        Probe(id="rt.progress", title="已处理条目数",
              category="coverage", measure=g("seen"),
              expect=AtLeast(0), severity="info", slow=False,
              hint=""),
    ]
