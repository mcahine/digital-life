"""日层汇总 —— 只产「日」这一层（R4）。

落库：daily_summary(date_key=YYYY-MM-DD)，只此一层；周/月/季/年由日记系统（second-brain）
自行滚动，本模块不写 04-Areas/GTD/50-回顾/，也不产出任何周期粒度。

跨设备：pc 与 phone 各自一份 device_breakdown，时长分设备记账、绝不相加为总时长
（同一天两设备时间并行，相加即重复计入）。

dbview 尚未就绪时降级为「纯 event 表」结构（ImportError 兜底），口径与 dbview 对齐：
away / locked 区间从活跃时长中剔除（C13）。
"""
from __future__ import annotations

import json
import re
import sqlite3
import time
from datetime import datetime

from .model import DEVICES
from .timeline_merge import display_title, merge_browser_web, page_key, span_seconds

LAYER = "day"
LAYERS = (LAYER,)  # 唯一允许的粒度；本模块不产出 week/month/quarter/year

_MIN_FOCUS_S = 15 * 60
_GAP_MERGE_MS = 120_000
_TIMELINE_MIN_S = 120.0  # 日报只留单段 >=2 分钟的事件；碎片折叠，完整版进时段网页
_TIMELINE_LIMIT = 4000   # 兜底上限（正常远达不到）
_TIMELINE_MIN_S = 120.0  # 日报只留单段 >=2 分钟的事件；碎片折叠，完整版进时段网页
_SUMMARY_LIMIT = 400     # 视觉总结全量
_KEY_ACTION_LIMIT = 30

_DEFAULT_TOTALS = {"active_s": 0, "away_s": 0, "locked_s": 0}

_BT = chr(96)  # markdown 反引号；源码里不写裸字符，避免编辑期被误替换


# ---------------------------------------------------------------- 时间与格式化

def normalize_date(date_key: str) -> str:
    s = (date_key or "").strip()
    try:
        return datetime.strptime(s, "%Y-%m-%d").strftime("%Y-%m-%d")
    except ValueError as e:
        raise ValueError(f"date_key 必须是 YYYY-MM-DD，收到 {date_key!r}") from e


def day_window(date_key: str) -> tuple[int, int]:
    """本地日 → [lo_ms, hi_ms) 的 UTC 毫秒区间。"""
    from . import tz

    d = tz.parse_local(f"{normalize_date(date_key)} 00:00:00")
    lo = tz.local_to_utc_ms(d)
    return lo, lo + 86_400_000


def _hms(ms: int) -> str:
    from . import tz

    return tz.utc_ms_to_local(int(ms)).strftime("%H:%M:%S")


def _hm(ms: int) -> str:
    from . import tz

    return tz.utc_ms_to_local(int(ms)).strftime("%H:%M")


def _fmt_dur(seconds) -> str:
    s = int(seconds or 0)
    if s <= 0:
        return "0 分"
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    if h:
        return f"{h} 小时 {m} 分"
    if m:
        return f"{m} 分"
    return f"{sec} 秒"


def _cell(v) -> str:
    return str(v if v is not None else "").replace("|", "/").replace("\n", " ").strip()


def _parse_json(s):
    if not s:
        return None
    if isinstance(s, (dict, list)):
        return s
    try:
        return json.loads(s)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------- 区间运算

def _clip(iv: tuple[int, int], lo: int, hi: int) -> tuple[int, int] | None:
    s, e = max(iv[0], lo), min(iv[1], hi)
    return (s, e) if e > s else None


def _merge(ints: list[tuple[int, int]]) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for s, e in sorted(ints):
        if out and s <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], e))
        else:
            out.append((s, e))
    return out


def _total(ints: list[tuple[int, int]]) -> int:
    return sum(e - s for s, e in ints)


def _intersect(a: list[tuple[int, int]], b: list[tuple[int, int]]) -> list[tuple[int, int]]:
    res: list[tuple[int, int]] = []
    i = j = 0
    while i < len(a) and j < len(b):
        s, e = max(a[i][0], b[j][0]), min(a[i][1], b[j][1])
        if e > s:
            res.append((s, e))
        if a[i][1] < b[j][1]:
            i += 1
        else:
            j += 1
    return res


def _classify_power(title: str | None) -> str | None:
    """power 事件 → 'locked' / 'away' / None（Active 不算离开，不从时长里剔除）。"""
    t = (title or "").strip().lower()
    if not t:
        return None
    if "lock" in t or "锁屏" in t or "锁定" in t:
        return "locked"
    if "away" in t or "离开" in t or "idle" in t:
        return "away"
    return None


# ---------------------------------------------------------------- 当日结构

def _fresh_structure(date_key: str, device: str) -> dict:
    return {"date": date_key, "device": device, "apps": [], "timeline": [],
            "switch_count": 0, "focus_blocks": [], "away": [], "locked": [],
            "screens": [], "totals": dict(_DEFAULT_TOTALS)}


def _focus_blocks(rows, lo: int, hi: int) -> list[dict]:
    """同一 app 连续 ≥15 分钟的使用段（一律按毫秒算，不按展示串）。"""
    blocks: list[dict] = []
    run_key = None
    run_start = None
    run_end = None

    def _flush() -> None:
        if run_key is not None and run_start is not None and run_end is not None:
            secs = int((run_end - run_start) // 1000)
            if secs >= _MIN_FOCUS_S:
                blocks.append({"start": _hms(run_start), "end": _hms(run_end),
                               "app": run_key, "seconds": secs})

    for r in rows:
        if r["kind"] not in ("app", "web"):
            continue
        iv = _clip((int(r["start_utc_ms"]), int(r["end_utc_ms"] or r["start_utc_ms"])), lo, hi)
        if iv is None:
            continue
        st, en = iv
        key = r["app"] or r["title"] or "(未知)"
        if key == run_key and run_end is not None and st - run_end <= _GAP_MERGE_MS:
            run_end = max(run_end, en)
            continue
        _flush()
        run_key, run_start, run_end = key, st, en
    _flush()
    return blocks


def _structure_fallback(con, date_key: str, device: str) -> dict:
    """纯 event 表实现（dbview 未就绪时的降级路径），口径对齐 dbview。"""
    from . import store

    lo, hi = day_window(date_key)
    rows = store.events_between(con, lo, hi, device=device)
    apps: dict[str, dict] = {}
    timeline: list[dict] = []
    away_iv: list[tuple[int, int]] = []
    locked_iv: list[tuple[int, int]] = []
    app_iv: list[tuple[int, int]] = []
    away_list: list[dict] = []
    locked_list: list[dict] = []
    screens: list[dict] = []
    switch = 0
    prev_key = None

    for r in rows:
        st = int(r["start_utc_ms"])
        en = int(r["end_utc_ms"] or r["start_utc_ms"])
        kind = r["kind"]
        if kind == "power":
            cls = _classify_power(r["title"])
            if cls == "away":
                away_iv.append((st, en))
                away_list.append({"start": _hms(st), "end": _hms(en), "reason": r["title"]})
            elif cls == "locked":
                locked_iv.append((st, en))
                locked_list.append({"start": _hms(st), "end": _hms(en), "reason": r["title"]})
            continue
        if kind == "screen":
            screens.append({"event_id": r["event_id"], "path": r["path"], "ts": _hms(st)})
            continue
        if kind in ("app", "web"):
            iv = _clip((st, en), lo, hi)
            if iv:
                app_iv.append(iv)
            key = r["app"] or r["title"] or "(未知)"
            d = apps.get(key)
            if d is None:
                d = apps[key] = {"app": r["app"] or key, "app_id": r["app_id"],
                                 "seconds": 0, "sessions": 0}
            d["seconds"] += max(0, (en - st) // 1000)
            d["sessions"] += 1
            timeline.append({"start": _hms(st), "end": _hms(en), "app": r["app"],
                             "title": r["title"], "url": r["url"]})
            if prev_key is not None and key != prev_key:
                switch += 1
            prev_key = key

    merged_app = _merge(app_iv)
    excluded = _merge(away_iv + locked_iv)
    active_ms = max(0, _total(merged_app) - _total(_intersect(merged_app, excluded)))

    st_out = _fresh_structure(date_key, device)
    st_out.update({
        "apps": sorted(apps.values(), key=lambda a: (-a["seconds"], a["app"] or "")),
        "timeline": timeline,
        "switch_count": switch,
        "focus_blocks": _focus_blocks(rows, lo, hi),
        "away": away_list,
        "locked": locked_list,
        "screens": screens,
        "totals": {
            "active_s": active_ms // 1000,
            "away_s": _total(_merge(away_iv)) // 1000,
            "locked_s": _total(_merge(locked_iv)) // 1000,
        },
        "source_basis": "event",
    })
    return st_out


def device_structure(con, date_key: str, device: str) -> dict:
    """优先 dbview.build_day_structure；未就绪（ImportError）时走纯 event 降级。"""
    build_day_structure = None
    try:
        from .dbview import build_day_structure  # type: ignore[no-redef]
    except ImportError:
        build_day_structure = None

    data = None
    if build_day_structure is not None:
        try:
            got = build_day_structure(con, date_key, device)
        except Exception:  # noqa: BLE001 —— dbview 故障不得让日报整条崩掉
            got = None
        if isinstance(got, dict):
            data = got

    if data is None:
        return _structure_fallback(con, date_key, device)

    data.setdefault("date", date_key)
    data["device"] = data.get("device") or device
    data.setdefault("apps", [])
    data.setdefault("timeline", [])
    data.setdefault("switch_count", 0)
    data.setdefault("focus_blocks", [])
    data.setdefault("away", [])
    data.setdefault("locked", [])
    data.setdefault("screens", [])
    totals = data.get("totals")
    if not isinstance(totals, dict):
        totals = {}
    for k, v in _DEFAULT_TOTALS.items():
        totals.setdefault(k, v)
    data["totals"] = totals
    data["source_basis"] = "dbview"
    return data


# ---------------------------------------------------------------- 当日切片

def slices_for_day(con, date_key: str, device: str | None = None) -> list[dict]:
    lo, hi = day_window(date_key)
    sql = ("SELECT s.slice_id, s.device, s.start_utc_ms, s.end_utc_ms, s.image_count, "
           "s.event_count, s.status, r.overall_summary, r.structured_json, r.conflict_count "
           "FROM slice s LEFT JOIN slice_result r ON r.slice_id = s.slice_id "
           "WHERE s.start_utc_ms >= ? AND s.start_utc_ms < ?")
    args: list = [lo, hi]
    if device:
        from . import devices as _dv
        clause, aliases = _dv.sql_device_filter(device, "s.device")
        sql += " AND " + clause
        args.extend(aliases)
    sql += " ORDER BY s.start_utc_ms, s.device"
    out: list[dict] = []
    # 一次性取出本日所有「带画面描述」的 screen 事件起始时间（毫秒）。
    # 逐片查库会退化成「每片扫全表 screen 事件」，实测单日要 70–103 秒。
    desc_ts = [row[0] for row in con.execute(
        "SELECT start_utc_ms FROM event "
        "WHERE start_utc_ms >= ? AND start_utc_ms < ? AND kind='screen' "
        "AND payload LIKE '%\"description\"%'", (lo, hi))]
    for r in con.execute(sql, args):
        d = dict(r)
        d["structured"] = _parse_json(d.get("structured_json"))
        # 画面描述的真值在事件层。用批量取出的时间点判断，不逐片查库
        # （逐片查会退化成每片扫全表，实测单日 70–103 秒）。
        _ss = int(d["start_utc_ms"]); _ee = int(d["end_utc_ms"])
        d["has_description"] = any(_ss <= t < _ee for t in desc_ts)
        d["start"] = _hm(d["start_utc_ms"])
        d["end"] = _hm(d["end_utc_ms"])
        out.append(d)
    return out


def _event_counts(con, lo: int, hi: int, device: str) -> tuple[int, dict[str, int]]:
    # 设备在库里可能以旧值（pc/phone）或注册 id（pc-main/phone-1）存在，必须全部计入。
    # 曾因这里精确匹配 device=?，改造后 event_count 恒为 0，日报整篇显示「当日无数据」。
    from . import devices as _dv
    aliases = _dv.query_aliases(device)
    rows = con.execute(
        "SELECT kind, COUNT(*) FROM event WHERE device IN (" + ",".join(["?"] * len(aliases)) +
        ") AND start_utc_ms>=? AND start_utc_ms<? GROUP BY kind", (*aliases, lo, hi),
    ).fetchall()
    kinds = {r[0]: int(r[1]) for r in rows}
    return sum(kinds.values()), kinds


# ---------------------------------------------------------------- markdown

def _apps_list(st: dict) -> list[dict]:
    apps = st.get("apps") or []
    if isinstance(apps, dict):
        apps = list(apps.values())
    apps = [a for a in apps if isinstance(a, dict)]
    apps.sort(key=lambda a: (-int(a.get("seconds") or 0), str(a.get("app") or "")))
    return apps


def _span(a, b) -> str:
    return f"{_BT}{a}-{b}{_BT}"


# 模型正文里任何层级的 markdown 标题行（# ~ ######）。
# 实测（2026-08-13）：模型自己写了 `# 2026-08-13 个人数字人生日报` 和若干 `## 凌晨启动：…`，
# 渲染出来会和日报自身的 H1/H2 抢层级 —— 视觉上像是「覆盖」了文档结构。
_MD_HEAD_LINE = re.compile(r"^#{1,6}\s+.*$", re.MULTILINE)


def _strip_md_heads(text: str) -> str:
    """剥掉模型正文里自带的 markdown 标题行（任意层级）。

    为什么不分层级处理：日报的章节结构由程序控制，模型正文只应是**自然段**。
    用户 2026-10-22 明确指出：「自然段落里没有句号结尾的一行内容很容易和其他正文区分，
    不需要用 H2 来特殊标记」——把这些行降级成普通段落，靠留白区分即可。
    """
    cleaned = _MD_HEAD_LINE.sub("", str(text or ""))
    # 剥完标题会留下连续空行，压成「段落之间一个空行」
    return re.sub(r"\n{3,}", "\n\n", cleaned).strip()

def _summary_blocks(text: str) -> list[str]:
    """把片层的 overall_summary 转成日报里的小块：**加粗小节** + 列表条目。

    为什么不用 `###`：日报的章节结构由程序控制，片层里的 `## 关键操作` 若原样保留，
    会和日报自己的 H2 抢层级（用户 2026-10-22 明确指出这个「覆盖」观感）。
    也不能压成纯段落（会丢掉三条要点的分隔）。折中：加粗 + 列表。
    """
    out: list[str] = []
    for raw in str(text or "").splitlines():
        s = raw.strip()
        if not s:
            continue
        if s.startswith("#"):
            head = s.lstrip("#").strip()
            if head:
                if out:
                    out.append("")
                out.append("**%s**" % head)
            continue
        if s.startswith(("-", "*", "•")):
            item = s.lstrip("-*• ").strip()
            if item:
                out.append("- %s" % item)
            continue
        out.append(s)
    out.append("")
    return out

def _strip_heads(text: str) -> str:
    """去掉 overall_summary 里的 ## 小标题，只留正文（叙事不需要小标题）。"""
    out = []
    for line in str(text or "").splitlines():
        s = line.strip()
        if s.startswith("##"):
            continue
        if s.startswith("-") or s.startswith("*"):
            s = s.lstrip("-* ").strip()
        if s:
            out.append(s)
    return " ".join(out)


def _narrate_slices(sl: list[dict], max_chars: int = 0) -> list[str]:
    """把一个设备当天各片的视觉结果串成自然段。

    为什么不用时间轴罗列（用户 2026-10-21 明确要求）：
    日报是「读」的，不是「查」的。时间轴属于时段网页，日报要讲今天发生了什么。
    也不再截断成 300 字 + 省略号 —— 那是把内容砍掉，等于丢信息。
    """
    paras: list[str] = []
    for s in sorted(sl, key=lambda x: str(x.get("start") or "")):
        summary = _strip_heads(s.get("overall_summary") or "")
        if not summary:
            continue
        span = "%s–%s" % (s.get("start") or "", s.get("end") or "")
        paras.append("%s 这段时间，%s" % (span, summary.rstrip("。")))
    joined = "\n\n".join(paras)
    if max_chars and len(joined) > max_chars:
        # 只在显式要求时才截；默认 0 = 不截断（省略号丢内容是问题，不是特性）
        joined = joined[:max_chars].rstrip() + "……"
    return [joined] if joined else []


def _apps_sentence(apps: list[dict], limit: int = 6) -> str:
    """「主要花在 X（4 小时 38 分）、Y（3 小时 29 分）上」这种自然句。"""
    top = [a for a in apps if int(a.get("seconds") or 0) > 0][:limit]
    if not top:
        return ""
    parts = ["%s（%s）" % (a.get("app") or a.get("app_id") or "未知", _fmt_dur(a.get("seconds")))
             for a in top]
    return "主要花在 " + "、".join(parts) + " 上"


def _sites_sentence(sites: list[dict], limit: int = 5) -> str:
    top = [s for s in sites if int(s.get("visits") or 0) > 0][:limit]
    if not top:
        return ""
    parts = []
    for s in top:
        dom = s.get("domain") or ""
        secs = int(s.get("seconds") or 0)
        # 只报「域名 + 时长 + 次数」三个客观量；不挑「代表页面」。
        # 用户 2026-10-22：「网站访问别搞代表页面这一列了」—— 一句话里也不要。
        parts.append("%s（%s，%d 次）" % (dom, _fmt_dur(secs), int(s.get("visits") or 0)))
    return "访问最多的是 " + "、".join(parts)


def _tl_line(t: dict) -> str:
    """渲染一条主时间线：整行标题做成指向 link_url 的链接。

    用户 2026-10-22 要求：浏览器窗口行与它里面的网页事件合成**一行**，
    链接直接挂在这一行的标题上 —— 不再另起一行、也不再缩进子行。
    """
    url = (t.get("link_url") or "").strip()
    title = display_title(t)
    body = _md_link(title, url) if url else _cell(title)
    return f"- {_span(t.get('start'), t.get('end'))} " + body


def _tl_sub_line(t: dict) -> str:
    """渲染缩进子行：同一行挂不下的其余网页。**只在 sub 非空时被调用**。"""
    url = (t.get("url") or "").strip()
    title = (t.get("title") or "").strip()
    anchor = url.split("//", 1)[-1] if (not title or title == url) else title
    return "  - └ " + (_md_link(anchor, url) if url else _cell(title))


# 兼容别名：合并逻辑已迁到 timeline_merge（旧名保留，smoke T14 仍 import 这个名字）
_merge_browser_web = merge_browser_web


def _compress_timeline(timeline: list[dict], min_seconds: float = 120.0) -> list[dict]:
    """日报用的压缩时间线：只留占到大多数时间的事件（碎片折叠）。"""
    return [t for t in timeline
            if span_seconds(t.get("start"), t.get("end")) >= min_seconds
            or (t.get("sub") and span_seconds(t.get("start"), t.get("end")) > 0)]


# 兼容别名：实现已统一到 timeline_merge.span_seconds（smoke T14g/T14h 仍 import 这个名字）
_span_seconds = span_seconds


def _sites_summary(st: dict) -> list[dict]:
    """按域名聚合 web 事件：时长、次数、该域名下停留最久的页面（用于做链接）。

    数据本来就在 event 表里（kind=web 的 app 是域名、title 是页面标题、url 是地址）。

    注意（2026-10-22 用户裁决）：日报**不再有「代表页面」列**——这里算出的 top_page
    只用于在 _sites_sentence 里给域名挂一个可点链接；全量页面清单归时段网页
    10-时段/<date>/web-<kind>.html。不要据此重建表格列。
    """
    buckets: dict[str, dict] = {}
    for t in st.get("timeline") or []:
        url = (t.get("url") or "").strip()
        if not url.startswith(("http://", "https://")):
            continue
        domain = (t.get("app") or "").strip() or url.split("//", 1)[-1].split("/", 1)[0]
        # timeline 条目没有 seconds 字段（实测），时长由 start/end 反推
        secs = float(t.get("seconds") or 0) or _span_seconds(t.get("start"), t.get("end"))
        title = (t.get("title") or "").strip()
        if not title or title == url:
            # 网页事件的 title 常直接等于 url：退化为「域名 + 路径」，避免同串出现两遍
            path = url.split("//", 1)[-1]
            title = path if len(path) <= 80 else path[:77] + "…"
        b = buckets.setdefault(domain, {"domain": domain, "seconds": 0.0,
                                        "visits": 0, "top_page": None})
        b["seconds"] += secs
        b["visits"] += 1
        if b["top_page"] is None or secs > float(b["top_page"].get("seconds") or 0):
            b["top_page"] = {"title": title, "url": url, "seconds": secs}
    return sorted(buckets.values(), key=lambda x: -x["seconds"])


def _md_link(title: str, url: str) -> str:
    """把「标题 + URL」渲染成 markdown 链接。

    用户要求：网页链接与浏览器文档名称本来就对得上，直接做成链接格式。
    标题为空时用 URL 作锚文本；URL 非法时退回纯文本，不产出坏链接。
    """
    url = (url or "").strip()
    title = " ".join(str(title or "").split())
    if not url:
        return _cell(title)
    if not (url.startswith("http://") or url.startswith("https://")):
        return _cell(title or url)
    if title == url or not title:
        # 实测很常见：网页事件的 title 直接就是 url。此时锚文本用「域名+路径」，
        # 否则 markdown 里会出现 [http://x/y](http://x/y) 这种同一串写两遍。
        title = url.split("//", 1)[-1] or url
    elif url in title:
        title = title.replace(url, "").strip(" ·-|") or url.split("//", 1)[-1]
    return "[%s](%s)" % (title.replace("]", "\\]"), url)


def _top_apps_section(st: dict, date_key: str, page: str) -> list[str]:
    """应用「突出部分」：一句话 + 指路全量网页（全量表已按用户要求删掉）。

    指路必须是**可点的真实路径**：写死 `<日期>` 占位符等于给用户一个坏链接。
    2026-10-22 结构升级后，全量页已合并为**一天一个文件** 10-时段/<日期>.html，
    锚点固定在 #apps / #sites。旧写法指向 10-时段/<日期>/apps-phone.html，
    那个目录早已不存在，链接是死的（2026-10-23 实测发现）。
    """
    apps = _apps_list(st)
    if not apps:
        return []
    sent = _apps_sentence(apps, limit=6)
    if not sent:
        return []
    return ["", "### 应用（最突出的几个）", "",
            "- %s，共 %d 个应用有记录" % (sent, len(apps)), "",
            "> 全量 %d 个应用见时段网页（[10-时段/%s.html#apps](../10-时段/%s.html#apps)）"
            % (len(apps), date_key, date_key)]


def _top_sites_section(st: dict, date_key: str, page: str) -> list[str]:
    """网站「突出部分」：一句话 + 指路全量网页（不列「代表页面」列）。

    数据源用 dbview 的 `websites`（已按域名聚合、与应用分开），
    不再从 timeline 里自己挑 url 猜 —— 那是两套口径，会出现
    「应用榜里混进域名」这种串味的现象（用户 2026-10-22 指出）。
    """
    sites = st.get("websites") or []
    if not sites:
        return []
    sent = _sites_sentence(sites, limit=5)
    if not sent:
        return []
    return ["", "### 网站（最常去的）", "",
            "- %s，共 %d 个域名有记录" % (sent, len(sites)), "",
            "> 全量 %d 个域名见时段网页（[10-时段/%s.html#sites](../10-时段/%s.html#sites)）"
            % (len(sites), date_key, date_key)]


def _render_device(dev: str, bd: dict, st: dict, sl: list[dict],
                   include_summaries: bool = True) -> list[str]:
    # 设备 id 形如 pc-main / phone-2，类别要从注册表取，不能再用 dev == "pc" 硬判断
    # （多设备改造前 device 就是 "pc"/"phone"，改造后那样写会让所有设备都变「手机」）。
    from . import devices as _dev_mod
    try:
        _kind = _dev_mod.kind_of(dev)
        _label = _dev_mod.label_of(dev)
    except ValueError:
        _kind, _label = ("pc" if dev.startswith("pc") else "phone"), dev
    # 同类只有一台时沿用「电脑/手机」；多台时用**真实设备名**（用户 2026-10-22：
    # 「手机就是 iQQQZ3，电脑是 D-001」），label 的「手机 1」只用于需要成句的场合。
    _multi = len(_dev_mod.by_kind(_kind)) > 1
    label = _dev_mod.name_of(dev) if _multi else ("电脑" if _kind == "pc" else "手机")
    # 中文与英文/数字之间留一个空格：旧写法 f"## {label}使用" 会拼出「手机 1使用」。
    sep = " " if label and label[-1].isascii() and label[-1].isalnum() else ""
    lines = [f"## {label}{sep}使用", ""]
    if not bd["event_count"] and not bd["slice_count"]:
        return lines + ["- （当日无数据）", ""]

    lines.append(f"- 活跃 **{_fmt_dur(bd['active_s'])}**；离开 {_fmt_dur(bd['away_s'])}、"
                 f"锁屏 {_fmt_dur(bd['locked_s'])} 已从活跃时长中剔除（C13）")
    k = bd["kinds"]
    lines.append(f"- 事件 {bd['event_count']} 条（app {k.get('app', 0)} / web {k.get('web', 0)} / "
                 f"screen {k.get('screen', 0)} / power {k.get('power', 0)}）；"
                 f"时段 {bd['slice_count']} 片，视觉总结 {bd['summary_count']} 片；"
                 f"切换 {bd['switch_count']} 次")
    if bd["slice_count"] and not bd["image_count"]:
        lines.append("- 当日仅活动，无截图")

    # 全量表已按用户要求从日报撤掉；这里只留「突出部分」，全量指路时段网页。
    _suffix = "phone" if _kind == "phone" else "pc"
    lines += _top_apps_section(st, bd.get("date") or "", f"apps-{_suffix}.html")
    lines += _top_sites_section(st, bd.get("date") or "", f"web-{_suffix}.html")

    focus = st.get("focus_blocks") or []
    if focus:
        lines += ["", "### 专注段（同一应用 ≥15 分钟）", ""]
        for b in focus[:10]:
            lines.append(f"- {_span(b.get('start'), b.get('end'))} "
                         f"{_cell(b.get('app'))} · {_fmt_dur(b.get('seconds'))}")

    tl = st.get("timeline") or []
    if tl:
        merged = merge_browser_web(tl)
        kept = _compress_timeline(merged, min_seconds=_TIMELINE_MIN_S)
        # 标题与引用块之间必须留空行，否则 markdown 渲染时 > 会紧贴标题。
        lines += ["", "### 时间线（重要事件）", "",
                  f"> 只保留单段 ≥{int(_TIMELINE_MIN_S // 60)} 分钟的事件；"
                  f"完整 {len(tl)} 条见时段网页（`scripts/build_slice_html.py`）。", ""]
        for t in kept[:_TIMELINE_LIMIT]:
            lines.append(_tl_line(t))
            # 时间线**不做嵌套子行**（用户 2026-10-22：
            # 「都不是一个事件的为什么要搞一个次级列表，搞得好像它们是一回事似的」）。
            # merge 阶段已保证：只有时间窗完全一致的行为才合并成一行，
            # 其余网页各自独立成行 —— 所以这里 sub 基本恒为空，保持不渲染。
            # 万一有（同窗多标签），也只作为该行的一部分用「等 N 页」标注，不展开成列表。
            extra = [s for s in (t.get("sub") or []) if (s.get("url") or "").strip()]
            if extra:
                lines[-1] += " 等 %d 页" % (len(extra) + 1)
        if len(kept) > _TIMELINE_LIMIT:
            lines.append(f"- 其余 {len(kept) - _TIMELINE_LIMIT} 条见时段网页")
        elif len(merged) > len(kept):
            # merged 是合并后的行数（网页已挂到父行），不能再与原始 tl 条数混算
            lines.append(f"- 另有 {len(merged) - len(kept)} 条碎片事件（<{int(_TIMELINE_MIN_S // 60)} 分钟）已折叠")

    away = st.get("away") or []
    if away:
        lines += ["", "### 离开时段", ""]
        for a in away[:10]:
            lines.append(f"- {_span(a.get('start'), a.get('end'))} "
                         f"{_cell(a.get('reason') or 'Away')}")

    if sl and include_summaries:
        lines += ["", "### 视觉总结", ""]
        shown = 0
        withsum = [s for s in sl if (s.get("overall_summary") or "").strip()]
        for s in withsum[:_SUMMARY_LIMIT]:
            one = " ".join(str(s["overall_summary"]).split())
            if len(one) > 300:
                one = one[:300] + "…"
            # 无画面依据的片必须标出来，否则读者会以为模型看过画面
            # （2026-10-23 用户视角检查：13% 的片总结纯靠 DB 时间线）
            tag = "（无画面描述）" if _no_visual_basis(s) else ""
            lines.append(f"- {_span(s['start'], s['end'])}{tag} {one}")
            shown += 1
        if len(withsum) > shown:
            lines.append(f"- 其余 {len(withsum) - shown} 片见 {_BT}10-时段/{_BT}")
        if shown == 0:
            lines.append("- （当日无视觉总结：仅活动）")
    return lines + [""]





def _no_visual_basis(sl: dict) -> bool:
    """该片是否**没有**画面描述（判据同 slice_pages/vision.py）。

    真值在事件层 payload.description，由 slices_for_day 一并取出并挂在 `has_description`。
    不能用 structured 里的 described_images —— 纯文本模式下它们恒为空。
    """
    hd = sl.get("has_description")
    if hd is None:
        return False
    return not hd


def _actions_of(structured) -> list[str]:
    if not isinstance(structured, dict):
        return []
    items = structured.get("key_actions")
    if items is None:
        items = structured.get("keyActions")
    if isinstance(items, dict):
        items = [items]
    out: list[str] = []
    if isinstance(items, list):
        for it in items:
            if isinstance(it, str) and it.strip():
                out.append(" ".join(it.split()))
            elif isinstance(it, dict):
                for key in ("detail", "text", "action", "title", "desc", "summary"):
                    v = it.get(key)
                    if isinstance(v, str) and v.strip():
                        out.append(" ".join(v.split()))
                        break
    return out


def _render_key_actions(slices_by_dev: dict[str, list[dict]]) -> list[str]:
    lines = ["## 关键操作", "",
             "> 合并两设备各片的视觉结构化结果（按时间排序）；DB 时间线为基线（R3）。", ""]
    rows: list[tuple[int, str, str]] = []
    for dev in DEVICES:
        for s in slices_by_dev.get(dev, []):
            for txt in _actions_of(s.get("structured")):
                from . import devices as _dm
                try:
                    _k = _dm.kind_of(dev)
                    _lb = _dm.label_of(dev)
                except ValueError:
                    _k, _lb = ("pc" if dev.startswith("pc") else "phone"), dev
                tag = _lb if len(_dm.by_kind(_k)) > 1 else ("电脑" if _k == "pc" else "手机")
                rows.append((int(s["start_utc_ms"]), dev,
                             f"{_span(s['start'], s['end'])} [{tag}] {txt}"))
    rows.sort(key=lambda x: (x[0], x[1]))
    if not rows:
        return lines + ["- （当日无结构化关键操作；见上方时间线与视觉总结）", ""]
    for _, _, txt in rows[:_KEY_ACTION_LIMIT]:
        lines.append(f"- {txt}")
    if len(rows) > _KEY_ACTION_LIMIT:
        lines.append(f"- 其余 {len(rows) - _KEY_ACTION_LIMIT} 条见 {_BT}10-时段/{_BT}")
    return lines + [""]


def render_narrative(date_key: str, breakdown: dict, structures: dict,
                     slices_by_dev: dict, narrative: str | None = None) -> str:
    """日报正文：自然段叙事（用户 2026-10-21 要求）。

    结构：
      # 日期 数字人生日报
      > 一行元信息（事件数/设备）
      ## 今天
      <自然段：这个设备主要做了什么、看了什么、切了多少次>
      <自然段：视觉总结整段（不截断、不加省略号）>
    """
    dd = breakdown["devices"]
    from . import devices as _dl

    def _label(d):
        try:
            k = _dl.kind_of(d)
            return _dl.label_of(d) if len(_dl.by_kind(k)) > 1 else ("电脑" if k == "pc" else "手机")
        except ValueError:
            return d

    has_data = any(v["event_count"] or v["slice_count"] for v in dd.values())
    total_events = sum(v["event_count"] for v in dd.values())
    total_slices = sum(v["slice_count"] for v in dd.values())
    labels = "、".join(_label(d) for d in DEVICES
                      if dd[d]["event_count"] or dd[d]["slice_count"])

    out = ["---", "date: %s" % date_key, "source: digital-life", "---", "",
           "# %s 数字人生日报" % date_key, "",
           "> 机器生成（日层）。周/月/季/年由日记系统自行滚动。", ""]

    if not has_data:
        out += ["## 今天", "", "这一天没有任何设备使用记录。", ""]
        return "\n".join(out).rstrip("\n") + "\n"

    out += ["## 今天", ""]
    body = _strip_md_heads(narrative or "")
    if body:
        # 模型写的叙事正文：纯自然段，**不升格成标题**。
        # 用户 2026-10-22：模型那种「不以句号结尾的短行」本身就和正文分得很开，
        # 不需要 H2 去特殊标记 —— 用 H2 反而会把日报自己的章节层级顶乱。
        out.append(body)
        out.append("")
    else:
        out.append("今天有记录的设备是%s，共 %d 条事件、切成 %d 个时段。"
                   % (labels, total_events, total_slices))
        out.append("")

    # 叙事只是「今天」这一节的开头；后面的应用/网站/专注/时间线/视觉/离开各节
    # 仍要照常产出 —— 早期版本在这里提前 return，把它们整节吞掉了。


    # ---- 各设备明细章节（应用 / 网站 / 专注 / 时间线 / 离开）
    # 叙事只在「今天」里讲发生了什么；明细仍按固定章节给出，互不覆盖。
    for dev in DEVICES:
        v = dd[dev]
        if not v["event_count"] and not v["slice_count"]:
            continue
        out += _render_device(dev, v, structures.get(dev) or {},
                              slices_by_dev.get(dev, []), include_summaries=False)

    # ---- 这里不再粘贴逐片总结（2026-10-24 按 R12 修正，见下方说明）
    all_sl: list[tuple[str, dict]] = []
    for dev in DEVICES:
        for s in slices_by_dev.get(dev, []):
            all_sl.append((dev, s))
    all_sl.sort(key=lambda p: (str(p[1].get("start") or ""), p[0]))
    n_with = sum(1 for _d, s in all_sl if (s.get("overall_summary") or "").strip())
    if all_sl:
        note = "本日共 %d 个时段、%d 片有画面级总结；逐片全文见时段网页%s。" % (
            len(all_sl), n_with, _BT + "10-时段/%s.html#vision" % date_key + _BT)
    else:
        note = "本日没有截图，以上内容全部来自应用与网页记录。"
    out += ["## 展开看全量", "", "- " + note, ""]

    total_sums = sum(v["summary_count"] for v in dd.values())
    out += ["---", "",
            "> 数据规模：%s，共 %d 条事件、%d 个时段、%d 片有视觉总结。完整时间线见时段网页。"
            % (labels, total_events, total_slices, total_sums), ""]

    return "\n".join(out).rstrip("\n") + "\n"


def render_daily(date_key: str, breakdown: dict, structures: dict[str, dict],
                 slices_by_dev: dict[str, list[dict]], narrative: str | None = None) -> str:
    dd = breakdown["devices"]
    has_data = any(v["event_count"] or v["slice_count"] for v in dd.values())
    total_events = sum(v["event_count"] for v in dd.values())
    total_slices = sum(v["slice_count"] for v in dd.values())
    total_sums = sum(v["summary_count"] for v in dd.values())

    lines = ["---", f"date: {date_key}", "source: digital-life", "---", "",
             f"# {date_key} 数字人生日报", "",
             "> 机器总结（日层，由 digital-life 生成）。周/月/季/年由日记系统自行滚动，"
             "本文件不含这些粒度。"]
    if has_data:
        from . import devices as _dl
        def _dev_label(d):
            k = _dl.kind_of(d)
            return _dl.label_of(d) if len(_dl.by_kind(k)) > 1 else ("电脑" if k == "pc" else "手机")
        labels = "、".join(_dev_label(d) for d in DEVICES
                          if dd[d]["event_count"] or dd[d]["slice_count"])
        lines.append(f"> 当日事件 {total_events} 条 · 时段 {total_slices} 片 · "
                     f"视觉总结 {total_sums} 片 · 有数据设备：{labels}")
    lines.append("> 各设备时长独立记账，不相加为总时长（同日两设备并行，相加即重复计入）。")
    lines.append("")

    if narrative:
        lines += ["## 当日概述", "", narrative.strip(), ""]

    if not has_data:
        lines += ["## 电脑使用", "", "- （当日无数据）", "",
                  "## 手机使用", "", "- （当日无数据）", ""]
        return "\n".join(lines + _render_key_actions(slices_by_dev)).rstrip("\n") + "\n"

    for dev in DEVICES:
        lines += _render_device(dev, dd[dev], structures.get(dev) or {}, slices_by_dev.get(dev, []))
    lines += _render_key_actions(slices_by_dev)
    return "\n".join(lines).rstrip("\n") + "\n"


# ---------------------------------------------------------------- 可选：模型概述

def _collect_slice_summaries(slices_by_dev: dict, limit_chars: int = 0) -> list[dict]:
    """收集当天各片的**完整**总结（供模型写日报）。

    不截断、不去小标题 —— 用户明确要求：片层总结必须由模型写全，
    日报再由模型对这些片段做二次总结。Python 只负责搬运原料。
    """
    out: list[dict] = []
    for dev, sl in (slices_by_dev or {}).items():
        for s in sorted(sl, key=lambda x: str(x.get("start") or "")):
            summary = (s.get("overall_summary") or "").strip()
            if not summary:
                continue
            item = {"device": dev, "from": s.get("start") or "", "to": s.get("end") or "",
                    "summary": summary}
            if limit_chars and len(summary) > limit_chars:
                item["summary"] = summary[:limit_chars]
            out.append(item)
    return out


DAILY_PROMPT = """你在为「{date}」写一篇个人数字人生日报。

下面是这一天里，各个时间段（片段）由视觉模型已经写好的总结原文。请**只依据这些原文**，
把它们编织成一篇连贯的中文日报正文。

写作要求（务必遵守）：
0. **不要写标题**。标题与日期由程序生成，你只写正文段落，**不要写任何以 # 开头的行**；
   也不要写「日报」「总结」这类开场。直接进入第一段。
1. **用自然段写，不要用列表、不要用时间轴、不要逐条罗列**。读者要能像读日记一样读下去。
2. **不要省略内容**。所有片段里出现过的重要事情都要写进去；绝对不要用「……」或「等等」跳过。
3. 不要编造原文里没有的应用、网站或事件。
4. 不要写周/月/季/年总结，不要给建议，不要写「总的来说」这类空话。
5. 分段建议：先写这一天的主要状态（在做什么、投入在哪），再按事情本身的发展讲，最后收束到今天的结果。
6. 篇幅随内容而定，宁可长一些，也不要压缩掉细节。

片段总结原文（按时间顺序）：
{segments}
"""


def _llm_narrative(client, date_key: str, breakdown: dict,
                   slices_by_dev: dict | None = None) -> tuple[str | None, str | None]:
    """交给模型写日报正文。未配置或失败一律降级为纯模板，不阻塞日报生成。"""
    if client is None or not hasattr(client, "chat"):
        return None, None

    segs = _collect_slice_summaries(slices_by_dev or {})
    if segs:
        seg_text = "\n\n".join(
            "[%s %s-%s]\n%s" % (s["device"], s["from"], s["to"], s["summary"]) for s in segs)
        prompt = DAILY_PROMPT.format(date=date_key, segments=seg_text)
    else:
        # 没有视觉总结时，退化为只用统计事实（这种情况日报内容本来就很有限）
        facts = {
            "date": date_key,
            "devices": {
                d: {"active_s": v["active_s"], "event_count": v["event_count"],
                    "top_apps": [a["app"] for a in v.get("apps_top", [])[:6]]}
                for d, v in breakdown["devices"].items()
            },
        }
        prompt = ("只依据下面事实，用自然段写一段中文日报正文（不要列表、不要时间轴、不要编造）：\n"
                  + json.dumps(facts, ensure_ascii=False))

    try:
        resp = client.chat([{"role": "user", "content": prompt}], max_tokens=8192)
    except Exception:  # noqa: BLE001 —— 模型故障不阻塞日报
        return None, None
    if not isinstance(resp, dict):
        return None, None
    text = (resp.get("content") or "").strip()
    return (text or None), (resp.get("model") or None)


# ---------------------------------------------------------------- 主入口

def build_daily(con, date_key: str, client=None) -> dict:
    # 本模块内部大量使用 r["col"] 取值，隐含要求连接带 row_factory。
    # 外部脚本/测试用裸 sqlite3.connect() 调进来时会崩在 tuple 下标上，
    # 所以在入口统一兜住，不要求调用方记得设（2026-10-23 实测踩到）。
    if con.row_factory is None:
        con.row_factory = sqlite3.Row
    """汇总当日 → 一段 markdown + device_breakdown，写入 daily_summary（只日层）。"""
    date_key = normalize_date(date_key)
    lo, hi = day_window(date_key)

    all_slices = slices_for_day(con, date_key)
    # slice 表里历史数据仍是旧值（pc/phone），必须按别名集合归拢，
    # 否则改造后该设备一片都匹配不到 -> 日报「时段 0 片」、视觉总结整段消失。
    from . import devices as _dvs
    slices_by_dev = {}
    for d in DEVICES:
        _al = set(_dvs.query_aliases(d))
        slices_by_dev[d] = [s for s in all_slices if s["device"] in _al]
    structures = {d: device_structure(con, date_key, d) for d in DEVICES}

    devices: dict[str, dict] = {}
    structured_devices: dict[str, dict] = {}
    for dev in DEVICES:
        st = structures[dev]
        sl = slices_by_dev[dev]
        totals = st.get("totals") or {}
        n_events, kinds = _event_counts(con, lo, hi, dev)
        apps = _apps_list(st)
        summary_count = sum(1 for s in sl if (s.get("overall_summary") or "").strip())
        devices[dev] = {
            "date": date_key, "device": dev,
            "active_s": int(totals.get("active_s") or 0),
            "away_s": int(totals.get("away_s") or 0),
            "locked_s": int(totals.get("locked_s") or 0),
            "event_count": n_events, "kinds": kinds,
            "switch_count": int(st.get("switch_count") or 0),
            "focus_count": len(st.get("focus_blocks") or []),
            "screen_count": len(st.get("screens") or []),
            "slice_count": len(sl), "summary_count": summary_count,
            "image_count": sum(int(s.get("image_count") or 0) for s in sl),
            "apps_top": [{"app": a.get("app") or a.get("app_id"),
                          "seconds": int(a.get("seconds") or 0)} for a in apps[:10]],
            "source_basis": st.get("source_basis", "event"),
        }
        structured_devices[dev] = {
            "active_s": devices[dev]["active_s"],
            "away_s": devices[dev]["away_s"],
            "locked_s": devices[dev]["locked_s"],
            "switch_count": devices[dev]["switch_count"],
            "focus_blocks": devices[dev]["focus_count"],
            "screens": devices[dev]["screen_count"],
            "slices": len(sl),
            "summaries": summary_count,
            "apps_top": devices[dev]["apps_top"],
        }

    breakdown = {
        "date": date_key, "layer": LAYER, "devices": devices,
        "note": "各设备时长独立记账，不相加为总时长（同日并行，相加即重复计入）",
    }

    narrative, model = _llm_narrative(client, date_key, breakdown, slices_by_dev)
    # 用户要求（2026-10-21）：日报正文由模型对当天片段做二次总结，
    # 用自然段写，不要时间轴罗列；模型不可用时才退回模板。
    #
    # 但「模型不可用」不等于「把已写好的叙事丢掉」：库里若已存模型写的叙事，
    # 复用它重新渲染版式（本次是改版式，不是改内容）。否则一次无模型的重跑
    # 会把这天唯一的生产内容降级成模板 —— 那是净损失。
    if narrative is None:
        prev = con.execute(
            "SELECT structured_json, model FROM daily_summary WHERE date_key = ?",
            (date_key,)).fetchone()
        if prev is not None:
            try:
                old = json.loads(prev["structured_json"] or "{}")
            except (TypeError, ValueError):
                old = {}
            kept = (old.get("narrative") or "").strip()
            if kept:
                narrative = kept
                model = prev["model"]
    if narrative:
        output = render_narrative(date_key, breakdown, structures, slices_by_dev, narrative)
    else:
        output = render_daily(date_key, breakdown, structures, slices_by_dev, narrative)

    structured = {
        "date": date_key, "layer": LAYER, "devices": structured_devices,
        "slices": [{"slice_id": s["slice_id"], "device": s["device"],
                    "start": s["start"], "end": s["end"],
                    "has_summary": bool((s.get("overall_summary") or "").strip())}
                   for s in all_slices],
        "counts": {"event": sum(v["event_count"] for v in devices.values()),
                   "slice": len(all_slices),
                   "slice_with_summary": sum(v["summary_count"] for v in devices.values())},
        "narrative": narrative,
    }

    con.execute(
        "INSERT INTO daily_summary(date_key, model, output_text, structured_json, "
        "device_breakdown_json, created_at) VALUES(?,?,?,?,?,?) "
        "ON CONFLICT(date_key) DO UPDATE SET model=excluded.model, "
        "output_text=excluded.output_text, structured_json=excluded.structured_json, "
        "device_breakdown_json=excluded.device_breakdown_json, created_at=excluded.created_at",
        (date_key, model, output,
         json.dumps(structured, ensure_ascii=False, sort_keys=True),
         json.dumps(breakdown, ensure_ascii=False, sort_keys=True),
         int(time.time() * 1000)),
    )

    return {
        "date": date_key, "layer": LAYER, "output_text": output,
        "device_breakdown": breakdown, "structured": structured,
        "slices": len(all_slices), "model": model, "counts": structured["counts"],
    }
