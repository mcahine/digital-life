"""网页标题解析 —— **单一真相函数**（resolve）。

为什么需要（2026-10-24 用户要求）：
  时段页时间线里很多行锚文本只有 `message.bilibili.com` 这种域名，
  读的人不知道那是视频、私信还是专栏。

为什么不只靠"原样显示"（2026-10-25 用户第二条诉求）：
  时间线用了本模块，网站 section 却自己走「自己的 title → 借同窗窗口标题 →
  域名·路径末段」三套降级链 ——**同一个 URL 在两处显示不同文本**。
  用户原话：「网站和时间线的链接要保持同步，不能时间线上已经有标题显示，
  但是你的网站还没有标题显示」。所以这里收敛成**唯一的 resolve()**，
  timeine / web 两处都只调它，任何一侧都不许再写降级链。

真相来源（按优先级，两处必须同序，否则又不一致）：
  1. link_title 表里 status='ok' 的抓取结果（网页本身的标题，最权威）
  2. **页面级索引** page_index：同页内 `link_url -> 行锚文本`（由编排器从
     合并后的时间线一次性建好）。它保证「时间线能显示的，网站也一定能显示」。
  3. 调用方给的原始标题（非域名形态才可用）
  4. 同时间窗的浏览器窗口标题（页面级索引覆盖不到的零散行）
  5. URL 压成的 `域名 · 路径末段`（最后兜底，绝不报错）

落库（2026-10-25，替代磁盘缓存 tmp/.titlecache.json）：
  抓到的标题写 link_title 表；**status='failed' 也落行**，否则每次建页都会
  重抓同一个抓不到的地址。渲染期**绝不联网** —— 网络只在 scripts/fetch_titles.py。

性能（DL-9）：整表一次装进内存，逐 URL 不查库。表很小（本机 122 条待抓量级），
753 页重建时这点开销可忽略；反过来逐行 SELECT 会是 750k 次查询。
"""
from __future__ import annotations

import re
import sqlite3
import threading
import urllib.request
from urllib.parse import urlsplit

# 表很小，整表常驻内存；进程内线程安全（多进程各自一份，天然隔离）。
_lock = threading.Lock()
_mem: dict[str, dict] | None = None
_loaded_from = ""

# 明显是「域名当标题」的形态：不含空格、含点、像个主机名
_HOSTLIKE = re.compile(r"^[a-z0-9][a-z0-9.\-]*\.[a-z]{2,}(/.*)?$", re.I)


# ---------------------------------------------------------------- 基础判定

def host_of(url: str) -> str:
    try:
        h = urlsplit(str(url or "").strip()).netloc.lower()
    except ValueError:
        return ""
    h = h.split("@")[-1].split(":")[0]
    return h[4:] if h.startswith("www.") else h


def is_hostlike(title: str, url: str = "") -> bool:
    """标题是否没有信息量（空 / 纯主机名 / 等于本 URL 的域名）。"""
    t = str(title or "").strip()
    if not t:
        return True
    if " " in t:
        return False
    if _HOSTLIKE.match(t):
        return True
    h = host_of(url)
    return bool(h) and t.lower().replace("www.", "") == h


def is_url(s: str) -> bool:
    return str(s or "").strip().startswith(("http://", "https://"))


# 纯追踪参数：去掉后仍指向同一页面，不影响落地内容。
# 2026-10-30 实测：49,580 个待抓 URL 里，仅靠规范化就能合并掉 8,600 个（17.3%）。
# 2026-11-02：从 scripts/fetch_titles.py **移入本模块**（DL-23 单一出处）——
#   抓取侧去重与回填侧分组必须用同一个规范化实现，否则两边分组不同、
#   回填会把标题写错组。fetch_titles 保留同名别名（指向同一对象，不是副本）。
TRACK_PARAMS = frozenset({
    "spm_id_from", "vd_source", "from_source", "share_source", "share_medium",
    "bbid", "seid", "spm", "tab", "ts",
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "utm_id",
    "sca_esv", "sxsrf", "ved", "ei", "uact", "oq", "gs_lp", "sclient", "biw", "bih",
})


def canon_url(u: str) -> str:
    """URL 规范化：去追踪参数、去尾斜杠、去 fragment。失败返回原串。

    **不能把规范化后的 URL 当落库键**：页面链接用的是原始 URL，
    改了键渲染时就匹配不上。用途只有两个：
      1. 抓取队列里「同一页面的不同追踪变体」只出现一次；
      2. 回填时把同一组变体认出来，让一条成功标题覆盖全组。
    """
    raw = str(u or "").strip()
    try:
        from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

        p = urlsplit(raw)
        if not p.netloc:
            return raw
        q = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
             if k.lower() not in TRACK_PARAMS]
        return urlunsplit((p.scheme, p.netloc, (p.path or "").rstrip("/"),
                           urlencode(q), ""))
    except Exception:  # noqa: BLE001 —— 畸形 URL 原样返回，由 fetch 去拒
        return raw


def needs_title(title: str, url: str) -> bool:
    """这个 URL 是否**真的**缺一个可读标题（要不要去抓）。

    判据比 is_hostlike 严一档，因为实测（2026-10-25）发现最大的一类缺口不是
    域名形态，而是 **title 字段里直接存了 URL 本身**：

        url  = http://chat.deepseek.com/
        title= http://chat.deepseek.com/          ← 和域名形态一样没有信息量

    按 is_hostlike 统计只有 71 个 URL；按「有没有可读标题」统计是 47,657 个
    （占 59.8%，访问量占 64.1%）。只按域名口径去抓，抓完了页面还是不好看。

    三种都算缺：空 / 纯域名 / 就是 URL（或 URL 的可读形态）。
    """
    u = str(url or "").strip()
    t = str(title or "").strip()
    if not t:
        return True
    if is_hostlike(t, u):
        return True
    if t == u or is_url(t):
        return True
    return bool(u) and t == readable_from_url(u)


def readable_from_url(url: str) -> str:
    """把 URL 压成可读短串：`域名 · 路径末段`（去掉追踪参数）。失败返回原串。"""
    raw = str(url or "").strip()
    try:
        p = urlsplit(raw)
    except ValueError:
        return raw
    host = p.netloc.split("@")[-1].split(":")[0].lower()
    if host.startswith("www."):
        host = host[4:]
    path = (p.path or "").strip("/")
    tail = path.rsplit("/", 1)[-1] if path else ""
    if len(tail) > 42:
        tail = tail[:39] + "…"
    return "%s · %s" % (host, tail) if tail else (host or raw)


# ---------------------------------------------------------------- 数据库层

def _connect():
    """惰性自开连接（只读用途）。调用方也可以显式 load_from_db(con)。"""
    from digitallife import paths
    con = sqlite3.connect(str(paths.db_path()), timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout=30000")
    return con


def load_from_db(con: sqlite3.Connection | None = None) -> int:
    """把 link_title 整表装进内存。返回行数。表不存在时安静返回 0。

    表不存在（老库还没跑 migrate）时**不抛异常**：建页不能因为少了张辅助表
    就整体失败 —— 那只该表现为"标题退回域名"，而不是 753 页全红。
    """
    global _mem, _loaded_from
    own = con is None
    if own:
        con = _connect()
    try:
        rows = con.execute(
            "SELECT url, title, source, status FROM link_title").fetchall()
    except sqlite3.OperationalError:
        rows = []
    finally:
        if own:
            con.close()
    d = {}
    for r in rows:
        try:
            d[str(r["url"])] = {"title": str(r["title"] or ""),
                                "source": str(r["source"] or ""),
                                "status": str(r["status"] or "")}
        except (IndexError, KeyError):
            d[str(r[0])] = {"title": str(r[1] or ""), "source": "http", "status": "ok"}
    with _lock:
        _mem = d
        _loaded_from = "db"
    return len(d)


def _db() -> dict[str, dict]:
    if _mem is None:
        load_from_db()
    return _mem or {}


def invalidate() -> None:
    """清内存缓存（批量抓取后重新 load 用）。"""
    global _mem
    with _lock:
        _mem = None


def db_title(url: str) -> str:
    """库里 status='ok' 的标题；failed / 缺失一律返回空串。"""
    rec = _db().get(str(url or "").strip()) or {}
    if rec.get("status") != "ok":
        return ""
    t = str(rec.get("title") or "").strip()
    return "" if is_hostlike(t) else t


def save(con: sqlite3.Connection, url: str, title: str, *, source: str = "http",
         status: str = "ok", failure_kind: str = "") -> None:
    """写一行并同步内存（建页读的是内存）。**不去重冲突**：后写覆盖前写。

    failure_kind（2026-11-02）：失败原因分类，见上方 KIND_* 常量。
    成功时传空串。它决定**下次还抓不抓**：
      dead / no_title -> collect() 永不再试
      blocked         -> 等熔断冷却后再试
      timeout         -> 限次重试
    attempts 每次落库自增（成功也算一次），供「超次数就放弃」使用。
    """
    u = str(url or "").strip()
    if not u:
        return
    import time
    con.execute(
        "INSERT INTO link_title(url, title, source, status, fetched_at, "
        "                      failure_kind, attempts) "
        "VALUES(?,?,?,?,?,?,1) ON CONFLICT(url) DO UPDATE SET "
        "title=excluded.title, source=excluded.source, "
        "status=excluded.status, fetched_at=excluded.fetched_at, "
        "failure_kind=excluded.failure_kind, "
        "attempts=link_title.attempts+1",
        (u, str(title or "")[:300], source, status, int(time.time()),
         str(failure_kind or "")))
    d = _db()
    with _lock:
        d[u] = {"title": str(title or "")[:300], "source": source,
                "status": status, "failure_kind": str(failure_kind or "")}


# ---------------------------------------------------------------- 抓取（仅 fetch_titles.py 调）

# 真实浏览器 UA。2026-10-30 实测：bilibili 对非浏览器 UA 会返回精简页。
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

# ---------------------------------------------------------------- 失败分类
# 2026-11-02：早期把一切失败都塞进 status='failed'，导致
#   412（被限流）与 404（页面已死）混在一起，重跑时把 412 的又打一遍，
#   等于持续激怒对方。分四类，各有处置：
#     blocked  412/429/403 —— 不是「这条 URL 的失败」，是该站正在拒绝我们。
#                            应熔断该域名、冷却后重试，不计入永久失败。
#     dead     404/410     —— 页面确实不存在，永不再抓。
#     timeout  超时/连接错 —— 可重试，但有次数上限。
#     no_title 200 但无标题 —— 页面本来就没 title，永不再抓。
KIND_OK = "ok"
KIND_BLOCKED = "blocked"
KIND_DEAD = "dead"
KIND_TIMEOUT = "timeout"
KIND_NO_TITLE = "no_title"

# blocked 的 HTTP 状态码：412 是 bilibili 风控的固定回应（实测），429 是通用限流。
_BLOCKED_CODES = frozenset({403, 412, 418, 429})
_DEAD_CODES = frozenset({404, 410, 451})

# 进程级共享 Client。
# 2026-11-02 实测：每条 URL 新建 Client 时 4.05 s/条，复用后 0.86 s/条 = 4.71x。
# 差别来自 TLS 握手与连接池重建，与 UA/压缩协商无关。
# 线程安全：httpx.Client 自身线程安全，可被 ThreadPoolExecutor 共享。
_client = None
_client_lock = threading.Lock()


def _get_client(timeout: float = 6.0):
    """取（或建）进程级 httpx.Client。建不出来返回 None。"""
    global _client
    if _client is not None:
        return _client
    with _client_lock:
        if _client is not None:
            return _client
        try:
            import httpx

            _client = httpx.Client(
                headers={"User-Agent": _UA},
                timeout=httpx.Timeout(timeout, connect=min(5.0, timeout)),
                limits=httpx.Limits(max_connections=16,
                                    max_keepalive_connections=16),
                follow_redirects=True,
            )
            return _client
        except Exception:  # noqa: BLE001
            return None


def close_client() -> None:
    """关掉共享 Client（脚本收尾用；进程退出时自动回收）。"""
    global _client
    with _client_lock:
        if _client is not None:
            try:
                _client.close()
            except Exception:  # noqa: BLE001
                pass
            _client = None


def fetch_detail(url: str, timeout: float = 6.0) -> tuple:
    """抓标题并返回失败分类：(title, kind)。

    kind 取值见上方常量。成功时 title 非空且 kind == KIND_OK。
    这是抓取侧真正该用的函数；fetch() 仅为兼容旧调用保留。
    """
    u = str(url or "").strip()
    if not u.startswith(("http://", "https://")):
        return "", KIND_DEAD          # 畸形/非 http，永不再试
    cli = _get_client(timeout)
    if cli is None:
        return "", KIND_TIMEOUT
    try:
        r = cli.get(u)
    except Exception:  # noqa: BLE001
        # 超时与连接类错误都算可重试；其余也按可重试处理（不判死）
        return "", KIND_TIMEOUT
    if r.status_code in _BLOCKED_CODES:
        return "", KIND_BLOCKED
    if r.status_code in _DEAD_CODES:
        return "", KIND_DEAD
    if r.status_code >= 400:
        return "", KIND_DEAD
    try:
        html = r.text
    except Exception:  # noqa: BLE001
        return "", KIND_TIMEOUT
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I)
    if not m:
        return "", KIND_NO_TITLE
    t = re.sub(r"\s+", " ", m.group(1)).strip()[:300]
    # 抓到「出错啦」「403 Forbidden」这类风控页标题也算没抓到
    if not t or is_hostlike(t, u):
        return "", KIND_NO_TITLE
    return t, KIND_OK


def fetch(url: str, timeout: float = 6.0) -> str:
    """联网抓 <title>。失败返回空串，绝不抛异常（建页/批处理不能因网络失败）。

    **为什么用 httpx 而不是 urllib**（2026-10-30 实测，同 UA 同 URL）：
        urllib  -> 352,359 B，返回简化页，取不到 <title>
        httpx   -> 2,353,573 B，title 正常
    差别在 HTTP 实现自身（连接/头/压缩协商），不是 UA。

    2026-11-02：改用进程级共享 Client（实测 4.05 s -> 0.86 s/条）。
    签名与「失败返回空串」契约不变；需要失败原因时用 fetch_detail()。
    """
    return fetch_detail(url, timeout)[0]


# ---------------------------------------------------------------- 单一真相

def page_index(pairs) -> dict[str, str]:
    """从 `[(url, 锚文本), ...]` 建 URL 级索引，**只收有信息量的**。

    编排器在合并后的时间线上调一次：一个 URL 若在时间线上有可读锚文本，
    网站 section 就必须显示**同一个字符串**。域名形态的值一律丢弃 ——
    收进去只会把"网站侧没有标题"从一个地方搬到另一个地方。
    """
    out: dict[str, str] = {}
    for url, text in pairs or []:
        u = str(url or "").strip()
        t = str(text or "").strip()
        if not u or not t or is_hostlike(t, u):
            continue
        if u not in out:
            out[u] = t
    return out


def resolve(raw_title: str, url: str, *, window_title: str = "",
            index: dict | None = None) -> tuple[str, str]:
    """**唯一**的「这个链接该显示什么」函数。返回 (显示文本, 来源标记)。

    来源标记用于审计与验收断言：'db' / 'index' / 'raw' / 'window' / 'url'。
    时间线（timeline.py）与网站（web.py）**只能**调这个函数，
    禁止任何一侧再自建降级链 —— 那正是两处不同步的根因（2026-10-25）。
    """
    u = str(url or "").strip()
    t = str(raw_title or "").strip()
    w = str(window_title or "").strip()

    # ① 库里抓到的网页真实标题（最权威：那是对网页本身的描述）
    if u:
        d = db_title(u)
        if d:
            return d, "db"
        # ② 页面级索引：时间线能显示的，网站必须同样显示
        if index:
            v = str(index.get(u) or "").strip()
            if v and not is_hostlike(v, u):
                return v, "index"

    # ③ 原始标题（既不是域名形态、也不是 URL、也不是 URL 的可读形态）
    if t and not needs_title(t, u):
        return t, "raw"

    # ④ 同时间窗的浏览器窗口标题
    if w and not is_hostlike(w, u) and not is_url(w):
        return w, "window"

    # ⑤ 兜底：URL 可读形态。**永远不返回空**，也不报错。
    if u:
        return readable_from_url(u), "url"
    return t, "raw"
