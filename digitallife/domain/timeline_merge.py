"""时间线合并：把「浏览器窗口事件」与其内部的「网页访问」合成一行。

背景（用户 2026-10-22 指出）：同一次上网会产出两条几乎重合的记录——

    `19:30:10-19:32:42` Microsoft Edge · 罗辑得了MVP…个人
    `19:30:18-19:32:42` www.bilibili.com · <链接>

一个时间戳就够；网页应当是浏览器那一行的**链接**，而不是另起一行、更不是缩进子行。
期望形态：

    `19:30:10-19:32:42` [Microsoft Edge · 罗辑得了MVP…个人](https://www.bilibili.com/…)

规则：

1. 网页事件（有 url）与窗口事件（无 url）时间区间**有重叠**即视为同一段行为；
   重叠秒数最长者优先，并列取更早开始的那条。
2. 挂上的第一个网页成为该行的 `link_url` —— 展示层把**整行标题**做成链接；
   其余网页留在 `sub` 里，由展示层决定缩进还是折叠（日报折叠、时段页全量缩进）。
3. 找不到父行的网页自己当主行（宁可多一行，不丢数据）。

输出条目字段：`start end app title url link_url sub`。
`url` 保留窗口事件原值（多为空）；`link_url` 才是该行该指向的地址。
"""
from __future__ import annotations

import re
from typing import Iterable
from urllib.parse import parse_qsl, urlsplit

__all__ = ["is_web", "is_path", "merge_browser_web", "display_title", "span_seconds"]

_BROWSER_TAIL = re.compile(r"\s*[-–—]\s*(Google Chrome|Microsoft\u200b? Edge|Firefox|Safari|Brave)\s*$")

# 浏览器窗口标题里的**窗口栏噪声**，不是页面内容。用户 2026-10-22 看到整屏
# 「…_哔哩哔哩_bilibili 和另外 5 个页面 - 个人」后要求清掉。
#   和另外 N 个页面  —— Chrome/Edge 多标签时拼进标题后缀
#   - 个人            —— 配置文件（profile）名后缀
_CHROME_TABS = re.compile(r"\s*和另外\s*\d+\s*个页面")
_PROFILE_TAIL = re.compile(r"\s*[-–—]\s*个人\s*$")

# 非 http 协议的地址引用（chrome://history/、edge://settings、about:blank …）。
# 它们不是可点击的网页，但同样是「窗口事件」的另一条记录，不该单独成行。
# 注意顺序：长的必须写在短的前面（chrome-extension 若排在 chrome 之后，
# 「chrome」分支要求紧跟 "："，遇到 "-" 就失败，整条规则永远匹配不到）。
_SCHEME_REF = re.compile(
    r"^(chrome-extension|moz-extension|view-source|devtools|chrome|edge|about|file)://?")


def _hms_to_s(v) -> float | None:
    """"HH:MM:SS" → 秒；非法返回 None。"""
    if not isinstance(v, str):
        return None
    parts = v.strip().split(":")
    if len(parts) != 3:
        return None
    try:
        h, m, s = (int(x) for x in parts)
    except ValueError:
        return None
    return float(h * 3600 + m * 60 + s)


def span_seconds(start, end) -> float:
    """区间秒数；跨午夜（end < start）按 +1 天计。"""
    a, b = _hms_to_s(start), _hms_to_s(end)
    if a is None or b is None:
        return 0.0
    return float(b - a if b >= a else b + 86400 - a)


def is_web(item: dict) -> bool:
    """有 http(s) 地址即视为网页事件。"""
    return str(item.get("url") or "").strip().startswith(("http://", "https://"))


def is_path(item: dict) -> bool:
    """标题是本地文件系统路径 —— 这类事件是「窗口事件」的影子副本。

    实测（2026-08-13）：同一毫秒区间里同时有
        03:24:51-03:24:56  Windows 资源管理器 · 1.22.98_1     （窗口标题）
        03:24:51-03:24:56  C:/Users/D-001/Desktop/1.22.98_1   （ManicTime Documents 时间线）
    24 组这样的重复，和「浏览器 vs 网页」是同一类问题：一个时间戳就够。
    """
    if is_web(item):
        return False
    title = str(item.get("title") or "").strip()
    if not title:
        return False
    if len(title) > 2 and title[1] == ":" and title[0].isalpha() and title[2] in "\\/":
        return True   # Windows 盘符路径：C:\ 或 C:/
    if str(item.get("app") or "").strip() == title and _SCHEME_REF.match(title):
        return True   # chrome://history/ 这类非 http 协议的内部页，同样是窗口行的影子
    return title.startswith("\\\\") or title.startswith("/home/") or title.startswith("/Users/")

def _overlap_s(a: dict, b: dict) -> float:
    """两个事件的真正重叠秒数；不重叠返回 0。"""
    a0, a1 = _hms_to_s(a.get("start")), _hms_to_s(a.get("end"))
    b0, b1 = _hms_to_s(b.get("start")), _hms_to_s(b.get("end"))
    if None in (a0, a1, b0, b1):
        return 0.0
    if a1 < a0:
        a1 += 86400
    if b1 < b0:
        b1 += 86400
    lo, hi = max(a0, b0), min(a1, b1)
    return max(0.0, hi - lo)


def clean_title(title: str) -> str:
    """去掉浏览器窗口标题里的窗口栏噪声。**公开 API**（T3 升格）。

    三类都要去（实测串在一起出现）：
      `… _哔哩哔哩_bilibili 和另外 5 个页面 - 个人 - Google Chrome`
        → `… _哔哩哔哩_bilibili`
    这些是**浏览器自己画的窗口栏文字**，不是页面内容，留着只会让每一行都尾巴一样长。

    为什么升格为公开（2026-10-25，T3）：呈现层（`slice_pages/contract.py`、
    `timeline.py`、`web.py`）三个文件原先都在 `from digitallife.timeline_merge
    import _clean_title` —— **跨包 import 下划线私有符号**，是 layering.md P0-1 判决
    里「最危险的私有依赖」。私有符号随时可改签名而不被外部约束；
    升格后它成为受契约保护的公开 API，改它必须先想清楚三处调用点。
    """
    t = _BROWSER_TAIL.sub("", str(title or ""))
    t = _CHROME_TABS.sub("", t)
    t = _PROFILE_TAIL.sub("", t)
    return t.strip()


# 兼容别名：模块内 4 处旧调用与外部旧 import 都还能用（T3 不破坏任何现有调用方）。
# 新代码一律用 clean_title()，见 DL-22。
_clean_title = clean_title


# ---------------------------------------------------------------- 同页判定

# 纯追踪/会话参数：同一页面的不同访问批次会产生不同值，对「这是哪个页面」无意义。
# 注意 **不能** 一刀切按「同路径」判同页 —— 实测 ?tab=quota 与 ?tab=billing、
# ?page=4 与 ?page=5 是不同的页面，必须各自保留（2026-10-22 用真实数据验证过）。
_TRACK_PARAMS = frozenset((
    "vd_source", "spm_id_from", "spm", "trackid", "from", "from_source",
    "source", "sourceid", "share_source", "share_medium", "share_plat",
    "share_session_id", "share_tag", "unique_k", "bbid", "ts", "msource",
    "seid", "sca_esv", "sxsrf", "ei", "ved", "uact", "gs_lcrp", "oq",
    "gs_lp", "sclient", "biw", "bih", "usg", "client",
))


def page_key(url: str) -> tuple:
    """把一个 URL 归一成「页面身份」：忽略锚点与纯追踪参数。

    用于回答「这两条链接是不是同一个页面」。实测必要性（2026-10-22）：
    同一段行为会产出
        http://api-docs.deepseek.com/zh-cn/updates
        http://api-docs.deepseek.com/zh-cn/updates#时间2024-12-10
    主行已是该页，子行再挂一遍纯属冗余（用户原话：「缩进那个纯纯冗余」）。
    """
    raw = str(url or "").strip()
    try:
        p = urlsplit(raw)
    except ValueError:
        return ("raw", raw)
    kept = tuple(sorted(
        (k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
        if k.lower() not in _TRACK_PARAMS
    ))
    # **刻意不含 scheme**：实测同一页面会同时以 http 与 https 记录
    # （如 http://kimi.moonshot.cn/chat/x 与 https://kimi.moonshot.cn/chat/x），
    # 把它俩当两页就会在原地长出一条重复子行。
    host = p.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    return (host, p.path or "/", kept)

def merge_browser_web(timeline: Iterable[dict]) -> list[dict]:
    """合并浏览器窗口与其内部网页事件，返回按开始时间排序的新列表。"""
    items = [dict(t) for t in (timeline or [])]
    anchors = [t for t in items if not is_web(t) and not is_path(t)]
    webs = [t for t in items if is_web(t)]
    paths = [t for t in items if is_path(t)]

    # ---------------------------------------------------------------- 配对判据
    # 「同一事件」只有一个可靠定义：**网页与窗口的起止时间完全相同**。
    #
    # 实测（五天真实数据，共 2,560 个网页事件）：
    #   起止完全相同 1,282 ｜ start 差 ≤2s 363 ｜ start 差 ≤60s 788 ｜ 差 >60s 127
    #
    # 前两类（1,645 个 = 64%）是 ManicTime 同一段行为的两种记法，合并成一行是对的。
    # 但从「起点差 60 秒」开始就是不同的行为了 —— 用户 2025-02-17 那天的报告里，
    # 一个 20:59:11 开始的 Obsidian 窗口被塞进 12 个豆包会话 + 1 个 bilibili 视频，
    # 原话：「都不是一个事件的为什么要搞一个次级列表，搞得好像它们是一回事似的」。
    #
    # 所以：**时间窗完全一致才合并；其余网页全部独立成行，不嵌套、不挂父行。**
    # 宁可时间线长几行，也不允许把不同事件说成一件事。
    attached: dict[int, list[dict]] = {}
    detached: list[dict] = []
    win_index: dict[tuple, list[dict]] = {}
    for a in anchors:
        k = (str(a.get("start") or ""), str(a.get("end") or ""))
        win_index.setdefault(k, []).append(a)

    for w in webs:
        k = (str(w.get("start") or ""), str(w.get("end") or ""))
        cands = win_index.get(k) or []
        if not cands:
            detached.append(w)
            continue
        # 同区间可能有多条窗口事件（如 Edge 与 Chrome 同时在跑）：
        # 取「app 名出现在网页 app 里」的那条，其次取第一条，保证确定性。
        wapp = str(w.get("app") or "").strip().lower()
        pick = None
        for a in cands:
            aapp = str(a.get("app") or "").strip().lower()
            aan = str(a.get("title") or "").strip().lower()
            if wapp and (wapp in aapp or aapp in wapp or wapp in aan):
                pick = a; break
        if pick is None:
            pick = cands[0]
        attached.setdefault(id(pick), []).append(w)
        # 网页事件自己的 title 常常直接就是 URL（实测 tai 源就是这样）。
        # 那样渲染出来锚文本只剩域名，读者分不清是哪个页面。
        # 同窗的窗口行标题是可读的，借过来当锚文本。
        if str(w.get("title") or "").strip().startswith(("http://", "https://")):
            readable = clean_title(pick.get("title") or "")
            if readable:
                w = dict(w)
                w["title"] = readable
                attached[id(pick)][-1] = w

    # 路径事件（ManicTime 文档时间线）同理：只在时间窗完全一致时才算影子副本。
    path_of: dict[int, dict] = {}
    orphan_paths: list[dict] = []
    for p in paths:
        k = (str(p.get("start") or ""), str(p.get("end") or ""))
        cands = win_index.get(k) or []
        if not cands:
            orphan_paths.append(p)
        else:
            path_of[id(cands[0])] = p

    out: list[dict] = []
    for t in anchors:
        row = dict(t)
        kids = attached.get(id(t)) or []
        if kids:
            # 时间窗相同 → 这些确实是同一次行为的「窗口视图」与「网页视图」。
            # 第一个网页当整行链接（锚文本仍是窗口标题，用户认的是那个标题）；
            # 其余同窗网页（极少，通常是同一秒的多个标签）保留为 sub。
            row["link_url"] = str(kids[0].get("url") or "").strip()
            seen = {page_key(row["link_url"])}
            subs: list[dict] = []
            for k2 in kids[1:]:
                pk = page_key(k2.get("url") or "")
                if pk in seen:
                    continue
                seen.add(pk)
                subs.append(k2)
            row["sub"] = subs
            row["web_count"] = len(kids)
        else:
            row["link_url"] = ""
            row["sub"] = []
        twin = path_of.get(id(t))
        if twin is not None:
            row["path_twin"] = str(twin.get("title") or "").strip()
        out.append(row)

    for w in detached:
        row = dict(w)
        row["link_url"] = str(w.get("url") or "").strip()
        row["sub"] = []
        out.append(row)

    for p in orphan_paths:
        out.append(dict(p, link_url="", sub=[]))

    out.sort(key=lambda x: (str(x.get("start") or ""), str(x.get("end") or "")))
    return _fold_span_twins(_drop_page_echoes(out))


def _drop_page_echoes(rows: list[dict]) -> list[dict]:
    """同一页面在时间上几乎重叠时，只保留信息最全的一行。

    实测（2025-02-17 20:49，用户看到的那两行）：

        20:49:14-20:50:03  将【机器人工程专业】… - Kimi.ai - Google Chrome   （窗口行，无链接）
        20:49:14-20:50:14  moonshot.cn → …/chat/cupj…                        （网页行，有链接）

    同一段行为的两种记法，域名一个写 moonshot.cn 一个写 kimi.moonshot.cn，
    起止只差 11 秒 —— 用户要的是「一个时间戳一条」，不是两条并排。

    判据（三条同时满足才折，宁可多留不误删）：
      ① 页面身份相同（page_key：域名+路径+关键查询；窗口行用标题里的 URL 或标题归一）
      ② 区间重叠 ≥ 较短者时长的 90%
      ③ 一行有链接、一行没有（或标题互相包含）—— 确认是同一件事的两种记法

    性能（2026-10-22 用户批准剪枝）：原先对每行都与全部已保留行两两比对，
    单日 build_daily 里这一函数占 97% 耗时（2025-02-17 实测 14.57s/14.99s，
    identity() 被调 668,038 次）。改为**按身份与标题建倒排索引**后只在同键候选里扫，
    复杂度从 O(n²) 降到 O(n·k)（k = 同键候选数，实测个位数）。
    **语义必须与旧版逐字一致**：候选仍按「插入顺序（slot 升序）」取第一个命中，
    折行时仍只把「无链接换有链接」的一方替换掉。
    """
    def span(r):
        a, b = _hms_to_s(r.get("start")), _hms_to_s(r.get("end"))
        if a is None or b is None:
            return None
        if b < a:
            b += 86400
        return (a, b)

    def identity(r):
        u = str(r.get("link_url") or r.get("url") or "").strip()
        if u.startswith(("http://", "https://")):
            return page_key(u)
        # 窗口行没有 URL：用「应用 + 去浏览器尾巴的标题」当身份
        return ("win", str(r.get("app") or "").strip().lower(),
                clean_title(r.get("title") or "").lower())

    kept: list[dict] = []
    spans: list[tuple | None] = []      # slot -> (start_s, end_s) | None
    idents: list[tuple] = []            # slot -> identity
    wins: list[bool] = []               # slot -> 是否窗口行
    titles: list[str] = []              # slot -> 去掉浏览器尾巴的小写标题
    gens: list[int] = []                # slot -> 代次（替换后旧索引项作废）
    by_ident: dict[tuple, list] = {}    # identity -> [(s, e, slot, gen)]
    by_title: dict[str, list] = {}      # 标题 -> [(s, e, slot, gen)]（跨类型对齐用）

    def register(slot: int) -> None:
        sp = spans[slot]
        if sp is None:
            return
        gens[slot] += 1
        item = (sp[0], sp[1], slot, gens[slot])
        by_ident.setdefault(idents[slot], []).append(item)
        t = titles[slot]
        if t:
            by_title.setdefault(t, []).append(item)

    dropped = 0
    for r in rows:
        sp = span(r)
        ident = identity(r)
        r_win = ident[0] == "win"
        r_title = clean_title(r.get("title") or "").lower()

        hit = -1
        if sp is not None:
            cands = list(by_ident.get(ident) or ())
            if r_title:
                cands.extend(by_title.get(r_title) or ())
            if len(cands) > 1:
                cands.sort(key=lambda c: c[2])     # 恢复「按插入顺序取第一个命中」
            for ks, ke, slot, gen in cands:
                if gen != gens[slot]:              # 该槽位已被替换，索引项作废
                    continue
                if slot == hit:
                    continue
                if idents[slot] != ident:
                    # 窗口行与网页行类型不同，但可能是同一件事：靠标题对齐
                    if r_win == wins[slot]:
                        continue
                    if not r_title or r_title != titles[slot]:
                        continue
                ov = max(0.0, min(sp[1], ke) - max(sp[0], ks))
                shorter = min(sp[1] - sp[0], ke - ks) or 1
                if ov / shorter < 0.9:
                    continue
                hit = slot
                break

        if hit < 0:
            slot = len(kept)
            kept.append(r)
            spans.append(sp)
            idents.append(ident)
            wins.append(r_win)
            titles.append(r_title)
            gens.append(0)
            register(slot)
            continue
        # 折掉：把有链接/更长的一方留下
        dropped += 1
        a_link = bool(str(kept[hit].get("link_url") or "").strip())
        b_link = bool(str(r.get("link_url") or "").strip())
        if b_link and not a_link:
            kept[hit] = r
            spans[hit] = sp
            idents[hit] = ident
            wins[hit] = r_win
            titles[hit] = r_title
            register(hit)
    if dropped:
        kept.sort(key=lambda x: (str(x.get("start") or ""), str(x.get("end") or "")))
    return kept


def _fold_span_twins(rows: list[dict]) -> list[dict]:
    """把「同区间、同内容、不同表述」的重复行折掉，只留信息最全的一条。

    实测另一类重复（2026-09-22，4 组）：
        08:21:06-08:21:16  记事本 · *BASB.md - 记事本      （窗口标题）
        08:21:06-08:21:16  *BASB.md · *BASB.md            （文档时间线）
    与「路径影子」同源：一个事件被两条 ManicTime 时间线各记一次。
    判据收紧到「同一区间 + 双方都无链接 + 一条的标题包含另一条的标题或应用名」，
    不满足就原样保留（宁可多一行，不误删）。
    """
    groups: dict[tuple, list[dict]] = {}
    for r in rows:
        groups.setdefault((str(r.get("start") or ""), str(r.get("end") or "")), []).append(r)

    out: list[dict] = []
    for (_s, _e), group in groups.items():
        if len(group) == 1:
            out.extend(group)
            continue
        group.sort(key=lambda r: (-len(str(r.get("title") or "")), str(r.get("app") or "")))
        keep = [group[0]]
        for cand in group[1:]:
            host = keep[0]
            ct = str(cand.get("title") or "").strip()
            ca = str(cand.get("app") or "").strip()
            ht = str(host.get("title") or "").strip()
            ha = str(host.get("app") or "").strip()
            if host.get("link_url") or cand.get("link_url"):
                keep.append(cand)      # 有链接的一律不折（链接是内容，不是表述）
                continue
            redundant = (ct and (ct in ht or ct == ha)) or (ca and (ca in ht or ca == ht))
            if redundant:
                host.setdefault("alt_of", []).append("%s · %s" % (ca, ct) if ca != ct else ct)
            else:
                keep.append(cand)
        out.extend(keep)
    return out


def _strip_www(s: str) -> str:
    """去掉字符串里所有 `www.` 前缀，用于「是不是同一个域名」的比较。"""
    return re.sub(r"(?i)\bwww\.", "", str(s or ""))


def display_title(row: dict) -> str:
    """整行锚文本：`应用 · 标题`（标题已去浏览器尾巴）。

    当标题本身就是 URL（tai 源的网页事件常这样）时，锚文本会退化成只剩域名，
    读者分不清是哪个页面。此时改用「域名 + 路径末段」，例如
    `doubao.com · chat/1401683721730306`，至少能区分不同会话。
    """
    app = str(row.get("app") or "").strip()
    # app 本身也可能就是一串地址（扩展页实测 app 与 title 都存了同一串）。
    if _SCHEME_REF.match(app) or app.startswith(("http://", "https://")):
        app = _short_scheme_ref(app) if _SCHEME_REF.match(app) else (_readable_from_url(app) or app)
    title = clean_title(row.get("title") or "")
    if title.startswith(("http://", "https://")):
        title = _readable_from_url(title) or title
    elif _SCHEME_REF.match(title):
        # 浏览器扩展/内部页的地址（chrome-extension://<32位id>/dashboard.html?recordKey=…）。
        # 实测 2025-02-17 时间线里它整串铺了两行，还盖住了「Google Chrome · RSS Reader」
        # 这条真正的窗口记录 —— 读者既看不出是什么应用，也读不完。
        title = _short_scheme_ref(title)
    if app and title and title != app:
        # 标题已经以应用名开头时不要重复（实测渲染出过
        # 「doubao.com · doubao.com · 861094268183810」这种双域名）
        if title.lower().startswith(app.lower()):
            return title
        # 同上，但 app 带 www. 前缀、标题不带（实测 tai 源 app 存的就是裸域名）：
        # 「www.bilibili.com · bilibili.com · BV1SZgG67Efs」——同一域名说两遍。
        if _strip_www(title).lower().startswith(_strip_www(app).lower()):
            return title
        return "%s · %s" % (app, title)
    return title or app


def _short_scheme_ref(ref: str) -> str:
    """把 chrome-extension:// / chrome:// 之类的地址压成可读短串。

    chrome-extension://bfdoopgbjcaghfblbdikollnphobemo/dashboard.html?recordKey=…
        -> 浏览器扩展 · dashboard.html
    chrome://settings/content/siteDetails?site=https%3A%2F%2Fsijishecn.cc
        -> chrome://settings · content/siteDetails
    """
    raw = str(ref or "").strip()
    m = _SCHEME_REF.match(raw)
    scheme = (m.group(1) if m else "").lower()
    rest = raw[len(m.group(0)):] if m else raw
    rest = rest.lstrip("/")
    path = rest.split("?", 1)[0].split("#", 1)[0]
    tail = path.rsplit("/", 1)[-1]
    if scheme in ("chrome-extension", "moz-extension", "devtools"):
        return "浏览器扩展 · %s" % tail if tail and len(tail) <= 40 else "浏览器扩展"
    head = "%s://%s" % (scheme, path.split("/", 1)[0]) if path else "%s://" % scheme
    if "/" in path:
        head += " · " + path.split("/", 1)[1]
    return head[:80]


def _readable_from_url(url: str) -> str:
    """把 URL 压成可读短串：`域名 · 路径末段`（去掉追踪参数）。"""
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
    return "%s · %s" % (host, tail) if tail else host
