"""link_title 抓取治理的常驻回归（2026-11-02）。

背景：上一轮全量抓取成功率 41.1%（37,070 条 / 9.1 小时），根因是 bilibili 风控：
    11-02 10 时 77.4% -> 12 时 0.3% -> 14 时恢复 57.7%
旧实现把 412（被限流）与 404（页面已死）都塞进 status='failed'，重跑时
把 412 的又打一遍 —— 等于持续激怒对方。

本套件把四条治理能力钉成回归（S19：构造性探查项常驻，不只在验收时临时做）：
  1. 失败分类：404/410->dead, 412/429/403->blocked, 超时->timeout, 无title->no_title
  2. 域名熔断：blocked 立刻熔断；timeout 累积到阈值；dead 不熔断；成功清零
  3. 规范化单一出处：canon_url 只在 linktitle，fetch_titles 是同一对象别名（DL-23）
  4. 落库分组：同规范化组的所有变体一起落库（旧实现只写 members[0]）
"""
from __future__ import annotations

import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from digitallife.render import linktitle


class _H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        p = self.path
        code = {"/404": 404, "/410": 410, "/412": 412, "/429": 429,
                "/403": 403, "/500": 500}.get(p.split("?")[0])
        if code:
            self.send_response(code)
            self.end_headers()
            self.wfile.write(b"x")
            return
        if p.startswith("/slow"):
            time.sleep(3)
            self.send_response(200); self.end_headers()
            self.wfile.write(b"<title>late</title>")
            return
        if p.startswith("/notitle"):
            self.send_response(200); self.end_headers()
            self.wfile.write(b"<html><body>none</body></html>")
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write("<html><head><title>真实标题</title></head></html>".encode())


@pytest.fixture(scope="module")
def base_url():
    srv = HTTPServer(("127.0.0.1", 0), _H)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield "http://127.0.0.1:%d" % port
    srv.shutdown()
    linktitle.close_client()


# ---------------------------------------------------------------- 1. 失败分类

def test_kind_dead_on_404(base_url):
    _t, k = linktitle.fetch_detail(base_url + "/404", timeout=5)
    assert k == linktitle.KIND_DEAD


def test_kind_dead_on_410(base_url):
    _t, k = linktitle.fetch_detail(base_url + "/410", timeout=5)
    assert k == linktitle.KIND_DEAD


@pytest.mark.parametrize("code", ["/412", "/429", "/403"])
def test_kind_blocked_on_ratelimit(base_url, code):
    """412/429/403 是「站点在拒绝我们」，不是「这条 URL 死了」。
    旧实现把它记成 failed，重跑时又打一遍 —— 这是上一轮被封的直接机制。"""
    _t, k = linktitle.fetch_detail(base_url + code, timeout=5)
    assert k == linktitle.KIND_BLOCKED


def test_kind_no_title_when_page_has_none(base_url):
    _t, k = linktitle.fetch_detail(base_url + "/notitle", timeout=5)
    assert k == linktitle.KIND_NO_TITLE


def test_kind_ok_returns_title(base_url):
    t, k = linktitle.fetch_detail(base_url + "/ok", timeout=5)
    assert k == linktitle.KIND_OK
    assert t == "真实标题"


def test_non_http_is_dead(base_url):
    """畸形/非 http URL 永不再试。"""
    _t, k = linktitle.fetch_detail("ftp://example.com/x", timeout=5)
    assert k == linktitle.KIND_DEAD


# ---------------------------------------------------------------- 2. 域名熔断

def test_guard_blocked_trips_immediately():
    """412/429 是对整站的信号，应立刻熔断，不等累积到阈值。"""
    from scripts.fetch_titles import DomainGuard
    g = DomainGuard(block_threshold=5, cooldown=60.0)
    u = "http://bad.example/x"
    assert g.is_open(u) is True
    g.record(u, linktitle.KIND_BLOCKED)
    assert g.is_open(u) is False


def test_guard_isolated_per_domain():
    """一个域熔断不得影响其他域 —— 上一轮 bilibili 拖住了全量 37,070 条。"""
    from scripts.fetch_titles import DomainGuard
    g = DomainGuard(block_threshold=5, cooldown=60.0)
    g.record("http://bad.example/x", linktitle.KIND_BLOCKED)
    assert g.is_open("http://bad.example/y") is False
    assert g.is_open("http://good.example/y") is True


def test_guard_timeout_needs_threshold():
    from scripts.fetch_titles import DomainGuard
    g = DomainGuard(block_threshold=3, cooldown=60.0)
    u = "http://slow.example/x"
    g.record(u, linktitle.KIND_TIMEOUT)
    g.record(u, linktitle.KIND_TIMEOUT)
    assert g.is_open(u) is True          # 未到阈值
    g.record(u, linktitle.KIND_TIMEOUT)
    assert g.is_open(u) is False


def test_guard_dead_does_not_trip():
    """dead/no_title 是页面级结论，不代表站点在拒绝我们。"""
    from scripts.fetch_titles import DomainGuard
    g = DomainGuard(block_threshold=2, cooldown=60.0)
    u = "http://gone.example/x"
    for _ in range(10):
        g.record(u, linktitle.KIND_DEAD)
        g.record(u, linktitle.KIND_NO_TITLE)
    assert g.is_open(u) is True


def test_guard_success_resets():
    from scripts.fetch_titles import DomainGuard
    g = DomainGuard(block_threshold=3, cooldown=60.0)
    u = "http://mix.example/x"
    g.record(u, linktitle.KIND_TIMEOUT)
    g.record(u, linktitle.KIND_TIMEOUT)
    g.record(u, linktitle.KIND_OK)        # 清零
    g.record(u, linktitle.KIND_TIMEOUT)
    g.record(u, linktitle.KIND_TIMEOUT)
    assert g.is_open(u) is True


def test_guard_recovers_after_cooldown():
    from scripts.fetch_titles import DomainGuard
    g = DomainGuard(block_threshold=3, cooldown=0.1)
    u = "http://cd.example/x"
    g.record(u, linktitle.KIND_BLOCKED)
    assert g.is_open(u) is False
    time.sleep(0.2)
    assert g.is_open(u) is True


# ---------------------------------------------------------------- 3. 单一出处

def test_canon_url_single_source():
    """DL-23：规范化实现只能有一处。fetch_titles 的必须是 linktitle 的同一对象，
    否则抓取侧去重与回填侧分组会用两套口径，回填会把标题写到错组。"""
    from scripts import fetch_titles
    assert fetch_titles.canon_url is linktitle.canon_url
    assert fetch_titles._TRACK_PARAMS is linktitle.TRACK_PARAMS


def test_canon_url_strips_tracking():
    got = linktitle.canon_url(
        "https://www.bilibili.com/video/BV1xx/?spm_id_from=333&vd_source=abc")
    assert "spm_id_from" not in got
    assert "vd_source" not in got
    assert "BV1xx" in got


def test_canon_url_keeps_meaningful_params():
    """只去追踪参数，不能把有意义的分页/查询参数一起删掉。"""
    got = linktitle.canon_url("https://x.com/search?q=hello&page=2")
    assert "q=hello" in got
    assert "page=2" in got

# ---------------------------------------------------------------- 4. 断点续跑（验收项 6）

def test_collect_skips_ok_urls(tmpdb):
    """已 status='ok' 的 URL 绝不重回待抓队列。

    2026-11-02 验证器发现的真实缺陷：旧写法
        if st == "ok" and pt and not needs_title(pt, u): continue
    有个漏洞 —— 若某行 ok 但 title 恰好被判 needs_title（如标题就等于域名），
    它就会掉进待抓队列，于是**每轮都被重抓**。这正是「断点续跑失效」的实质。
    修法：status='ok' 一律跳过，不看 title 是否可用。
    """
    from scripts import fetch_titles as FT

    con = tmpdb
    try:
        ok_urls = {r[0] for r in con.execute(
            "SELECT url FROM link_title WHERE status='ok' LIMIT 300")}
        if not ok_urls:
            pytest.skip("库里没有 ok 记录")
        todo = FT.collect(con, retry_failed=True)   # 最激进模式
        in_todo = set()
        for _h, _o, members in todo:
            in_todo.update(members)
        relapse = ok_urls & in_todo
        assert not relapse, "已成功的 URL 又进了待抓队列: %d 个" % len(relapse)
    finally:
        con.close()


# ---------------------------------------------------------------- 5. 缓存零写入（验收项 7）

def test_fetch_does_not_write_disk_cache():
    """抓取链路只写 sqlite，不得碰 tmp/.datacache 与 tmp/.pagecache。

    这是用户明确要求（「别给我本地留一大堆缓存垃圾」），且历史上
    tmp/.titlecache.json 已被废弃（每次建页重抓、跨进程不一致）。
    断言方式：抓一条后两目录的文件数/字节数不变。
    """
    from pathlib import Path

    tmp = Path(__file__).resolve().parents[1] / "tmp"

    def snap():
        out = {}
        for d in (".datacache", ".pagecache"):
            p = tmp / d
            fs = [x for x in p.rglob("*") if x.is_file()] if p.exists() else []
            out[d] = (len(fs), sum(x.stat().st_size for x in fs))
        return out

    before = snap()
    linktitle.fetch_detail("http://127.0.0.1:1/never", timeout=1.0)
    after = snap()
    assert before == after, "抓取写入了磁盘缓存: %r -> %r" % (before, after)

def test_collect_skips_ok_even_if_title_looks_like_domain(tmpdb):
    """构造性回归（S20）：ok 但 title 恰好是域名形态的行，也不得重回待抓。

    这是旧实现的真实缺陷：
        if st == "ok" and pt and not needs_title(pt, u): continue
    —— 若 title 被判 needs_title（如标题就等于主机名），该 ok 行每轮都被重抓。
    实测（2026-11-02）：注入此类行后，旧实现的 collect() 会把它排进待抓（RELAPSE），
    修复后 CLEAN。**触发条件必须真的构造出来**，否则断言恒绿（S58）。
    """
    import sqlite3
    from digitallife import paths
    from scripts import fetch_titles as FT

    probe = "http://hostlike-probe.example/page?utm_source=x"
    con = tmpdb
    try:
        con.execute("DELETE FROM link_title WHERE url=?", (probe,))
        con.execute("DELETE FROM event WHERE url=?", (probe,))
        con.execute(
            "INSERT INTO event(event_id, device, kind, source, start_utc_ms,"
            " end_utc_ms, app, title, url, payload, ingest_run)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            ("S20-%d" % abs(hash(probe)), "pc-main", "web", "shion", 1, 2,
             "chrome", probe, probe, "{}", "s20"))
        # 关键：status='ok' 但 title 恰好是主机名 -> needs_title 为 True
        con.execute(
            "INSERT INTO link_title(url, title, source, status, fetched_at)"
            " VALUES(?,?,?,?,0) ON CONFLICT(url) DO UPDATE SET"
            " title=excluded.title, status=excluded.status",
            (probe, "hostlike-probe.example", "http", "ok"))
        con.commit()

        # 前置断言：触发条件成立（S58：先确认条件真被构造出来）
        assert linktitle.needs_title("hostlike-probe.example", probe), \
            "触发条件未成立，本测试无意义"

        todo = FT.collect(con, retry_failed=True)
        in_todo = set()
        for _h, _o, members in todo:
            in_todo.update(members)
        assert probe not in in_todo, \
            "ok 但标题像域名的行又进了待抓队列（断点续跑失效）"
    finally:
        con.execute("DELETE FROM link_title WHERE url=?", (probe,))
        con.execute("DELETE FROM event WHERE url=?", (probe,))
        con.commit()
        left = con.execute(
            "SELECT COUNT(*) FROM link_title WHERE url=?", (probe,)).fetchone()[0]
        con.close()
        assert left == 0, "测试残留未清理（S16）"

# ---------------------------------------------------------------- 6. event 标题回填（验收项 1/2 核心）

def test_backfill_event_titles_plan(tmpdb):
    """event 表自带可读标题、link_title 无 ok 行的 URL，必须能被回填计划识别。

    这修正了一个口径错误（2026-11-02 验证器第二轮）：
      旧口径只认 link_title.status='ok' 才算「已有标题」，
      但 bilibili 视频页的真标题常常就在 event.title 里（浏览器历史带下来的）：
          url   = https://www.bilibili.com/video/BV18h4y1H7tq/?spm_id_from=...
          title = '鸭 王 争 爸 3'
      渲染 resolve() 第③级用它，页面看着正常 —— 但没落进 link_title。
      任务书说的「同组其他变体没落库」正是这一类（32,024 个，含 bilibili 8,649）。
    """
    from scripts import backfill_event_titles as BET

    probe = "http://event-title-probe.example/video/BVtest123"
    con = tmpdb
    try:
        con.execute("DELETE FROM link_title WHERE url=?", (probe,))
        con.execute("DELETE FROM event WHERE url=?", (probe,))
        con.execute(
            "INSERT INTO event(event_id, device, kind, source, start_utc_ms,"
            " end_utc_ms, app, title, url, payload, ingest_run)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            ("BET-%d" % abs(hash(probe)), "pc-main", "web", "shion", 1, 2,
             "chrome", "真实的视频标题_哔哩哔哩_bilibili", probe, "{}", "bet"))
        con.commit()

        todo = BET.plan(con)
        found = [u for u, _t in todo if u == probe]
        assert found, "event 自带可读标题的 URL 未被回填计划识别"

        # 落库后不该再被计划
        from digitallife.render import linktitle as LT
        LT.load_from_db(con)
        LT.save(con, probe, "真实的视频标题_哔哩哔哩_bilibili", source="event", status="ok")
        con.commit()
        todo2 = BET.plan(con)
        assert probe not in [u for u, _t in todo2], "已回填的仍被列入计划"
    finally:
        con.execute("DELETE FROM link_title WHERE url=?", (probe,))
        con.execute("DELETE FROM event WHERE url=?", (probe,))
        con.commit()
        left = con.execute(
            "SELECT COUNT(*) FROM link_title WHERE url=?", (probe,)).fetchone()[0]
        con.close()
        assert left == 0, "测试残留未清理（S16）"


def test_bilibili_video_url_renders_db_title(tmpdb):
    """构造性回归：bilibili 视频页在回填后必须由 resolve() 走 'db' 来源，
    而不是退化成 'bilibili.com · BVxxx'（验收项 2 的机器可验版本）。"""
    from digitallife.render import linktitle as LT

    probe = "http://bili-render-probe.example/video/BVprobe999"
    con = tmpdb
    try:
        con.execute("DELETE FROM link_title WHERE url=?", (probe,))
        # 回填前：无记录 -> 应退化成 url 来源
        LT.load_from_db(con)
        before, before_src = LT.resolve("", probe)
        assert before_src == "url", "回填前应走 url 来源，实际 %s" % before_src
        assert "·" in before, "回填前应是「域名 · 路径」形态，实际 %r" % before

        # 回填后：应走 db 来源
        LT.save(con, probe, "真实的视频标题_哔哩哔哩_bilibili",
                source="event", status="ok")
        con.commit()
        LT.load_from_db(con)
        after, after_src = LT.resolve("", probe)
        assert after_src == "db", "回填后应走 db 来源，实际 %s" % after_src
        assert after == "真实的视频标题_哔哩哔哩_bilibili"
        assert after != before
    finally:
        con.execute("DELETE FROM link_title WHERE url=?", (probe,))
        con.commit()
        con.close()

# ---------------------------------------------------------------- 7. 通用检测内核（linkhealth）

def test_expect_all_kinds_judge_correctly():
    """判据本体自证：每种 Expect 都能判对，也能判错。

    一个永远返回 ok 的检测等于没检测 —— 这里逐条验证「该红时真红」。
    """
    from digitallife.render.linkhealth import (
        AtLeast, AtMost, Between, Equals, Truthy,
    )

    assert AtLeast(5).check(10)[0] is True
    assert AtLeast(5).check(3)[0] is False
    assert AtMost(5).check(3)[0] is True
    assert AtMost(5).check(10)[0] is False
    assert Between(1, 12).check(6)[0] is True
    assert Between(1, 12).check(20)[0] is False
    assert Equals("a").check("a")[0] is True
    assert Equals("a").check("b")[0] is False
    assert Truthy().check(1)[0] is True
    assert Truthy().check(0)[0] is False
    # 非数值输入不得抛异常，应判假
    assert AtLeast(1).check("abc")[0] is False


def test_probe_exception_does_not_break_batch():
    """单条 Probe 抛异常时，整批不崩，该条记失败并带异常名。"""
    from digitallife.render.linkhealth import AtLeast, Probe, run

    bad = Probe(id="t.boom", title="故意抛", category="integrity",
                measure=lambda c: 1 / 0, expect=AtLeast(1), severity="fail")
    good = Probe(id="t.ok", title="正常", category="integrity",
                 measure=lambda c: 5, expect=AtLeast(1), severity="fail")
    rep = run([bad, good], fast=True)
    assert len(rep.results) == 2
    assert rep.results[0].passed is False
    assert "ZeroDivisionError" in rep.results[0].detail
    assert rep.results[1].passed is True          # 后面的照常跑


def test_fast_skips_slow_probes():
    """快检跳过 slow 项，全检包含 —— 判据同源，深度不同。"""
    from digitallife.render.linkhealth import Probe, Truthy, run

    slow = Probe(id="t.slow", title="慢项", category="coverage",
                 measure=lambda c: 1, expect=Truthy(), slow=True)
    fast_p = Probe(id="t.fast", title="快项", category="coverage",
                   measure=lambda c: 1, expect=Truthy(), slow=False)

    r_fast = run([slow, fast_p], fast=True)
    ids_fast = {r.probe_id for r in r_fast.results}
    assert "t.slow" not in ids_fast
    assert "t.fast" in ids_fast

    r_full = run([slow, fast_p], fast=False)
    ids_full = {r.probe_id for r in r_full.results}
    assert "t.slow" in ids_full
    assert "t.fast" in ids_full


def test_report_vetoes_is_structured():
    """vetoes() 给出结构化结论供 Strategy 读 —— 检测自己不做事。"""
    from digitallife.render.linkhealth import AtLeast, AtMost, Probe, run

    ps = [
        Probe(id="a.low", title="不够", category="coverage",
              measure=lambda c: 1, expect=AtLeast(10), severity="fail",
              hint="该补数据"),
        Probe(id="a.fine", title="够", category="coverage",
              measure=lambda c: 100, expect=AtLeast(10), severity="fail"),
    ]
    rep = run(ps, fast=True)
    v = rep.vetoes()
    assert "a.low" in v
    assert "a.fine" not in v
    assert v["a.low"]["hint"] == "该补数据"
    assert rep.passed is False


def test_warn_does_not_affect_passed():
    """warn 级不通过不影响整体 passed（只有 fail 级才挡）。"""
    from digitallife.render.linkhealth import AtLeast, Probe, run

    ps = [
        Probe(id="w.x", title="警告项", category="coverage",
              measure=lambda c: 0, expect=AtLeast(5), severity="warn"),
        Probe(id="f.y", title="失败项", category="integrity",
              measure=lambda c: 10, expect=AtLeast(5), severity="fail"),
    ]
    rep = run(ps, fast=True)
    assert rep.passed is True                 # warn 不挡
    assert len(rep.warned) == 1
    # 一旦 fail 级也坏，就挡
    ps[1] = Probe(id="f.y", title="失败项", category="integrity",
                  measure=lambda c: 1, expect=AtLeast(5), severity="fail")
    assert run(ps, fast=True).passed is False


def test_counts_are_comparable_across_runs(tmpdb):
    """counts() 结构稳定 —— 改动前后能直接对拍。"""
    from digitallife.render.linkhealth import run
    from digitallife.render import linkprobes

    import sqlite3
    from digitallife import paths

    con = tmpdb
    try:
        r1 = run(linkprobes.all_probes(), ctx=con, fast=True)
        r2 = run(linkprobes.all_probes(), ctx=con, fast=True)
    finally:
        con.close()
    assert set(r1.counts()) == set(r2.counts())
    assert r1.counts() == r2.counts()          # 同条件两次取值相同


def test_runtime_probes_flag_bad_state():
    """运行期探针：并发越界/被拒过多时必须给出 veto。"""
    from digitallife.render.linkhealth import run
    from digitallife.render import linkprobes

    bad = {"seen": 100, "blocked": 40, "dead": 5,
           "cur_workers": 20, "blocked_streak": 6}
    rep = run(linkprobes.runtime_probes(bad), fast=True)
    v = rep.vetoes()
    assert "rt.workers" in v            # 并发 20 越界
    assert "rt.blocked_streak" in v     # 连续被拒 6 > 3
    assert "rt.blocked_ratio" in v      # 0.4 > 0.3
    assert rep.passed is False

    good = {"seen": 100, "blocked": 1, "dead": 5,
            "cur_workers": 4, "blocked_streak": 1}
    assert run(linkprobes.runtime_probes(good), fast=True).passed is True


def test_probe_definitions_are_unique():
    """Probe id 必须唯一 —— 报告与对拍都靠 id。"""
    from digitallife.render import linkprobes

    ids = [p.id for p in linkprobes.all_probes()]
    assert len(ids) == len(set(ids)), "Probe id 重复: %s" % ids


def test_health_strategy_reads_vetoes_not_decides():
    """Strategy 读检测结论行动；检测本身不改变任何状态（分层）。"""
    from digitallife.render.linkhealth import AtLeast, Probe, run

    ps = [Probe(id="rt.workers", title="并发", category="policy",
                measure=lambda c: 20, expect=AtLeast(0), severity="fail")]
    rep = run(ps, fast=True)
    # 检测只产出结论，不动任何外部对象
    assert rep.vetoes() == {} or "rt.workers" not in rep.vetoes()  # 已通过
    # 构造一个真的越界的
    ps2 = [Probe(id="rt.workers", title="并发", category="policy",
                 measure=lambda c: 20, expect=AtLeast(100), severity="fail")]
    rep2 = run(ps2, fast=True)
    assert "rt.workers" in rep2.vetoes()
