"""片层/时间线/应用/网站的**文本片段**渲染（纯函数，无 IO、无 HTML/JS）。

从 daily.py 拆出（T4，2026-10-25 纯搬迁，不改任何函数体）。
"""
from __future__ import annotations

import re

from ..domain.timeline_merge import display_title, merge_browser_web, span_seconds
from .structure import _cell, _fmt_dur


_BT = chr(96)  # markdown 反引号；源码里不写裸字符，避免编辑期被误替换


# ---------------------------------------------------------------- 时间与格式化

_TIMELINE_MIN_S = 120.0  # 日报只留单段 >=2 分钟的事件；碎片折叠，完整版进时段网页
_TIMELINE_LIMIT = 4000   # 兜底上限（正常远达不到）
_SUMMARY_LIMIT = 400     # 视觉总结全量
# 日报「关键操作」只留这么多条（R12：突出部分）。改小它的直接理由：
# 2026-09-15 原先列了 248 条，一篇日报 27K 字，读起来是流水账不是日记。
_KEY_ACTION_LIMIT = 10

_MD_HEAD_LINE = re.compile(r"^#{1,6}\s+.*$", re.MULTILINE)


_DEVICE_SECTIONS: tuple[str, ...] = ("应用", "网站", "专注段", "时间线", "离开时段", "视觉总结")

# 单节无数据时的占位正文（全文唯一出处，不要在别处再写一遍这套文案）
_EMPTY_SECTION = "- （无）"

# 视觉总结的缺叙事说明（三级降级的第 2、3 级，见 render_narrative 文档）
_NARR_MISSING_OTHERS = "- （本节未单独生成叙事）"
_NARR_MISSING_NONE = "- （本节尚无模型叙事；当日片层总结见时段网页）"


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
def _compress_timeline(timeline: list[dict], min_seconds: float = 120.0) -> list[dict]:
    """日报用的压缩时间线：只留占到大多数时间的事件（碎片折叠）。"""
    return [t for t in timeline
            if span_seconds(t.get("start"), t.get("end")) >= min_seconds
            or (t.get("sub") and span_seconds(t.get("start"), t.get("end")) > 0)]


# 兼容别名：实现已统一到 timeline_merge.span_seconds（smoke T14g/T14h 仍 import 这个名字）
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
    """应用「突出部分」正文：一句话 + 指路全量网页（**不含标题行**）。

    指路必须是**可点的真实路径**：写死 `<日期>` 占位符等于给用户一个坏链接。
    2026-10-22 结构升级后，全量页已合并为**一天一个文件** 10-时段/<日期>.html，
    锚点固定在 #apps / #sites。旧写法指向 10-时段/<日期>/apps-phone.html，
    那个目录早已不存在，链接是死的（2026-10-23 实测发现）。

    2026-10-25 起本函数**不再输出 `### 应用（最突出的几个）` 标题**：
    日报骨架由用户定稿（`### 应用` / `### 网站` / `### 专注段` 六节固定），
    标题由 _render_device 统一产出。标题与正文分居两处时，改文案必然一半新一半旧
    —— 这正是 753 篇日报归一化后出现 28 种结构模式的原因之一。
    `page` 参数保留仅为兼容旧调用方（已不使用）。
    """
    apps = _apps_list(st)
    if not apps:
        return []
    sent = _apps_sentence(apps, limit=6)
    if not sent:
        return []
    return ["- %s，共 %d 个应用有记录" % (sent, len(apps)), "",
            "> 全量 %d 个应用见时段网页（[10-时段/%s.html#apps](../10-时段/%s.html#apps)）"
            % (len(apps), date_key, date_key)]


def _top_sites_section(st: dict, date_key: str, page: str) -> list[str]:
    """网站「突出部分」正文：一句话 + 指路全量网页（**不含标题行**，不列「代表页面」列）。

    数据源用 dbview 的 `websites`（已按域名聚合、与应用分开），
    不再从 timeline 里自己挑 url 猜 —— 那是两套口径，会出现
    「应用榜里混进域名」这种串味的现象（用户 2026-10-22 指出）。

    标题 `### 网站` 由 _render_device 统一产出，理由同 _top_apps_section。
    `page` 参数保留仅为兼容旧调用方（已不使用）。
    """
    sites = st.get("websites") or []
    if not sites:
        return []
    sent = _sites_sentence(sites, limit=5)
    if not sent:
        return []
    return ["- %s，共 %d 个域名有记录" % (sent, len(sites)), "",
            "> 全量 %d 个域名见时段网页（[10-时段/%s.html#sites](../10-时段/%s.html#sites)）"
            % (len(sites), date_key, date_key)]


# 日报骨架：每台设备节下**固定六节**，顺序不可变。
# 为什么固定（2026-10-25 用户裁决）：旧实现每节都是 `if focus:` / `if tl:` / `if away:`，
# 没数据就整节消失 —— 753 篇日报归一化后因此长出 **28 种结构模式**。
# 用户要的是「一节都不许少，没数据就写一句没有」。
_merge_browser_web = merge_browser_web


_span_seconds = span_seconds

