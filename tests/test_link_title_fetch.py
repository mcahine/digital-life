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

def test_collect_skips_ok_urls():
    """已 status='ok' 的 URL 绝不重回待抓队列。

    2026-11-02 验证器发现的真实缺陷：旧写法
        if st == "ok" and pt and not needs_title(pt, u): continue
    有个漏洞 —— 若某行 ok 但 title 恰好被判 needs_title（如标题就等于域名），
    它就会掉进待抓队列，于是**每轮都被重抓**。这正是「断点续跑失效」的实质。
    修法：status='ok' 一律跳过，不看 title 是否可用。
    """
    import sqlite3

    from digitallife import paths
    from scripts import fetch_titles as FT

    con = sqlite3.connect(str(paths.db_path()))
    con.row_factory = sqlite3.Row
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

def test_collect_skips_ok_even_if_title_looks_like_domain():
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
    con = sqlite3.connect(str(paths.db_path()))
    con.row_factory = sqlite3.Row
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
