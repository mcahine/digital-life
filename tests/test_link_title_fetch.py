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
