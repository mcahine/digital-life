"""生成「时段」单日网页：**一天一个 HTML**。

2026-10-22 用户裁决（推翻前一天的「每天一个文件夹 + 9 页」）：
    10-时段/ 里不要再以日期为名然后存多个 HTML 文件，直接一个 HTML 包含一天的信息，
    参考 00-Inbox/index.html 是怎么做的（除了文件命名不是对应日期，其他都挺好）。

产出：

    04-Areas/数字人生/10-时段/YYYY-MM-DD.html      ← 一天一页
    04-Areas/数字人生/10-时段/片层/YYYY-MM-DD/*.md ← 片层 md（publish 写，本脚本不动）

页面结构（照搬参考页）：
    header  日期 · 数字人生 + 星期 + 规模统计 + 主题按钮
    section 时间线 / 应用 / 网站 / 视觉，各带 h2 + 计数，按设备用 tab 切换
    右下    fab 目录浮钮 → 四个锚点

数据一律来自数据库（event / slice / slice_result），删掉那个 html 重跑即可重建。
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from digitallife import paths  # noqa: E402
from digitallife.dbview import build_day_structure  # noqa: E402
from slice_pages import base, apps, timeline, vision, web  # noqa: E402
from slice_pages.contract import (  # noqa: E402
    PC_DEVICE, PHONE_DEVICE, Ctx, device_label, load_slices,
)

SLICE_ROOT = paths.VAULT / "10-时段"
LEGACY_HTML = SLICE_ROOT / "_html"        # 2026-10-22 之前的单日页
LAYERED_DIR = SLICE_ROOT / "片层"          # 片层 md 的新家（旧位置是日期目录）

_WEEK = ("星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日")


def _day_devices(con: sqlite3.Connection, date_key: str) -> list[str]:
    """当日有片或有视觉结果的设备（按注册表顺序，保证页面稳定）。"""
    found: list[str] = []
    # 遍历**注册表里的全部设备**，不能写死两台。旧写法只认 PC_DEVICE / PHONE_DEVICE，
    # 加第二部手机后 phone-2 整天被漏掉（2026-10-22）。
    from digitallife import devices as _dv
    for dev in [d.device_id for d in _dv.all_devices()]:
        st = build_day_structure(con, date_key, dev)
        if st and (st.get("timeline") or st.get("apps")):
            found.append(dev)
    return found


def build_ctx(con: sqlite3.Connection, date_key: str) -> Ctx:
    devices = _day_devices(con, date_key)
    structures = {d: build_day_structure(con, date_key, d) for d in devices}
    slices = {d: load_slices(con, date_key, d) for d in devices}
    return Ctx(date_key=date_key, con=con, devices=devices,
               structures=structures, slices=slices)


def _counts(con: sqlite3.Connection, date_key: str) -> dict:
    from slice_pages.contract import day_range_ms
    lo, hi = day_range_ms(date_key)
    row = con.execute(
        "SELECT COUNT(*), SUM(CASE WHEN kind='web' THEN 1 ELSE 0 END), "
        "       SUM(CASE WHEN kind='screen' THEN 1 ELSE 0 END) "
        "FROM event WHERE start_utc_ms >= ? AND start_utc_ms < ?", (lo, hi)
    ).fetchone()
    sl = con.execute(
        "SELECT COUNT(*), COUNT(r.slice_id) FROM slice s "
        "LEFT JOIN slice_result r ON r.slice_id = s.slice_id "
        "WHERE s.start_utc_ms >= ? AND s.start_utc_ms < ?", (lo, hi)
    ).fetchone()
    return {"events": row[0] or 0, "web": row[1] or 0, "screens": row[2] or 0,
            "slices": sl[0] or 0, "slices_done": sl[1] or 0}


def _device_panels(ctx: Ctx, mod, *, order: list[str] | None = None) -> tuple[str, list, int]:
    """把一个 section 按设备拆成多个 panel + tab 控件。

    返回 (panels_html, tab_options, 合计计数)。tab_options 的 count 是各设备自己的行数。
    """
    devs = order if order is not None else ctx.devices
    if not devs:
        return "", [], 0
    # 先渲染全部设备，再丢掉 0 行的：实测 2025-02-17 网站段里
    # 「手机 0」这种 tab 点开只有一句「没有网页访问记录」，纯噪音。
    rendered: list[tuple[str, str, str, int]] = []
    for dev in devs:
        # 面板 id 必须**逐设备唯一**。旧写法取 dev.split("-")[0] 会把 phone-1 与
        # phone-2 都截成 "phone"，两个 panel 撞同一个 id —— 后来者覆盖前者，
        # 页面上直接少一台设备（2026-10-22 加第二部手机时踩到）。
        pid = "%s-%s" % (mod.ANCHOR, dev)
        page_ctx = Ctx(date_key=ctx.date_key, con=ctx.con, devices=ctx.devices,
                       structures=ctx.structures, slices=ctx.slices, device=dev)
        html, n = mod.panel(pid, page_ctx, hidden=False)
        if n:
            rendered.append((pid, device_label(dev), html, n))
    if not rendered:
        return "", [], 0
    # 只留下有内容的设备；第一个可见，其余 hidden 由 tab 切换
    panels = [html.replace('<div class="panel" id="%s">' % pid,
                           '<div class="panel" id="%s"%s>' % (pid, "" if i == 0 else " hidden"), 1)
              for i, (pid, _lab, html, _n) in enumerate(rendered)]
    opts = [(pid, lab, "{:,}".format(n)) for pid, lab, _h, n in rendered]
    total = sum(n for _p, _l, _h, n in rendered)
    return "".join(panels), opts, total


def _max_of(opts) -> int:
    """tab 选项里最大的那个计数（= 单设备最多有多少行）。"""
    vals = []
    for _pid, _lab, cnt in opts or []:
        try:
            vals.append(int(str(cnt).replace(",", "")))
        except (TypeError, ValueError):
            pass
    return max(vals) if vals else 0


def _section(anchor: str, title: str, count_text: str, body: str, seg_html: str = "") -> str:
    return ('<section class="section" id="%s"><div class="section-head">'
            '<h2>%s</h2><span class="count">%s</span>%s</div>%s</section>'
            % (base.esc(anchor), base.esc(title), base.esc(count_text), seg_html, body))


def render_day(ctx: Ctx, counts: dict) -> tuple[str, list]:
    """返回 (整页 HTML, 目录条目)。目录只列实际渲染出内容的 section。"""
    body: list[str] = []
    toc: list[tuple[str, str, str]] = []

    # ---- 时间线
    if ctx.devices:
        panels, opts, n = _device_panels(ctx, timeline)
        if n:
            body.append(_section(timeline.ANCHOR, timeline.LABEL, "{:,} 行".format(_max_of(opts)),
                                 panels, base.seg("timeline", opts)))
            toc.append((timeline.ANCHOR, timeline.LABEL, "{:,}".format(n)))

        # ---- 应用
        panels, opts, n = _device_panels(ctx, apps)
        if n:
            # 计数用「各设备最大值」而不是合计：合计 59 会比单设备面板里的 28 更醒目，
            # 但下面正文是逐设备的，两者并排就自相矛盾（用户 2026-10-22 截图即此）。
            body.append(_section(apps.ANCHOR, apps.LABEL, "{:,} 个".format(_max_of(opts)),
                                 panels, base.seg("apps", opts)))
            toc.append((apps.ANCHOR, apps.LABEL, "{:,}".format(n)))

        # ---- 网站
        panels, opts, n = _device_panels(ctx, web)
        if n:
            body.append(_section(web.ANCHOR, web.LABEL, "{:,} 项".format(_max_of(opts)),
                                 panels, base.seg("sites", opts)))
            toc.append((web.ANCHOR, web.LABEL, "{:,}".format(n)))

    # ---- 视觉（跨设备，不分 panel）
    vhtml, vtotal, vdone = vision.panel(ctx)
    if vtotal:
        body.append(_section(vision.ANCHOR, vision.LABEL,
                             "%d 片 · %d 片已生成" % (vtotal, vdone), vhtml))
        toc.append((vision.ANCHOR, vision.LABEL, "{:,}".format(vtotal)))

    weekday = _WEEK[datetime.strptime(ctx.date_key, "%Y-%m-%d").weekday()]
    stats = ('<p class="stats">'
             '<span><b>%s</b> 条原始事件</span>'
             '<span><b>%s</b> 次网页访问</span>'
             '<span>视觉 <b>%d / %d</b></span>'
             '</p>') % ("{:,}".format(counts["events"]), "{:,}".format(counts["web"]),
                        counts["slices_done"], counts["slices"])

    if not body:
        body.append('<p class="empty">这一天没有任何设备使用记录。</p>')

    html = base.page(date_key=ctx.date_key, weekday=weekday, stats_html=stats,
                     sections=toc, body="".join(body))
    return html, toc


def build_day(con: sqlite3.Connection, date_key: str, out_root: Path | None = None) -> dict:
    ctx = build_ctx(con, date_key)
    counts = _counts(con, date_key)
    html, toc = render_day(ctx, counts)
    target = (out_root or SLICE_ROOT) / (date_key + ".html")
    base.write(target, html)
    return {"path": target, "bytes": len(html.encode("utf-8")),
            "sections": [t[0] for t in toc], "counts": counts}


def _days(con: sqlite3.Connection) -> list[str]:
    return [r[0] for r in con.execute(
        "SELECT date_key FROM daily_summary ORDER BY date_key")]


def _prune_dirs() -> tuple[int, int]:
    """删掉旧的「日期目录」与 _html/（内容已全部并入 <日期>.html）。返回 (目录数, 文件数)。"""
    import shutil
    dirs = files = 0
    for p in SLICE_ROOT.iterdir():
        if not p.is_dir() or p.name == "片层":
            continue
        files += len(list(p.glob("*.html")))
        shutil.rmtree(p, ignore_errors=True)
        dirs += 1
    if LEGACY_HTML.exists():
        files += len(list(LEGACY_HTML.glob("*.html")))
        shutil.rmtree(LEGACY_HTML, ignore_errors=True)
        dirs += 1
    return dirs, files


def _build_one(date_key: str) -> dict:
    """在工作进程里构建一天（模块级函数才可 pickle）。自开连接，只读。"""
    import sqlite3 as _sq
    con = _sq.connect(paths.db_path(), timeout=30, check_same_thread=False)
    con.row_factory = _sq.Row
    try:
        con.execute("PRAGMA busy_timeout=30000")
        res = build_day(con, date_key)
        return {"date": date_key, "bytes": res["bytes"],
                "path": str(res["path"]), "sections": len(res["sections"]), "error": ""}
    except Exception as exc:  # noqa: BLE001
        return {"date": date_key, "bytes": 0, "path": "", "sections": 0,
                "error": "%s: %s" % (type(exc).__name__, str(exc)[:120])}
    finally:
        con.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=None, help="YYYY-MM-DD；省略则配 --all")
    ap.add_argument("--all", action="store_true", help="全部有日报的日期")
    ap.add_argument("--prune", action="store_true",
                    help="删除旧的日期目录与 _html/（内容已并入 <日期>.html）")
    ap.add_argument("--workers", type=int, default=1,
                    help="并发进程数；1 = 串行。实测 4 进程约 2.3 倍")
    args = ap.parse_args()

    con = sqlite3.connect(paths.db_path())
    con.row_factory = sqlite3.Row
    days = [args.date] if args.date else (_days(con) if args.all else [])
    if not days:
        print("未指定日期：用 --date YYYY-MM-DD 或 --all")
        con.close()
        return 2
    con.close()          # 每个进程自己开连接，父进程的用不上了

    ok = 0
    total_bytes = 0
    if args.workers > 1 and len(days) > 1:
        # 串行 1.18s/页 → 4 进程 0.51s/页（2.3×）。单页构建是纯 CPU 的字符串渲染，
        # 数据库只读，天然可分进程；753 页从 15 分钟降到 6.4 分钟。
        from concurrent.futures import ProcessPoolExecutor
        with ProcessPoolExecutor(max_workers=args.workers) as ex:
            for i, res in enumerate(ex.map(_build_one, days), 1):
                if res.get("error"):
                    print("  %s FAIL %s" % (res["date"], res["error"]), flush=True)
                else:
                    ok += 1
                    total_bytes += res["bytes"]
                if i % 100 == 0:
                    print("  ... %d/%d" % (i, len(days)), flush=True)
    else:
        for d in days:
            try:
                res = build_day(con, d)
                ok += 1
                total_bytes += res["bytes"]
                print("  %s -> %s (%.1f KB, %d 个 section)" % (
                    d, res["path"].name, res["bytes"] / 1024.0, len(res["sections"])))
            except Exception as exc:  # noqa: BLE001
                print("  %s FAIL %s: %s" % (d, type(exc).__name__, str(exc)[:120]))

    print("生成 %d 天单页 -> %s/<日期>.html（共 %.1f MB）"
          % (ok, SLICE_ROOT, total_bytes / 1024.0 / 1024.0))

    if args.prune:
        dirs, files = _prune_dirs()
        print("已删除旧结构：%d 个目录 / %d 个 html" % (dirs, files))

    con.close()
    return 0 if ok == len(days) else 1


if __name__ == "__main__":
    raise SystemExit(main())
