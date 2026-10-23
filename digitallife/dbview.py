"""日结构视图 —— 纯 DB 推导，不掺任何模型。

唯一数据来源是 event 表（ManicTime 的 app / web / power 为主来源）：本模块不做语义推断，
这是「DB 是骨架」的落地点。

口径（写死在这里，smoke 可断言）：
- 时间是唯一主轴：区间一律在全局 UTC ms 轴上算，只在输出时转本地 HH:MM:SS。
- C13：kind='power' 的 title ∈ {Active, Away, Session lock}。Away / Session lock 的区间
  必须从使用时长里剔除（实测 ManicTime 有 1796 条此类事件，不剔除会把离开 8 小时算成使用时长）。
  Away 与 locked 重叠时只按 locked 计，因此 totals 三项互不重叠：
  active_s + away_s + locked_s = 当日 app 覆盖与离开时段的并集。
- focus_blocks：同一 app 连续跨度 ≥15 分钟（允许中间 ≤120s 间隙）；
  seconds = 该连续段的墙钟跨度（含被允许的 ≤120s 间隙）。
- sessions：同一 app 当日被「>120s 空档或他 app 插入」切开后的段数。
- timeline：已剔除 away/locked 的活跃段，按 (app,title,url) 合并 ≤1s 的缝隙。
"""
from __future__ import annotations

import json
import sqlite3
import time
from datetime import timedelta
from typing import Iterable

import re
from urllib.parse import urlsplit

from . import tz

FOCUS_MIN_S = 15 * 60          # focus_blocks 的最小连续跨度
FOCUS_GAP_MS = 120_000         # focus/session 允许的中间间隙
TIMELINE_GAP_MS = 1_000        # timeline 合并同 (app,title,url) 的微缝隙

POWER_AWAY = "away"
POWER_LOCKED = "locked"

Interval = tuple[int, int]


# ---- 区间原语 -----------------------------------------------------------

_HOST_OK = re.compile(r"^[a-z0-9]([a-z0-9.-]*[a-z0-9])?\.[a-z]{2,}$")


def _domain_of(url: str) -> str:
    """从 URL 取域名（去 www.、去端口、转小写）。

    为什么不信任 event.app：tai 源把**页面标题**写进了 app 字段
    （实测形如「长评哪吒2登顶…_哔哩哔哩_bilibili」），直接当域名用会让
    网站榜变成标题榜 —— 用户 2026-10-22 指出的「网站和页面记录混在一起」。
    """
    raw = str(url or "").strip()
    try:
        netloc = urlsplit(raw).netloc or raw.split("//", 1)[-1].split("/", 1)[0]
    except ValueError:
        netloc = raw.split("//", 1)[-1].split("/", 1)[0]
    netloc = netloc.split("@")[-1].split(":")[0].lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    # 合法性兜底：域名只允许字母数字、点、连字符。
    # 实测脏数据里出现过 URL 编码残片被当成域名（形如 `2Fsijishecn.cc`），
    # 也有中文标题整行落进 app 字段的情况 —— 都不该出现在网站榜里。
    if not _HOST_OK.match(netloc):
        return ""
    return netloc

def _merge(ivs: Iterable[Interval], gap_ms: int = 0) -> list[Interval]:
    """排序合并：重叠或间隔 ≤ gap_ms 的区间并成一段。gap_ms=0 即「相接即并」。"""
    out: list[list[int]] = []
    for s, e in sorted((int(a), int(b)) for a, b in ivs):
        if e <= s:
            continue
        if out and s - out[-1][1] <= gap_ms:
            if e > out[-1][1]:
                out[-1][1] = e
        else:
            out.append([s, e])
    return [(s, e) for s, e in out]


def _length(ivs: Iterable[Interval]) -> int:
    return sum(e - s for s, e in ivs)


def _subtract(ivs: Iterable[Interval], cuts: list[Interval]) -> list[Interval]:
    """ivs 减去 cuts（cuts 必须已排序合并）。返回剩余的活跃区间。"""
    out: list[Interval] = []
    for s, e in ivs:
        cur: list[Interval] = [(s, e)]
        for cs, ce in cuts:
            if not cur:
                break
            nxt: list[Interval] = []
            for a, b in cur:
                if ce <= a or cs >= b:
                    nxt.append((a, b))
                    continue
                if cs > a:
                    nxt.append((a, cs))
                if ce < b:
                    nxt.append((ce, b))
            cur = nxt
        out.extend(cur)
    return out


# ---- 时间与分类 ---------------------------------------------------------

def _day_window(date_key: str) -> tuple[int, int]:
    """本地日 YYYY-MM-DD → (day 起点 UTC ms, 次日起点 UTC ms)。"""
    start = tz.parse_local(f"{date_key.strip()} 00:00:00")
    return tz.local_to_utc_ms(start), tz.local_to_utc_ms(start + timedelta(days=1))


def _hms(ms: int) -> str:
    return tz.utc_ms_to_local(ms).strftime("%H:%M:%S")


def power_class(title: str | None) -> str | None:
    """电源状态归一：Away / Session lock / Active（Active 返回 None = 不剔除）。

    两类来源都要认：
    1. ManicTime 的 kind='power'（title = Active / Away / Session lock）；
    2. **手机侧 IQQZ3 把屏幕状态记成 kind='app'**（title/app = '屏幕关闭 (锁定)' /
       '屏幕开启 (解锁)' / '屏幕关闭'）。实测 2025-02-10 一天里这类事件占 app 时长 **73%**
       （68,801s / 94,309s），不剔除会把"锁屏 18 小时"算成使用时长。
    """
    t = (title or "").strip().lower()
    if "lock" in t or "锁定" in t:
        return POWER_LOCKED
    if "away" in t or "离开" in t:
        return POWER_AWAY
    # 手机屏幕态：关闭即离开（含锁定）；开启(解锁) 表示恢复使用，不是离开
    if t.startswith("屏幕关闭") or t == "screen off":
        return POWER_LOCKED if "锁定" in t else POWER_AWAY
    # 手机关机/重启：实测「设备关机」单条最长 76.7 天、「设备启动」7 天 ——
    # 这类区间显然不是使用时长，归为离开。
    if t.startswith("设备关机") or t.startswith("设备启动") or t in ("shutdown", "boot"):
        return POWER_AWAY
    return None


# 需要从「使用时长」里剔除的手机 app 事件（屏幕状态行不是应用使用）
SCREEN_STATE_PREFIXES = ("屏幕关闭", "屏幕开启", "screen off", "screen on")


def _app_name(row: sqlite3.Row | dict) -> str | None:
    """app 列为空时退到 title（ManicTime ReportId=4 的非 URL 行 app=None）。"""
    return row["app"] or row["title"] or None


# 路径形态的应用名：盘符路径、UNC、类 Unix 绝对路径。
# 它们来自 ManicTime 文档时间线，是「窗口事件的影子」，不是应用本身。
# 实测 2025-02-17 应用榜里混进「C:\\Users\\D-001\\Desktop\\日记\\.obsidian\\plugins」
# 与「C:\\Users\\D-001\\Downloads」，当天 26 个应用里占 2 个。
_DRIVE_PATH = re.compile(r"^[A-Za-z]:[\\/]")
_UNIX_PATH = ("/home/", "/Users/", "/mnt/", "/opt/", "/var/", "/usr/")


def _is_path_name(name: str) -> bool:
    """应用名是不是一条文件系统路径。

    反斜杠在这里一律用 startswith 判，不塞进正则 —— UNC 前缀是整整两个反斜杠，
    写成 raw string 要 4 个字符，层数一多就分不清（本函数第一版就写错了）。
    """
    s = str(name or "").strip()
    if not s:
        return False
    return bool(_DRIVE_PATH.match(s)) or s.startswith("\\\\") or s.startswith(_UNIX_PATH)

# 点事件（只有 start、没有 end 的 app/web 记录）补的默认时长。
# 60s 与截图采样粒度一致：够让它出现在时间线里，又不会虚增使用时长。
POINT_EVENT_MS = 60_000


def _fetch(con: sqlite3.Connection, lo: int, hi: int, device: str | None) -> list[sqlite3.Row]:
    sql = ("SELECT * FROM event WHERE start_utc_ms < ? AND COALESCE(end_utc_ms, start_utc_ms) >= ?")
    args: list = [hi, lo]
    if device:
        # 同一台设备在库里可能以旧值（pc/phone）或注册 id（pc-main/phone-1）存在，
        # 查询必须两者都认，否则改造后历史数据会整体查不到。
        from . import devices as _dv
        aliases = _dv.query_aliases(device)
        sql += " AND device IN (%s)" % ",".join("?" * len(aliases))
        args.extend(aliases)
    sql += " ORDER BY start_utc_ms"
    return con.execute(sql, args).fetchall()


# ---- 主入口 -------------------------------------------------------------

def build_day_structure(con: sqlite3.Connection, date_key: str, device: str | None = None) -> dict:
    """读 event 表 <-> 输出当日结构；device=None 表示 pc+phone 合并到同一条时间轴。"""
    lo, hi = _day_window(date_key)
    rows = _fetch(con, lo, hi, device)

    locked_raw: list[Interval] = []
    away_raw: list[Interval] = []
    app_rows: list[tuple[int, int, sqlite3.Row]] = []
    screen_rows: list[sqlite3.Row] = []

    for r in rows:
        s = int(r["start_utc_ms"])
        e = int(r["end_utc_ms"]) if r["end_utc_ms"] is not None else s
        kind = r["kind"]
        # ★ 点事件（只有 start、没有 end）必须补一个默认时长，否则 ce>cs 恒为假、
        #   整条记录被静默丢弃。实测：shion 的 28,805 条 web 事件 100% 没有 end，
        #   导致「明明有 web 数据，日报里却一条都不显示」（用户 2026-10-21 指出）。
        #   截图类点事件不补（它们不参与时间线），只补 app/web；默认 60 秒与
        #   ManicTime 的截图采样粒度一致，且不会被误当成整段使用时长。
        if e <= s and kind in ("app", "web"):
            e = s + POINT_EVENT_MS
        if kind == "power":
            cls = power_class(r["title"])
            cs, ce = max(s, lo), min(e, hi)
            if cls and ce > cs:
                (locked_raw if cls == POWER_LOCKED else away_raw).append((cs, ce))
        elif kind == "screen":
            if lo <= s < hi:
                screen_rows.append(r)
        elif kind in ("app", "web"):
            # 手机侧（IQQZ3）把屏幕状态记成 kind='app'，必须在此拦下并转成 away/locked，
            # 否则「锁屏 18 小时」会被算成使用时长（实测占当天 app 时长 73%）。
            if kind == "app":
                cls = power_class(r["app"] or r["title"])
                if cls:
                    cs, ce = max(s, lo), min(e, hi)
                    if ce > cs:
                        (locked_raw if cls == POWER_LOCKED else away_raw).append((cs, ce))
                    continue
            cs, ce = max(s, lo), min(e, hi)
            if ce > cs:
                app_rows.append((cs, ce, r))

    locked = _merge(locked_raw)
    away = _subtract(_merge(away_raw), locked)     # 重叠部分只算一次（locked 优先）

    # ★ 用截图证伪「离开/锁定」区间。
    #   实测（2026-06-15 phone）：IQQZ3 报了一条 2026-06-12 23:41 → 06-16 15:53 的
    #   「屏幕关闭 (锁定)」（3.7 天），但该区间内有 965 张截图、覆盖全天各时段 ——
    #   这是 IQQZ3 的已知缺陷：App 被系统杀掉后，无活动记录的间隙被膨胀成超长锁屏。
    #   截图是「人确实在用这台设备」的直接证据，优先级高于推断出来的离开区间。
    #   规则：某段离开/锁定区间内若存在截图，则只保留「首张截图之前 + 末张截图之后」的部分，
    #   中间被截图覆盖的时段从离开区间中扣除。
    if screen_rows:
        shot_ts = sorted(int(r["start_utc_ms"]) for r in screen_rows)

        def _clip_by_screens(ivs: list[Interval]) -> list[Interval]:
            out_ivs: list[Interval] = []
            for a, b in ivs:
                inside = [t for t in shot_ts if a <= t < b]
                if not inside:
                    out_ivs.append((a, b))
                    continue
                first, last = inside[0], inside[-1]
                if first > a:
                    out_ivs.append((a, first))
                if b > last + 1:
                    out_ivs.append((last + 1, b))
            return out_ivs

        locked = _merge(_clip_by_screens(locked))
        away = _merge(_clip_by_screens(away))

    busy = _merge([*locked, *away])

    active: list[dict] = []
    for cs, ce, r in app_rows:
        name = _app_name(r)
        for a, b in _subtract([(cs, ce)], busy):
            if b > a:
                active.append({
                    "start": a, "end": b, "app": name, "app_id": r["app_id"],
                    "title": r["title"], "url": r["url"],
                })
    active.sort(key=lambda x: (x["start"], -x["end"]))

    # ---- apps ----
    # 只聚合**应用**事件。带 URL 的是网页访问，归下面的 websites。
    # 用户 2026-10-22 指出：「网站怎么和页面记录混在一起的」—— 旧实现把
    # www.bilibili.com / 127.0.0.1 这类域名当成应用排进「应用」榜，两个维度混成一张表。
    per_app: dict[str, dict] = {}
    for seg in active:
        if (seg.get("url") or "").strip().startswith(("http://", "https://")):
            continue
        # 浏览器内部页（chrome://settings/... 、edge://... ）不是「应用」。
        # 实测它带着整条 URL 当应用名，会把网站地址混进应用榜 ——
        # 形如「chrome://settings/content/siteDetails?site=https%3A%2F%2Fsijishecn.cc」。
        _appname = str(seg.get("app") or "").strip()
        if "://" in _appname:
            continue
        # 文件系统路径也不是「应用」。实测 2025-02-17 应用榜里混进
        # 「C:\Users\D-001\Desktop\日记\.obsidian\plugins」「C:\Users\D-001\Downloads」
        # —— 那是 ManicTime 文档时间线把路径记成了 app 名（当天 26 个应用里占 2 个）。
        if _is_path_name(_appname):
            continue
        key = seg["app"] or ""
        item = per_app.get(key)
        if item is None:
            item = per_app[key] = {"app": seg["app"], "app_id": seg["app_id"],
                                   "iv": [], "last_app_id": seg["app_id"]}
        item["iv"].append((seg["start"], seg["end"]))
        if not item["app_id"] and seg["app_id"]:
            item["app_id"] = seg["app_id"]

    apps: list[dict] = []
    for item in per_app.values():
        spans = _merge(item["iv"], FOCUS_GAP_MS)
        apps.append({
            "app": item["app"],
            "app_id": item["app_id"],
            "seconds": _length(_merge(item["iv"])) // 1000,
            "sessions": len(spans),
        })
    apps.sort(key=lambda x: (-x["seconds"], x["app"] or ""))

    # ---- websites：按域名聚合网页事件（与应用分开，各自成一个维度）----
    per_site: dict[str, list] = {}
    for seg in active:
        u = (seg.get("url") or "").strip()
        if not u.startswith(("http://", "https://")):
            continue
        # 域名**一律从 URL 提取**，不能信任 app 字段：
        # 实测 tai 源的 web 事件里 app 存的是**页面标题**（如「长评哪吒2登顶…_哔哩哔哩_bilibili」），
        # 直接当域名用会让网站榜变成标题榜（用户 2026-10-22 指出的「混在一起」）。
        dom = _domain_of(u)
        if not dom:
            continue      # 域名不合法（编码残片/中文标题），不进网站榜
        per_site.setdefault(dom, []).append((seg["start"], seg["end"]))
    websites: list[dict] = []
    for dom, iv in per_site.items():
        websites.append({"domain": dom,
                         "seconds": _length(_merge(iv)) // 1000,
                         "visits": len(_merge(iv, FOCUS_GAP_MS))})
    websites.sort(key=lambda x: (-x["seconds"], x["domain"] or ""))

    # ---- timeline（按 (app,title,url) 合并微缝隙）----
    tl: list[dict] = []
    for seg in active:
        last = tl[-1] if tl else None
        if (last is not None
                and seg["start"] - last["_e"] <= TIMELINE_GAP_MS
                and last["app"] == seg["app"]
                and last["title"] == seg["title"]
                and last["url"] == seg["url"]):
            last["_e"] = max(last["_e"], seg["end"])
            continue
        tl.append({"s": seg["start"], "e": seg["end"], "_e": seg["end"],
                   "app": seg["app"], "title": seg["title"], "url": seg["url"]})
    timeline = [{"start": _hms(x["s"]), "end": _hms(x["e"]), "app": x["app"],
                 "title": x["title"], "url": x["url"]} for x in tl]

    # ---- switch_count：app 级切换次数 ----
    seq: list[str | None] = []
    seq_end = 0
    for seg in active:
        if seq and seg["app"] == seq[-1] and seg["start"] - seq_end <= TIMELINE_GAP_MS:
            seq_end = max(seq_end, seg["end"])
            continue
        seq.append(seg["app"])
        seq_end = seg["end"]
    switch_count = sum(1 for i in range(1, len(seq)) if seq[i] != seq[i - 1])

    # ---- focus_blocks ----
    focus: list[dict] = []
    for item in per_app.values():
        for s, e in _merge(item["iv"], FOCUS_GAP_MS):
            secs = (e - s) // 1000
            if secs >= FOCUS_MIN_S:
                focus.append({"start": _hms(s), "end": _hms(e),
                              "app": item["app"], "seconds": secs})
    focus.sort(key=lambda x: (x["start"], x["app"] or ""))

    away_out = [{"start": _hms(s), "end": _hms(e), "reason": POWER_AWAY} for s, e in away]
    locked_out = [{"start": _hms(s), "end": _hms(e), "reason": POWER_LOCKED} for s, e in locked]
    away_all = sorted([*away_out, *locked_out], key=lambda x: (x["start"], x["reason"]))

    screens = [{"event_id": r["event_id"], "path": r["path"], "ts": int(r["start_utc_ms"])}
               for r in sorted(screen_rows, key=lambda r: int(r["start_utc_ms"]))]

    return {
        "date": date_key.strip(),
        "device": device,
        "apps": apps,
        "websites": websites,
        "timeline": timeline,
        "switch_count": switch_count,
        "focus_blocks": focus,
        "away": away_all,
        "screens": screens,
        "totals": {
            "active_s": _length(_merge([(s["start"], s["end"]) for s in active])) // 1000,
            "away_s": _length(away) // 1000,
            "locked_s": _length(locked) // 1000,
            # 有截图但无应用事件的日子（手机 App 被杀、只剩 ScreenMemo 在拍）会出现
            # 「active=0 却有几百张截图」的自相矛盾。用截图的 30 秒占用给一个下界估计，
            # 与 active_s 分开报，不污染「来自应用事件的真实时长」这一口径。
            "screen_active_lower_bound_s": len(screen_rows) * 30,
        },
    }


def write_app_timeline(con: sqlite3.Connection, slice_id: str, structure: dict) -> None:
    """把 DB 基线时间线写进 slice_result.app_timeline_json（无 result 行时先建骨架）。

    只写 structure["timeline"]（列名即口径）；调用方负责传入哪一段的时间线。
    落库即 commit —— 调用方（vision / publish）不需要再自己提交。
    """
    if not con.execute("SELECT 1 FROM slice WHERE slice_id = ?", (slice_id,)).fetchone():
        raise ValueError(
            f"未知 slice_id: {slice_id!r} —— 先用 slicer.build_slices() 建片，再写 DB 基线"
        )
    timeline = structure.get("timeline") if isinstance(structure, dict) else None
    if timeline is None:
        raise ValueError("structure 缺少 'timeline' 键：应传入 dbview.build_day_structure() 的返回值")
    con.execute(
        "INSERT INTO slice_result(slice_id, created_at) VALUES(?, ?) "
        "ON CONFLICT(slice_id) DO NOTHING",
        (slice_id, int(time.time() * 1000)),
    )
    con.execute(
        "UPDATE slice_result SET app_timeline_json = ? WHERE slice_id = ?",
        (json.dumps(timeline, ensure_ascii=False), slice_id),
    )
    con.commit()
