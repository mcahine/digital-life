"""digital-life 冒烟测试 —— 可完全脱离模型与真实源文件运行。

覆盖：schema/迁移、幂等、清洗规则、时间归一、FTS、切片守恒、DB 优先、构造性探查（S19）。
退出码 0 = 全通过；1 = 有失败（供 CI 与人工复核）。

用法：
    python scripts/smoke.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# S16：测试必须跑在独立临时库上。用 setdefault 会让外部 DL_DB 把上一次的残留带进来
# （实测：复用旧库时 T3「行数不变」因基数 54 而假红）。这里强制覆盖，不复用。
_TMP = tempfile.mkdtemp(prefix="dl-smoke-")
os.environ["DL_DB"] = str(Path(_TMP) / "smoke.sqlite")
os.environ["DL_WORK_DIR"] = str(Path(_TMP) / "work")

from digitallife import paths  # noqa: E402
from digitallife.clean import SourceReport, build_report, is_empty_event, merge_adjacent, normalize_event  # noqa: E402
from digitallife.model import Event  # noqa: E402
from digitallife.store import (  # noqa: E402
    count_events, events_between, open_db, search_events, time_span, upsert_events,
)
from digitallife.tz import (  # noqa: E402
    local_day, local_to_utc_ms, parse_flexible, parse_hms_to_seconds, utc_ms_to_local,
)

passed: list[str] = []
failed: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        passed.append(name)
        print(f"[PASS] {name}")
    else:
        failed.append(name)
        print(f"[FAIL] {name} {detail}")


def approx(a: int, b: int, tol: int = 1) -> bool:
    return abs(a - b) <= tol


def main() -> int:
    con = open_db()

    # ---------- T1 时间归一 ----------
    # 期望值来源（CP-4）：独立进程用 datetime 算的真实值
    #   datetime(2026,10,20,1,17,54, tz=+08:00).timestamp()*1000 == 1792430274000
    #   11*3600+49*60+40 == 42580
    check("T1 本地→UTC（UTC+8）", local_to_utc_ms(parse_flexible("2026-10-20 01:17:54")) == 1792430274000,
          str(local_to_utc_ms(parse_flexible("2026-10-20 01:17:54"))))
    check("T1b 日期分组用本地时区", local_day(local_to_utc_ms(parse_flexible("2026-10-20 01:17:54"))) == "2026-10-20")
    check("T1c hms → 秒", parse_hms_to_seconds("11:49:40") == 42580 and parse_hms_to_seconds("0:00:10") == 10)
    check("T1d 容忍多种本地串",
          parse_flexible("2025/1/4 15:56:23") == parse_flexible("2025-01-04 15:56:23")
          and parse_flexible("1/4/2025 15:56:23").month == 1)

    # ---------- T2 event_id 幂等 ----------
    e1 = Event(device="pc", source="manictime", kind="app", start_utc_ms=1_700_000_000_000,
               end_utc_ms=1_700_000_060_000, app="Chrome", title="t1")
    e1b = Event(device="pc", source="manictime", kind="app", start_utc_ms=1_700_000_000_000,
                end_utc_ms=1_700_000_060_000, app="Chrome", title="t1", payload={"x": 1})
    check("T2 同内容 event_id 相同（payload 不参与）", e1.event_id == e1b.event_id)
    e2 = Event(device="pc", source="manictime", kind="app", start_utc_ms=1_700_000_000_000,
               end_utc_ms=1_700_000_060_000, app="Chrome", title="t2")
    check("T2b 标题不同 → event_id 不同", e1.event_id != e2.event_id)
    e3 = Event(device="phone", source="manictime", kind="app", start_utc_ms=1_700_000_000_000,
               end_utc_ms=1_700_000_060_000, app="Chrome", title="t1")
    check("T2c 设备不同 → event_id 不同", e1.event_id != e3.event_id)

    # ---------- T3 幂等写入 ----------
    batch = [
        Event(device="pc", source="manictime", kind="app", start_utc_ms=1_700_000_000_000,
              end_utc_ms=1_700_000_060_000, app="Chrome", title="A"),
        Event(device="pc", source="manictime", kind="web", start_utc_ms=1_700_000_060_000,
              end_utc_ms=1_700_000_120_000, url="https://example.com/x"),
        Event(device="phone", source="iqqz3", kind="app", start_utc_ms=1_700_000_000_000,
              end_utc_ms=1_700_000_030_000, app="哔哩哔哩"),
    ]
    base_n = count_events(con)
    upsert_events(con, batch, "run1")
    con.commit()
    n1 = count_events(con) - base_n
    upsert_events(con, batch, "run2")
    con.commit()
    n2 = count_events(con) - base_n
    check("T3 连跑两次 event 行数不变", n1 == n2 == 3, f"{n1} vs {n2}（基线 {base_n}）")

    lo, hi = time_span(con)
    check("T3b 时间跨度", lo == 1_700_000_000_000 and hi == 1_700_000_120_000)
    check("T3c 按设备过滤", len(events_between(con, 1_699_999_999_000, 1_700_000_200_000, device="phone-1")) == 1)
    check("T3d 按 kind 过滤",
          len(events_between(con, 1_699_999_999_000, 1_700_000_200_000, kinds=("web",))) == 1)

    # ---------- T4 FTS ----------
    con.execute("INSERT INTO event_fts(event_fts) VALUES('rebuild')")
    con.commit()
    hits = search_events(con, "Chrome")
    check("T4 FTS 命中 app 名", len(hits) >= 1, str(len(hits)))
    check("T4b 空查询不扫全表", search_events(con, "  ") == [])

    # ---------- T5 清洗规则 ----------
    empty = Event(device="pc", source="tai", kind="app", start_utc_ms=1_700_000_000_000,
                  end_utc_ms=1_700_000_000_000)
    check("T5 C12 空事件判定", is_empty_event(empty) is True)
    named = Event(device="pc", source="tai", kind="app", start_utc_ms=1_700_000_000_000,
                  end_utc_ms=1_700_000_000_000, app="x")
    check("T5b C12 有标识不算空", is_empty_event(named) is False)

    a = Event(device="pc", source="manictime", kind="app", start_utc_ms=0, end_utc_ms=60_000, app="X", title="t")
    b = Event(device="pc", source="manictime", kind="app", start_utc_ms=120_000, end_utc_ms=180_000, app="X", title="t")
    c = Event(device="pc", source="manictime", kind="app", start_utc_ms=600_000, end_utc_ms=660_000, app="X", title="t")
    merged, mcount, mshort = merge_adjacent([a, b, c])
    check("T5c C4 间隔≤120s 合并", len(merged) == 2 and mcount == 1 and mshort == 0,
          f"len={len(merged)} merged={mcount} short={mshort}")
    check("T5d C4 合并后区间被拉长", merged[0].end_utc_ms == 180_000, str(merged[0].end_utc_ms))

    # 判定：默认**不丢**短事件（实测按 30s 过滤会丢 15.6% 真实使用时长，那不是去重是丢事实）。
    # 只有调用方显式传 min_duration_s 时才允许按阈值丢弃。
    # 判别力哨兵：app 与 web 交错时，同名 app 仍必须合并。
    # 单一合并流（旧的 out[-1] 写法）会因中间的 web 切断 app 的相邻关系而漏合并；
    # 实测在真实数据上表现为「应合 31,602 次却只合 687 次」。
    ia = Event(device="pc", source="manictime", kind="app", start_utc_ms=0, end_utc_ms=60_000,
               app="Obsidian", title="笔记")
    iw = Event(device="pc", source="manictime", kind="web", start_utc_ms=60_000, end_utc_ms=90_000,
               url="https://example.com")
    ib = Event(device="pc", source="manictime", kind="app", start_utc_ms=90_000, end_utc_ms=150_000,
               app="Obsidian", title="笔记")
    inter, icount, _ = merge_adjacent([ia, iw, ib])
    apps_left = [e for e in inter if e.kind == "app"]
    check("T5i 交错类型下同名 app 仍合并（按类型分流的判别哨兵）",
          icount == 1 and len(apps_left) == 1 and apps_left[0].end_utc_ms == 150_000,
          f"merged={icount} app段={len(apps_left)}")

    short = Event(device="pc", source="manictime", kind="app", start_utc_ms=0, end_utc_ms=5_000,
                  app="Y", title="t")
    kept_default, _, dropped_default = merge_adjacent([short])
    check("T5e 默认不丢弃短事件（保护 15.6% 真实时长）",
          dropped_default == 0 and len(kept_default) == 1,
          f"dropped={dropped_default} kept={len(kept_default)}")
    _, _, dropped_explicit = merge_adjacent([short], min_duration_s=30)
    check("T5h 显式传 min_duration_s=30 时才按阈值丢弃", dropped_explicit == 1, str(dropped_explicit))

    blank = Event(device="pc", source="tai", kind="app", start_utc_ms=0, end_utc_ms=None, app="  ", url="")
    normalize_event(blank)
    check("T5f C11 空串归 None", blank.app is None and blank.url is None, f"{blank.app!r}")

    rep = SourceReport(source="tai", device="pc-main")
    rep.rows_read = 10
    rep.keep(7)
    rep.drop("empty", 2)
    rep.drop("dup", 1)
    summary = build_report([rep])
    check("T5g 清洗报告可解释差额",
          summary["total_rows_read"] == 10 and summary["total_rows_kept"] == 7
          and summary["total_dropped"] == 3, str(summary)[:160])

    # ---------- T6 构造性探查（S19） ----------
    check("T6 未知 device 被拒", _raises(lambda: Event(device="tv", source="tai", kind="app", start_utc_ms=0)))
    check("T6b 未知 source 被拒", _raises(lambda: Event(device="pc", source="nope", kind="app", start_utc_ms=0)))
    check("T6c 未知 kind 被拒", _raises(lambda: Event(device="pc", source="tai", kind="nope", start_utc_ms=0)))
    check("T6d 结束早于开始被夹到 0 时长",
          Event(device="pc", source="tai", kind="app", start_utc_ms=100, end_utc_ms=50).duration_s == 0)
    check("T6e 越界区间查询返回空", events_between(con, 9_999_999_999_999, 9_999_999_999_999) == [])
    check("T6f 空批次写入返回 0", upsert_events(con, [], "run3") == 0)

    # ---------- T7 dbview 剔除离开时段（若模块已就绪） ----------
    try:
        from digitallife.dbview import build_day_structure
        con.execute("DELETE FROM event")
        # ★ S58 教训：断言必须能构造出缺陷的触发条件。原版让 app 事件 09:00-11:00 与
        #   Away 03:00-09:00 完全不重叠 —— 剔除与不剔除都得 7200，注入缺陷也不变红（实测复现）。
        #   现改为 **app 事件横跨 Away 区间**：app 04:00-12:00（8h），Away 03:00-09:00（6h），
        #   正确实现应剔除重叠的 5h，只剩 3h；不剔除则是 8h，二者可判别。
        day0 = local_to_utc_ms(parse_flexible("2026-10-20 00:00:00"))
        h = 3_600_000
        away_ev = Event(device="pc", source="manictime", kind="power",
                        start_utc_ms=day0 + 3 * h, end_utc_ms=day0 + 9 * h, title="Away")
        app_ev = Event(device="pc", source="manictime", kind="app",
                       start_utc_ms=day0 + 4 * h, end_utc_ms=day0 + 12 * h, app="Obsidian", title="笔记")
        upsert_events(con, [away_ev, app_ev], "run4")
        con.commit()
        st = build_day_structure(con, "2026-10-20")
        totals = st.get("totals") or {}
        act = totals.get("active_s", 0)
        check("T7 C13 active_s 剔除与 Away 重叠的时段",
              act == 3 * 3600 and totals.get("away_s", 0) == 6 * 3600,
              f"active={act}（期望 10800 = 8h - 5h 重叠）away={totals.get('away_s')}（期望 21600）")
        # 判别力哨兵：若有人把剔除关掉，active 会变成 8h=28800，下面这条必须变红
        check("T7d 剔除生效的判别哨兵（active 必须显著小于原始覆盖 8h）",
              act < 6 * 3600,
              f"active={act} 原始覆盖={8 * 3600}")
        check("T7b 结构视图字段齐备",
              all(k in st for k in ("apps", "timeline", "switch_count", "focus_blocks", "away", "totals")),
              str(sorted(st)))
        check("T7c 应用时长来自 event",
              any(a.get("app") == "Obsidian" for a in st.get("apps", [])), str(st.get("apps")))
    except ImportError:
        print("[SKIP] T7 dbview 尚未就绪")
    except Exception as e:  # noqa: BLE001
        check("T7 dbview 可用", False, f"{type(e).__name__}: {e}")

    # ---------- T8 slicer 截图守恒与二分（若模块已就绪） ----------
    try:
        from digitallife.slicer import MAX_IMAGES, build_slices
        con.execute("DELETE FROM event")
        con.execute("DELETE FROM slice")
        day0 = local_to_utc_ms(parse_flexible("2026-10-21 00:00:00"))
        evs = []
        for i in range(MAX_IMAGES * 2 + 5):
            evs.append(Event(device="pc", source="manictime", kind="screen",
                             start_utc_ms=day0 + i * 30_000, path=f"shots/{i}.webp"))
        evs.append(Event(device="pc", source="manictime", kind="app",
                         start_utc_ms=day0, end_utc_ms=day0 + 28 * 60_000,
                         app="Chrome", title="t"))
        upsert_events(con, evs, "run5")
        con.commit()
        out = build_slices(con, "pc-main", "2026-10-21")
        con.commit()
        total_imgs = sum(s.get("image_count", 0) for s in out)
        parents = [s for s in out if not s.get("split_of")]
        subs = [s for s in out if s.get("split_of")]
        # 语义（以 slicer.py:_plan_rows 实现为准）：父片保留自身 image_count 但标 status='split'，
        # 其截图由子片覆盖。因此「不超上限」约束的是**叶子片**，同时叶子必须守恒。
        leaves = [s for s in out if s.get("status") != "split"]
        total_shot = sum(1 for e in evs if e.kind == "screen")
        check("T8 叶子片截图数不超上限",
              all(s.get("image_count", 0) <= MAX_IMAGES for s in leaves),
              f"leaves={[s.get('image_count') for s in leaves]}")
        check("T8e 叶子截图守恒（总和不丢图）",
              sum(s.get("image_count", 0) for s in leaves) == total_shot,
              f"leaves_sum={sum(s.get('image_count', 0) for s in leaves)} total={total_shot}")
        check("T8b 超限触发二分子片", len(subs) >= 1, f"parents={len(parents)} subs={len(subs)}")
        n_before = con.execute("SELECT COUNT(*) FROM slice").fetchone()[0]
        build_slices(con, "pc-main", "2026-10-21")
        con.commit()
        n_after = con.execute("SELECT COUNT(*) FROM slice").fetchone()[0]
        check("T8c 切片幂等", n_before == n_after, f"{n_before} vs {n_after}")
        check("T8d 子片指回父片", all(s.get("split_of") for s in subs))
    except ImportError:
        print("[SKIP] T8 slicer 尚未就绪")
    except Exception as e:  # noqa: BLE001
        check("T8 slicer 可用", False, f"{type(e).__name__}: {e}")

    # ---------- T2d 判重键必须包含有语义的 payload 字段（真实缺陷回归）----------
    # 实测来源：iqqz3 有 10,089 组「同秒、同 Note、不同电量」的电池记录，
    # 判重键若只用 app|title 会合并掉 11,075 行真实数据（battery 112,073 → 112,014）。
    bat_a = Event(device="phone", source="iqqz3", kind="battery",
                  start_utc_ms=1_700_000_000_000, title="level changed, screen off",
                  payload={"battery_pct": 82})
    bat_b = Event(device="phone", source="iqqz3", kind="battery",
                  start_utc_ms=1_700_000_000_000, title="level changed, screen off",
                  payload={"battery_pct": 81})
    check("T2d 电池判重键含电量（同秒不同电量不得合并）",
          bat_a.event_id != bat_b.event_id,
          "两条被判为同一事件 → 真实数据会被合并")
    ntf_a = Event(device="phone", source="iqqz3", kind="notify",
                  start_utc_ms=1_700_000_000_000, app="微信", title="微信",
                  payload={"count": 1})
    ntf_b = Event(device="phone", source="iqqz3", kind="notify",
                  start_utc_ms=1_700_000_000_000, app="微信", title="微信",
                  payload={"count": 5})
    check("T2e 通知判重键含条数", ntf_a.event_id != ntf_b.event_id,
          "同一天同 App 不同条数会被合并")
    # 反向：payload 里无关字段变化**不应**产生新事件（保持幂等）
    ap_a = Event(device="pc", source="manictime", kind="app",
                 start_utc_ms=1_700_000_000_000, end_utc_ms=1_700_000_060_000,
                 app="Chrome", title="t", payload={"x": 1})
    ap_b = Event(device="pc", source="manictime", kind="app",
                 start_utc_ms=1_700_000_000_000, end_utc_ms=1_700_000_060_000,
                 app="Chrome", title="t", payload={"x": 2})
    check("T2f 无关 payload 变化不产生新事件（幂等不受影响）", ap_a.event_id == ap_b.event_id)

    # ---------- T12 真实数据缺陷回归：手机屏幕态 + 截图证伪离开区间 ----------
    try:
        from digitallife.dbview import build_day_structure, power_class
        # (a) IQQZ3 把屏幕状态记成 kind='app'，必须被识别为离开/锁定
        check("T12 手机屏幕关闭(锁定) 归为 locked", power_class("屏幕关闭 (锁定)") == "locked",
              str(power_class("屏幕关闭 (锁定)")))
        check("T12b 手机屏幕关闭(未锁定) 归为 away", power_class("屏幕关闭") == "away",
              str(power_class("屏幕关闭")))
        check("T12c 屏幕开启(解锁) 不算离开", power_class("屏幕开启 (解锁)") is None,
              str(power_class("屏幕开启 (解锁)")))
        check("T12d 设备关机归为 away（实测单条最长 76.7 天）",
              power_class("设备关机") == "away", str(power_class("设备关机")))

        # (b) 截图证伪：一条 3.7 天的假锁屏，若区间内有截图，必须被截断
        con.execute("DELETE FROM event")
        # ★ S58 教训：截图必须落在**被断言的那一天**里。初版把 7 张截图按 3.7 天均分，
        #   全落到 06-13，却断言 06-14 —— 触发条件根本没构造出来（实测 locked 仍 =86400）。
        #   现改为：假锁屏跨 06-12 23:41 → 06-16，截图**全部放在 06-14 当天**。
        d0 = local_to_utc_ms(parse_flexible("2026-06-12 23:41:00"))
        lock_end = d0 + int(3.7 * 86400 * 1000)
        day14 = local_to_utc_ms(parse_flexible("2026-06-14 00:00:00"))
        shots = [Event(device="phone", source="screenmemo", kind="screen",
                       start_utc_ms=day14 + h * 3_600_000, path=f"s{h}.webp")
                 for h in (2, 6, 9, 12, 15, 18, 21)]
        upsert_events(con, [
            Event(device="phone", source="iqqz3", kind="app",
                  start_utc_ms=d0, end_utc_ms=lock_end, app="屏幕关闭 (锁定)"),
            *shots,
        ], "run12")
        con.commit()
        st12 = build_day_structure(con, "2026-06-14", "phone-1")
        tot = st12["totals"]
        check("T12e 截图证伪假锁屏（3.7 天假锁屏不得整天记为 locked）",
              tot["locked_s"] < 86400,
              f"locked={tot['locked_s']}（若=86400 说明截图证伪未生效）")
        check("T12f 当日截图被读入（触发条件成立）",
              len(st12["screens"]) == 7, f"screens={len(st12['screens'])}")
        # 首张截图前的锁屏必须仍保留（不能把整段锁屏一抹了之）
        check("T12g 首张截图之前的锁屏仍保留",
              tot["locked_s"] >= 2 * 3600, f"locked={tot['locked_s']}")
    except ImportError:
        print("[SKIP] T12 dbview 尚未就绪")
    except Exception as e:  # noqa: BLE001
        check("T12 dbview 可用", False, f"{type(e).__name__}: {e}")

    # ---------- T11 冲突计数必须有判别力（若 vision 已就绪） ----------
    try:
        from digitallife.vision import _conflicts
        _base = [{"start": "09:00:00", "end": "09:15:00", "app": "Chrome", "title": "MDN"}]
        consistent_time = _conflicts({"timeline": [{"time": "09:05:00", "app": "Chrome", "action": "浏览"}]}, _base)[0]
        consistent_start = _conflicts({"timeline": [{"start": "09:05:00", "app": "Chrome"}]}, _base)[0]
        bad_app = _conflicts({"timeline": [{"time": "09:05:00", "app": "Slack"}]}, _base)[0]
        bad_time = _conflicts({"timeline": [{"time": "23:50:00", "app": "Chrome"}]}, _base)[0]
        empty = _conflicts({"timeline": []}, _base)[0]
        check("T11 与 DB 一致的 timeline 不计冲突（time 键）", consistent_time == 0, f"={consistent_time}")
        check("T11b 与 DB 一致的 timeline 不计冲突（start 键）", consistent_start == 0, f"={consistent_start}")
        check("T11c 应用瞎编必须计冲突", bad_app >= 1, f"={bad_app}")
        check("T11d 时间瞎编必须计冲突", bad_time >= 1, f"={bad_time}")
        check("T11e 空 timeline 不计冲突", empty == 0, f"={empty}")
        check("T11f 指标有判别力（冲突样例 > 一致样例）",
              bad_app > consistent_time and bad_time > consistent_time,
              f"一致={consistent_time} 应用错={bad_app} 时间错={bad_time}")
    except ImportError:
        print("[SKIP] T11 vision 尚未就绪")
    except Exception as e:  # noqa: BLE001
        check("T11 vision 可用", False, f"{type(e).__name__}: {e}")

    # ---------- T10 切片主轴：纯 power 窗不建片 + 跨设备同网格（若 slicer 已就绪） ----------
    try:
        from digitallife.slicer import build_slices as _bs
        con.execute("DELETE FROM event")
        con.execute("DELETE FROM slice")
        d = local_to_utc_ms(parse_flexible("2026-10-22 00:00:00"))
        hh = 3_600_000
        _evs = [
            # 00:00-08:00 只有 Away（8 个纯 power 窗）→ 不得建片
            *[Event(device="pc", source="manictime", kind="power",
                    start_utc_ms=d + i * hh, end_utc_ms=d + (i + 1) * hh, title="Away")
              for i in range(8)],
            # 08:00-09:00 有 app → 应建 4 个 15 分钟窗
            Event(device="pc", source="manictime", kind="app",
                  start_utc_ms=d + 8 * hh, end_utc_ms=d + 9 * hh, app="Obsidian", title="x"),
            # phone 15:10 起 → 应被拉到 15:00 网格
            Event(device="phone", source="iqqz3", kind="app",
                  start_utc_ms=d + 15 * hh + 10 * 60_000, end_utc_ms=d + 16 * hh, app="微信"),
        ]
        upsert_events(con, _evs, "run7")
        con.commit()
        pc_s = _bs(con, "pc-main", "2026-10-22")
        ph_s = _bs(con, "phone-1", "2026-10-22")
        con.commit()
        first_pc = min(s["start_utc_ms"] for s in pc_s)
        check("T10 纯 power 窗不建片（片只落在有实质事件的时段）",
              first_pc == d + 8 * hh and len(pc_s) == 4,
              f"首片偏移={(first_pc - d) // hh}h 片数={len(pc_s)}（期望 08:00 起 4 片）")
        grid_ok = all(s["start_utc_ms"] % 900_000 == 0 for s in [*pc_s, *ph_s])
        check("T10b 跨设备同 15 分钟 UTC 网格", grid_ok,
              str(sorted({s["start_utc_ms"] % 900_000 for s in [*pc_s, *ph_s]})))
        ph_first = min(s["start_utc_ms"] for s in ph_s)
        check("T10c 事件被拉到网格（15:10 → 15:00）",
              ph_first == d + 15 * hh, f"偏移={(ph_first - d) // 60_000} 分钟")
    except ImportError:
        print("[SKIP] T10 slicer 尚未就绪")
    except Exception as e:  # noqa: BLE001
        check("T10 slicer 可用", False, f"{type(e).__name__}: {e}")

    # ---------- T9 日记边界：跨设备分账 + 不写 50-回顾（若 daily 已就绪） ----------
    try:
        from digitallife.daily import build_daily
        con.execute("DELETE FROM event")
        con.execute("DELETE FROM daily_summary")
        d0 = local_to_utc_ms(parse_flexible("2026-10-20 09:00:00"))
        hh = 3_600_000
        upsert_events(con, [
            Event(device="pc", source="manictime", kind="app",
                  start_utc_ms=d0, end_utc_ms=d0 + 2 * hh, app="Obsidian", title="笔记"),
            Event(device="pc", source="manictime", kind="power",
                  start_utc_ms=d0, end_utc_ms=d0 + 2 * hh, title="Active"),
            Event(device="phone", source="iqqz3", kind="app",
                  start_utc_ms=d0 + hh, end_utc_ms=d0 + 2 * hh, app="哔哩哔哩"),
        ], "run6")
        con.commit()
        before = {p.name for p in paths.REVIEW_DIR.rglob("*.md")} if paths.REVIEW_DIR.is_dir() else set()
        res = build_daily(con, "2026-10-20")
        con.commit()
        after = {p.name for p in paths.REVIEW_DIR.rglob("*.md")} if paths.REVIEW_DIR.is_dir() else set()
        txt = res.get("output_text") or ""
        bd = res.get("device_breakdown") or {}
        devs = bd.get("devices") or {}
        check("T9 日报跨设备并列两节",
              ("电脑" in txt) and ("手机" in txt or "iQQQ" in txt), txt[:120])
        check("T9b 设备时长分账且不相加",
              {"pc-main", "phone-1"}.issubset(set(devs))
              and devs["pc-main"].get("active_s") == 7200 and devs["phone-1"].get("active_s") == 3600,
              str({k: v.get("active_s") for k, v in devs.items()}))
        check("T9c 不写 50-回顾（周期由日记系统负责）", before == after, f"新增 {sorted(after - before)}")
        check("T9d 日报不含周期粒度词", not any(k in txt for k in ("周记", "月记", "季记", "年记")))
        check("T9e daily_summary 只落日层", res.get("layer") == "day", str(res.get("layer")))
    except ImportError:
        print("[SKIP] T9 daily 尚未就绪")
    except Exception as e:  # noqa: BLE001
        check("T9 daily 可用", False, f"{type(e).__name__}: {e}")

    # ---------- T13 结构化校验器：打回重做的判据必须有判别力 ----------
    try:
        from digitallife.schema_check import validate_structured as _vs
        good = {
            "apps": [{"name": "Obsidian", "duration": "01:00:00", "note": "写作"}],
            "categories": ["办公"],
            "timeline": [{"start": "09:00:00", "end": "10:00:00", "app": "Obsidian", "title": "笔记"}],
            "key_actions": [{"range": "09:00:00-10:00:00", "action": "写作", "detail": "写日报"}],
            "described_images": [], "image_tags": [], "image_descriptions": [],
            # 日记体（2026-10-24）：每节是连贯叙述段落，不带时间戳前缀、不用列表。
            # 旧样例是「- 09:00:00-10:00:00：…」清单，改造后它本身就不该算合格。
            "overall_summary": (
                "## 关键操作\n先在 Obsidian 里打开当天要写的日报草稿，"
                "写到一半切到 Chrome 查了一会儿资料，随后又回到编辑器接着写。\n\n"
                "## 主要活动\n时间基本都花在撰写日报正文上，"
                "查资料和整理结构化字段只占了零头。\n\n"
                "## 重点内容\n正文最终写到约七百字，"
                "应用名一律引用真实名称，关键词覆盖也比较充分。\n"),
        }
        check("T13 合格样例通过校验", _vs(good).ok is True)
        bad_cases = [
            ("缺字段", {k: v for k, v in good.items() if k != "timeline"}, {}),
            ("类型错", {**good, "apps": "非数组"}, {}),
            ("总结过短", {**good, "overall_summary": "今天在用电脑。"}, {}),
            ("缺小节", {**good, "overall_summary": good["overall_summary"].replace("## 主要活动", "## 次要活动")}, {}),
            ("时间格式错", {**good, "timeline": [{"start": "9点", "end": "10点", "app": "Obsidian"}]}, {}),
            ("编造应用", {**good, "apps": [{"name": "Slack", "duration": "01:00:00"}]},
             {"db_apps": {"Obsidian", "Chrome"}}),
            ("图片未覆盖", {**good, "image_tags": [{"idx": 1, "tags": ["a"]}]},
             {"require_images": True, "image_count": 5}),
            ("空结构", {}, {}),
        ]
        for _name, _st, _kw in bad_cases:
            _v = _vs(_st, **_kw)
            check("T13 抓出「%s」" % _name, _v.ok is False,
                  str([e.message[:50] for e in _v.errors[:1]]))
        check("T13 打回理由含规则名", any(e.rule for e in _vs({}).errors))
        check("T13 打回理由非空", len(_vs({}).reason()) > 10)
    except ImportError:
        print("[SKIP] T13 schema_check 尚不可用")

    # ---------- T14 日报细节完整性与链接格式 ----------
    try:
        from digitallife.daily import _md_link, _sites_summary, _span_seconds
        # 链接格式：标题 + URL -> markdown
        check("T14a URL 渲染成 markdown 链接",
              _md_link("示例页", "https://example.com/a") == "[示例页](https://example.com/a)")
        # title 等于 url 时不得出现「url 作为锚文本原样重复」
        got = _md_link("https://example.com/a", "https://example.com/a")
        check("T14b title==url 时锚文本改用域名+路径",
              got == "[example.com/a](https://example.com/a)", got)
        # 非 http 不得产出坏链接
        check("T14c 非法 URL 退回纯文本", "](" not in _md_link("x", "ftp://x"))
        # 域名汇总必须按 url 聚合，且时长来自 start/end
        st = {"timeline": [
            {"app": "a.com", "title": "https://a.com/1", "url": "https://a.com/1",
             "start": "10:00:00", "end": "10:30:00"},
            {"app": "a.com", "title": "https://a.com/2", "url": "https://a.com/2",
             "start": "10:30:00", "end": "10:45:00"},
            {"app": "b.com", "title": "https://b.com/x", "url": "https://b.com/x",
             "start": "11:00:00", "end": "11:05:00"},
        ]}
        sites = _sites_summary(st)
        check("T14d 域名汇总按域名分组", {s["domain"] for s in sites} == {"a.com", "b.com"})
        a = next(s for s in sites if s["domain"] == "a.com")
        check("T14e 同时长累加（无 seconds 字段时由 start/end 反推）",
              int(a["seconds"]) == 2700, a["seconds"])
        check("T14f 代表页面取停留最久的那个", a["top_page"]["url"] == "https://a.com/1")
        check("T14g 跨午夜区间换算正确", _span_seconds("23:50:00", "00:10:00") == 1200.0)
        # 判别力：若把 seconds 读法改坏（恒为 0），T14e 必须能抓
        check("T14h 时长为 0 的边界不虚报", int(_span_seconds("10:00:00", "10:00:00")) == 0)
    except ImportError:
        print("[SKIP] T14 daily 工具函数不可用")

    # ---------- T15 IQQZ3 多快照不得互相覆盖 ----------
    try:
        import tempfile as _tf
        from pathlib import Path as _P
        from digitallife.sources.iqqz3 import Iqqz3Source as _IQ
        from digitallife.clean import SourceReport as _SR

        _tmp = _P(_tf.mkdtemp(prefix="dl-t15-"))
        _d = _tmp / "2025-01-05"
        _d.mkdir(parents=True)
        _hdr_a = "App name,Date,Time,Duration\n"
        # 两个快照：各含 1 条**不同**的 Activity
        (_d / "AUM_V4_Activity_2025-01-05_01-00-00.csv").write_text(
            _hdr_a + 'AppA,2025/1/5,10:00:00,0:05:00\n', encoding="utf-8")
        (_d / "AUM_V4_Activity_2025-01-05_09-00-00.csv").write_text(
            _hdr_a + 'AppB,2025/1/5,11:00:00,0:05:00\n', encoding="utf-8")
        (_d / "AUM_V4_App_2025-01-05_01-00-00.csv").write_text(
            "App name,Package\nAppA,com.a\nAppB,com.b\n", encoding="utf-8")

        _src = _IQ(root=_tmp)
        _evs = list(_src.iter_events(_SR(source="iqqz3", device="phone-1")))
        _apps = {e.app for e in _evs}
        check("T15a 两个快照的事件都被读到（不互相覆盖）",
              {"AppA", "AppB"} <= _apps, str(_apps))
        check("T15b 同一时间点重复内容按 event_id 去重",
              len([e for e in _evs if e.app == "AppA"]) == 1)
    except Exception as _e:  # noqa: BLE001
        check("T15 IQQZ3 多快照读取", False, f"{type(_e).__name__}: {_e}")

    # ---------- T16 设备注册表：多手机/多电脑必须可区分 ----------
    try:
        from digitallife import devices as _dv

        ids = [d.device_id for d in _dv.all_devices()]
        check("T16a 设备 id 不是 pc/phone 粗粒度值",
              "pc" not in ids and "phone" not in ids, str(ids))
        check("T16b 旧别名可解析（历史数据不失联）",
              _dv.get("pc").device_id == "pc-main" and _dv.get("phone").device_id == "phone-1")
        check("T16c 未注册设备直接报错并指路",
              _raises(lambda: _dv.get("phone-99")))
        # 关键：两台手机同属 phone 类别，但 id 不同 -> 重叠时段能分辨
        _p2 = _dv.Device("phone-9", "phone", "测试手机", name="TEST-PHONE")
        check("T16d 同类别设备可共存", _p2.kind == "phone" and _p2.device_id != "phone-1")
        # 环境变量覆盖导入归属
        import os as _os
        _old = _os.environ.get("DL_DEVICE_IQQZ3")
        _os.environ["DL_DEVICE_IQQZ3"] = "phone-1"
        check("T16e DL_DEVICE_<SOURCE> 可覆盖归属",
              _dv.resolve_for_source("iqqz3", "phone-1") == "phone-1")
        if _old is None:
            _os.environ.pop("DL_DEVICE_IQQZ3", None)
        else:
            _os.environ["DL_DEVICE_IQQZ3"] = _old
        # 事件构造时 device 归一化到注册 id
        _e = Event(device="pc", source="manictime", kind="app", start_utc_ms=1)
        check("T16f Event 把旧别名归一成注册 id", _e.device == "pc-main", _e.device)
        # 判别力：多手机并存时，同时间同内容的事件必须算两条（不同 event_id），
        # 否则「两部手机同一时段并行使用」会被静默合并成一条。
        _saved = _dv.DEVICES, dict(_dv._BY_ID)
        try:
            _dv.DEVICES = _dv.DEVICES + (_p2,)
            _dv._BY_ID["phone-9"] = _p2
            _a = Event(device="phone-1", source="iqqz3", kind="app", start_utc_ms=5, app="X")
            _b = Event(device="phone-9", source="iqqz3", kind="app", start_utc_ms=5, app="X")
            check("T16g 不同设备的相同时刻事件不互相去重", _a.event_id != _b.event_id)
            # 判别力：多手机时标题必须出现**设备真名**（iQQQZ3 / iQQQ Neo11），
            # 而不是笼统的「手机」。旧断言只数台数，改坏了也测不出来。
            _real = [_p for _p in _dv.by_kind("phone") if _p.device_id != "phone-9"]
            _names = [_dv.name_of(_p.device_id) for _p in _real]
            _labels = [_dv.label_of(_p.device_id) for _p in _real]
            check("T16h 多手机时用设备真名而非「手机」",
                  len(_real) >= 2 and len(set(_names)) == len(_names)
                  and all(n and n not in _labels for n in _names),
                  "names=%s labels=%s" % (_names, _labels))
        finally:
            _dv.DEVICES, _by = _saved
            _dv._BY_ID.clear()
            _dv._BY_ID.update(_by)
    except Exception as _e:  # noqa: BLE001
        check("T16 设备注册表", False, f"{type(_e).__name__}: {_e}")

    # ---------- T17 日报 markdown 格式：标题与引用块之间必须有空行 ----------
    try:
        from digitallife.daily import _render_device
        import re as _re
        # 造一个最小可渲染的当日结构（时间线含一条 5 分钟事件）
        _bd = {"event_count": 3, "slice_count": 0, "image_count": 0,
               "active_s": 300, "away_s": 0, "locked_s": 0, "summary_count": 0,
               "switch_count": 1, "kinds": {"app": 3}}
        _st = {"timeline": [{"start": "10:00:00", "end": "10:05:00",
                             "app": "X", "title": "t"}],
               "apps": [{"app": "X", "seconds": 300, "sessions": 1}],
               "focus_blocks": [], "away": []}
        _lines = _render_device("pc-main", _bd, _st, [])
        _txt = "\n".join(_lines)
        _m = _re.search(r"### 时间线（重要事件）\n(.*)", _txt)
        check("T17a 时间线标题存在", _m is not None)
        if _m:
            check("T17b 标题与引用块之间必须有空行（否则 > 紧贴标题）",
                  _m.group(1).strip() == "", repr(_m.group(1)[:40]))
        check("T17c 不得出现「标题\\n>」直接相连",
              "### 时间线（重要事件）\n>" not in _txt)
        # 判别力：两个设备标签不同，确认不是硬编码「手机」
        check("T17d 单机时标题沿用「电脑使用」", "## 电脑使用" in _txt, _txt[:30])
    except Exception as _e:  # noqa: BLE001
        check("T17 日报格式", False, f"{type(_e).__name__}: {_e}")

    # ---------- T18 设备别名必须贯穿所有查询（否则历史数据整类查不到） ----------
    try:
        from digitallife import devices as _dv2
        # 别名集合：一个注册 id 必须同时认旧值
        check("T18a query_aliases 含旧别名", set(_dv2.query_aliases("pc-main")) == {"pc-main", "pc"},
              str(_dv2.query_aliases("pc-main")))
        check("T18b phone 别名同理", set(_dv2.query_aliases("phone-1")) == {"phone-1", "phone"},
              str(_dv2.query_aliases("phone-1")))
        _cl, _al = _dv2.sql_device_filter("pc-main", "s.device")
        check("T18c SQL 片段用 IN 而非 =", " IN (" in _cl and "= ?" not in _cl, _cl)
        check("T18d 参数个数与占位符一致", _cl.count("?") == len(_al), f"{_cl} vs {_al}")

        # 端到端：**绕开 Event 的归一化**，直接按旧值 pc 写库，
        # 模拟「改造前导入的历史数据」，再按新 id 查询必须命中。
        # （注意：不能用 Event(device="pc") —— 它会被归一成 pc-main，测不出别名生效与否，
        #   这正是本断言第一版注入后不变红的真因，S58。）
        _eid = "smoke-t18-alias"
        con.execute(
            "INSERT OR REPLACE INTO event(event_id, device, source, kind, "
            "start_utc_ms, end_utc_ms, app, app_id, title, ingest_run) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (_eid, "pc", "manictime", "app", 1_700_000_000_000, 1_700_000_060_000,
             "AliasX", "AliasX", "alias-test", "smoke-t18"))
        con.commit()
        _got = events_between(con, 1_699_999_999_000, 1_700_000_100_000, device="pc-main")
        check("T18e 库内存旧值 pc，按新 id pc-main 能查到",
              any(r["app"] == "AliasX" for r in _got), f"got={len(_got)}")
        check("T18f 按旧值 pc 查同样能查到",
              any(r["app"] == "AliasX"
                  for r in events_between(con, 1_699_999_999_000, 1_700_000_100_000, device="pc")))
        con.execute("DELETE FROM event WHERE event_id=?", (_eid,))
        con.commit()
    except Exception as _e:  # noqa: BLE001
        check("T18 设备别名查询", False, f"{type(_e).__name__}: {_e}")

    # ---------- T19 时间线归并剪枝：索引版必须与两两比对语义等价 ----------
    # 背景：_drop_page_echoes 原先对每行与全部已保留行两两比对，单日 build_daily 里
    # 占 97% 耗时（2025-02-17 实测 14.57s/14.99s，identity() 被调 668,038 次）。
    # 2026-10-22 用户批准剪枝 → 改成按身份/标题倒排索引。
    # 下面这些用例是剪枝的**语义契约**：谁把候选顺序、代次失效、跨类型对齐改坏，
    # 谁就必须在这里变红（S58：先确认触发条件真被构造出来）。
    try:
        from digitallife.timeline_merge import _drop_page_echoes as _dpe

        _U = "https://www.bilibili.com/video/BV1FF"

        def _row(st, en, app, title, url="", link=""):
            return dict(start=st, end=en, app=app, title=title, url=url,
                        link_url=link, sub=[])

        def _n(rows):
            return len(_dpe([dict(r) for r in rows]))

        def _pair(off):
            # 重叠率按「较短者」的 90% 判；两个 60s 区间错开 off 秒
            return [
                _row("10:00:00", "10:01:00", "Google Chrome", _U, _U, _U),
                _row("10:00:%02d" % off, "10:01:%02d" % off, "Google Chrome", _U, _U, _U),
            ]

        check("T19a 重叠 90% 同身份 → 折成 1 行", _n(_pair(6)) == 1, str(_n(_pair(6))))
        check("T19b 重叠 89% → 保留 2 行", _n(_pair(7)) == 2, str(_n(_pair(7))))
        check("T19c 重叠 60% → 保留 2 行", _n(_pair(24)) == 2, str(_n(_pair(24))))
        _dj = [_row("10:00:00", "10:01:00", "Google Chrome", _U, _U, _U),
               _row("11:00:00", "11:01:00", "Google Chrome", _U, _U, _U)]
        check("T19d 同身份但当期不同 → 保留 2 行", _n(_dj) == 2, str(_n(_dj)))

        # 跨类型对齐：窗口行（无链接）与网页行（有链接）标题相同、重叠 90% → 折，
        # 且必须留下**有链接**的那条（链接是内容，不是表述）
        _xw = [_row("10:00:00", "10:01:00", "Google Chrome", "bilibili"),
               _row("10:00:02", "10:01:02", "tai", "bilibili", _U, _U)]
        _xr = _dpe([dict(r) for r in _xw])
        check("T19e 跨类型标题对齐 → 折成 1 行且留下有链接者",
              len(_xr) == 1 and str(_xr[0].get("link_url") or "") == _U, str(_xr))

        # 候选顺序：旧版取「插入槽位最小的第一个命中」。同身份必须能共存才触发。
        _od = [_row("10:00:00", "10:01:00", "tai", _U, _U, ""),
               _row("12:00:00", "12:01:00", "shion", _U, _U, ""),
               _row("10:00:00", "12:01:00", "tai", "long", _U, _U)]
        _orr = _dpe([dict(r) for r in _od])
        check("T19f 候选按插入顺序取第一个命中（替换槽位 0）",
              len(_orr) == 2 and _orr[0].get("title") == "long", str(_orr))

        # 代次失效：槽位被替换后，指向旧内容的索引项不得再命中
        # 第 1 行先占 (10:00:00-10:02:00) 且无链接；第 2 行与之重叠 100%、有链接 →
        # 整槽替换，槽位 span 收窄到 (10:01:00-10:01:20)。第 3 行落在**旧 span 内、新 span 外**：
        # 有代次检查 → 旧索引项作废，不折，共 2 行；无代次检查 → 被旧 span 误折，只剩 1 行。
        # （上一版 fixture 只断言行数，而「折行」不改行数，注入不变红 —— S58 同款坑。）
        _st = [_row("10:00:00", "10:02:00", "tai", _U, _U, ""),
               _row("10:01:00", "10:01:20", "tai", _U, _U, _U),
               _row("10:00:10", "10:00:20", "tai", _U, _U, _U)]
        check("T19g 槽位替换后旧索引项作废", _n(_st) == 2, str(_n(_st)))

        # 反向哨兵：剪枝不得退化回 O(n²)。3000 行宽松上限 10s，
        # 两两比对需数百万次 identity（每次 ~14µs）→ 分钟级，阈值足以判别。
        import random as _rnd
        import time as _t
        _rnd.seed(20261022)
        _big = []
        for _i in range(3000):
            _s0 = _rnd.randint(0, 80000)
            _d = _rnd.choice([0, 1, 5, 30, 120, 600])
            _u = "https://%s/p%d" % (_rnd.choice(["a.com", "www.b.com", "c.cn"]), _rnd.randint(0, 60))
            _big.append(_row("%02d:%02d:%02d" % (_s0 // 3600, (_s0 % 3600) // 60, _s0 % 60),
                             "%02d:%02d:%02d" % ((_s0 + _d) // 3600, ((_s0 + _d) % 3600) // 60, (_s0 + _d) % 60),
                             "tai", _u, _u, _u))
        _t0 = _t.time()
        _bn = len(_dpe(_big))
        _dt = _t.time() - _t0
        check("T19h 3000 行剪枝不得退化（<10s）", _dt < 10.0, "%.2fs rows=%d" % (_dt, _bn))

        # 锚文本去重：app 带 www.、标题不带（或反向）时不得把同一域名说两遍。
        # 实测残留：`www.bilibili.com · bilibili.com · BV1SZgG67Efs`（2026-08-13 共 16 处）。
        from digitallife.timeline_merge import display_title as _dt_
        check("T19i www 变体不得重复域名",
              _dt_({"app": "www.bilibili.com", "title": "bilibili.com · BV1SZgG67Efs"})
              == "bilibili.com · BV1SZgG67Efs",
              _dt_({"app": "www.bilibili.com", "title": "bilibili.com · BV1SZgG67Efs"}))
        check("T19j 不同域名仍要带 app 前缀",
              _dt_({"app": "Microsoft Edge", "title": "Kimi.ai"}) == "Microsoft Edge · Kimi.ai",
              _dt_({"app": "Microsoft Edge", "title": "Kimi.ai"}))
    except Exception as _e:  # noqa: BLE001
        check("T19 时间线归并剪枝", False, f"{type(_e).__name__}: {_e}")

    # ---------- T20 单日页（10-时段/<日期>.html，一天一页）----------
    # 2026-10-22 用户裁决：不要「日期目录 + 9 个 html」，一天一个文件。
    # 这里锁三件事：单页路径、四段结构、零行设备不出 tab。
    try:
        import re as _re2
        from slice_pages import base as _pb, apps as _apps, web as _web
        from slice_pages.contract import Ctx as _Ctx

        # T20a 转义必须真的转义（S9）
        check("T20a base.esc 转义尖括号与引号",
              _pb.esc('<b x="1">&') == "&lt;b x=&quot;1&quot;&gt;&amp;", _pb.esc('<b x="1">&'))

        # T20b 设备 tab：少于 2 个选项时不出控件（单设备日整条不渲染）
        check("T20b 单选项不出 tab", _pb.seg("x", [("x-pc", "电脑", "1")]) == "")
        seg2 = _pb.seg("x", [("x-pc", "电脑", "3"), ("x-phone", "手机", "2")])
        check("T20c 双选项出 tablist 且首项 selected",
              'role="tablist"' in seg2 and 'aria-selected="true"' in seg2
              and seg2.count('role="tab"') == 2, seg2[:120])

        # T20d 空设备面板返回 0 行（编排器据此丢弃 tab）
        _empty = _Ctx(date_key="2026-08-13", con=con, devices=[], structures={}, slices={}, device="pc-main")
        _h, _n = _apps.panel("apps-pc", _empty)
        check("T20d 无应用时返回 0 行", _n == 0, str(_n))

        # T20e 有数据时行数如实（构造 3 个应用）
        _st = {"apps": [{"app": "A", "seconds": 30, "sessions": 1},
                        {"app": "B", "seconds": 20, "sessions": 1},
                        {"app": "C", "seconds": 10, "sessions": 1}], "switch_count": 2}
        _ctx2 = _Ctx(date_key="2026-08-13", con=con, devices=["pc-main"],
                     structures={"pc-main": _st}, slices={}, device="pc-main")
        _h2, _n2 = _apps.panel("apps-pc", _ctx2)
        check("T20e 三个应用 -> 3 行", _n2 == 3, str(_n2))

        # T20f 网站域名过滤：非法域名不得进榜，中文标题（tai 源 app）也不得顶替域名
        _st3 = {"timeline": [
            {"start": "10:00:00", "end": "10:01:00", "app": "长评哪吒2登顶_哔哩哔哩",
             "title": "x", "url": "https://www.bilibili.com/video/BV1"},
            {"start": "10:01:00", "end": "10:02:00", "app": "x", "title": "y",
             "url": "https://example.com/a"},
        ]}
        _ctx3 = _Ctx(date_key="2026-08-13", con=con, devices=["pc-main"],
                     structures={"pc-main": _st3}, slices={}, device="pc-main")
        _h3, _n3 = _web.panel("sites-pc", _ctx3)
        check("T20f 域名从 URL 提取（不取中文标题）",
              "bilibili.com" in _h3 and "长评哪吒2登顶" not in _h3 and _n3 == 2, str(_n3))

        # T20g 设备名必须用真实设备名（用户 2026-10-22：电脑 = D-001，手机 = iQQQZ3）
        from slice_pages.contract import device_label as _dl
        check("T20g 设备名用真名而非「电脑/手机」",
              _dl("pc-main") == "D-001" and _dl("phone-1") == "iQQQZ3",
              "%s / %s" % (_dl("pc-main"), _dl("phone-1")))

        # T20h 提示段落不得复用 .bar —— 那个类名是「百分比横条」，
        # 撞名会把整段文字染成一条蓝色胶囊（用户截图「蓝条是什么鬼」）。
        check("T20h 提示段落用 .lead，base.CSS 里没有 .bar 横条",
              ".lead{" in _pb.CSS and ".bar{" not in _pb.CSS,
              [k for k in (".lead{", ".bar{") if k in _pb.CSS])

        # T20i 网站访问的锚文本不得整条 URL 上屏（页面级记录要能读出来）
        _st4 = {"timeline": [
            {"start": "10:00:00", "end": "10:01:00", "app": "bilibili.com",
             "title": "https://www.bilibili.com/video/BV1", "url": "https://www.bilibili.com/video/BV1"},
            {"start": "10:01:00", "end": "10:02:00", "app": "bilibili.com",
             "title": "解释合取谬误 - 豆包", "url": "https://www.doubao.com/chat/x"},
        ]}
        _ctx4 = _Ctx(date_key="2026-08-13", con=con, devices=["pc-main"],
                     structures={"pc-main": _st4}, slices={}, device="pc-main")
        _h4, _n4 = _web.panel("sites-pc", _ctx4)
        _labels = _re2.findall(r'rel="noreferrer" title="[^"]*">([^<]+)</a>', _h4)
        check("T20i 无标题时退成「域名 · 路径末段」，不整条 URL",
              _labels and not any(x.startswith(("http://", "https://")) for x in _labels)
              and any("· BV1" in x for x in _labels)
              and any("解释合取谬误" in x for x in _labels), str(_labels))

        # T20j 窗口栏噪声必须清掉（「和另外 N 个页面」「- 个人」）
        from digitallife.timeline_merge import _clean_title as _ct
        _raw = "…_哔哩哔哩_bilibili 和另外 5 个页面 - 个人 - Google Chrome"
        check("T20j 去掉「和另外 N 个页面/- 个人/浏览器名」",
              _ct(_raw) == "…_哔哩哔哩_bilibili", _ct(_raw))
    except Exception as _e:  # noqa: BLE001
        check("T20 单日页", False, f"{type(_e).__name__}: {_e}")

    # ---------- T26 日报也必须标注无画面描述的片 ----------
    # T25 只覆盖了时段页。日报里每片同样列总结，不加标注同样会误导。
    try:
        from digitallife.daily import _no_visual_basis as _nvb
        check("T26a 日报：无描述 -> 标注",
              _nvb({"has_description": False}) is True)
        check("T26b 日报：有描述 -> 不标注",
              _nvb({"has_description": True}) is False)
        check("T26c 日报：字段缺失时安全返回 False", _nvb({}) is False)
        # 时段页与日报两个实现必须口径一致
        import importlib as _il2
        _v2 = _il2.import_module("slice_pages.vision")
        _probe = {"has_description": False}
        check("T26d 两个实现口径一致",
              _v2._no_visual_basis(_probe) is _nvb(_probe))
    except Exception as _e:  # noqa: BLE001
        check("T26 日报无描述标注", False, f"{type(_e).__name__}: {_e}")

    # ---------- T25 无画面描述的片必须在页面上写明 ----------
    # 2026-10-23 用户视角检查：13% 的片没有画面描述，总结纯靠 DB 时间线，
    # 但页面上没有任何提示 —— 读者会以为模型看过画面。
    try:
        import importlib as _il
        _vis = _il.import_module("slice_pages.vision")
        # 判据已改为事件层的 has_description（structured.described_images
        # 在纯文本模式下恒空，用它判断会把所有片都标成无描述）。
        check("T25a 有描述时不标注",
              _vis._no_visual_basis({"has_description": True}) is False)
        check("T25b 无描述时标注",
              _vis._no_visual_basis({"has_description": False}) is True)
        check("T25c 字段缺失时不标注（宁可不标也不错标）",
              _vis._no_visual_basis({}) is False)
        check("T25d 字段为 None 时不标注",
              _vis._no_visual_basis({"has_description": None}) is False)
    except Exception as _e:  # noqa: BLE001
        check("T25 无描述标注", False, f"{type(_e).__name__}: {_e}")

    # ---------- T24 build_daily 必须容忍裸连接（无 row_factory） ----------
    # 2026-10-23 实测：daily.py 内部大量用 r["col"]，隐含要求 row_factory。
    # 裸 sqlite3.connect() 调进来会崩在
    #   ValueError: dictionary update sequence element #0 has length 16
    #   TypeError: tuple indices must be integers or slices, not str
    # 崩了之后日报内容停留在旧状态 —— 表现为「库里有 52 片，日报说 0 片」。
    try:
        import sqlite3 as _sq
        from digitallife.daily import build_daily as _bd
        _c2 = _sq.connect(paths.db_path())
        try:
            check("T24a 裸连接进来时 row_factory 被兜底",
                  _c2.row_factory is None, "前置：构造无 row_factory 的连接")
            # 冒烟跑在独立临时库上（S16），没有生产数据，产出会很短。
            # 所以判据是「**不抛异常且返回结构化结果**」，不是字数。
            # 真正的字数验证在真库上做（见 runbook 记录：裸连接 28,876 字）。
            _r2 = _bd(_c2, "2026-07-15")
            check("T24b 裸连接不抛异常并返回结果",
                  isinstance(_r2, dict) and "output_text" in _r2,
                  str(type(_r2)))
            check("T24c 调用后 row_factory 已被设为 Row",
                  _c2.row_factory is _sq.Row, str(_c2.row_factory))
            _c2.rollback()
        finally:
            _c2.close()
    except Exception as _e:  # noqa: BLE001
        check("T24 裸连接兜底", False, f"{type(_e).__name__}: {_e}")

    # ---------- T23 日报里的时段页链接必须指向真实结构 ----------
    # 2026-10-22 结构从「日期目录 + 9 页」并成「一天一个 html」，
    # 但 daily.py 的指路链接仍写 10-时段/<日期>/apps-phone.html —— 死链。
    # 2026-10-23 实测发现并修复。
    try:
        import pathlib as _pl
        import re as _re
        from digitallife import paths as _paths
        _v = _pl.Path(_paths.VAULT)
        _f = _v / "10-时段" / "2026-08-07.html"
        if _f.exists():
            _txt = _f.read_text(encoding="utf-8")
            check("T23a 单日页锚点存在",
                  'id="apps"' in _txt and 'id="sites"' in _txt)
        else:
            check("T23a 单日页锚点存在", True, "样本日不存在，跳过")
        # 反向哨兵：旧式日期目录链接不得再出现
        from digitallife.daily import _top_apps_section
        _sec = _top_apps_section({"apps": [{"app": "X", "seconds": 60}]},
                                 "2026-08-07", "apps-phone.html")
        _j = "\n".join(_sec)
        check("T23b 指路链接不用旧式日期目录",
              not _re.search(r"10-时段/\d{4}-\d{2}-\d{2}/", _j), _j[-120:])
        check("T23c 指路链接指向单文件 + 锚点",
              "2026-08-07.html#apps" in _j, _j[-120:])
    except Exception as _e:  # noqa: BLE001
        check("T23 日报链接结构", False, f"{type(_e).__name__}: {_e}")

    # ---------- T22 max_tokens 放大不得突破上游硬上限 ----------
    # 实测报错（2026-10-23，批次里 2 次）：
    #   {"error":"field MaxTokens invalid, should be in [1, 65536]"}
    # 根因：空内容自救重试 bigger = max(max_tokens*4, 4096) 没有封顶，
    # 调用方已按 65536 发送时会被放大成 262,144 → 400 → 不可重试 → 整片 FAIL。
    try:
        from digitallife.llm import (MAX_TOKENS_CAP, ChatClient, ChatConfig,
                                     EmptyContentError)
        check("T22a 上限常量为 65536", MAX_TOKENS_CAP == 65536, str(MAX_TOKENS_CAP))

        def _drive(start_budget):
            """驱动一次自救重试，返回两次实际发出的 max_tokens。"""
            cfg = ChatConfig(base_url="https://x/v1/chat/completions", api_key="k",
                             model="m", timeout=1.0)
            cli = ChatClient(cfg)
            seen = []

            def _post(payload):
                seen.append(payload.get("max_tokens"))
                if len(seen) == 1:
                    raise EmptyContentError("starved", reasoning="r", starved=True)
                return {"content": "ok", "model": "m"}

            cli._post = _post
            cli._send([{"role": "user", "content": "x"}], start_budget, None)
            return seen

        check("T22b 已到上限时不再放大", _drive(65536) == [65536, 65536],
              str(_drive(65536)))
        check("T22c 小预算仍放大到 4 倍", _drive(4096) == [4096, 16384],
              str(_drive(4096)))
    except Exception as _e:  # noqa: BLE001
        check("T22 max_tokens 上限", False, f"{type(_e).__name__}: {_e}")

    # ---------- T20k 多设备面板 id 必须逐设备唯一 ----------
    # 旧写法 dev.split("-")[0] 把 phone-1 / phone-2 都截成 "phone"，
    # 两个 panel 撞同一个 id，页面上直接少一台设备。
    try:
        from scripts.build_slice_html import _device_panels  # noqa: F401
        import scripts.build_slice_html as _bsh
        from slice_pages import apps as _ap
        _st = {"apps": [{"app": "X", "seconds": 10, "sessions": 1}], "switch_count": 0}
        _c = _Ctx(date_key="2026-08-07", con=con, devices=["phone-1", "phone-2"],
                  structures={"phone-1": _st, "phone-2": _st}, slices={})
        _html, _opts, _n = _bsh._device_panels(_c, _ap)
        _pids = [o[0] for o in _opts]
        check("T20k 两台手机的面板 id 不撞号",
              len(_pids) == 2 and len(set(_pids)) == 2, str(_pids))
    except Exception as _e:  # noqa: BLE001
        check("T20k 多设备面板 id", False, f"{type(_e).__name__}: {_e}")

    # ---------- T21 多手机：设备真名 + 手机侧视觉通道 ----------
    # 用户 2026-10-22 澄清 iQQQ Neo11 与 iQQQZ3 是两台不同的手机，
    # 且手机视觉总结只用文字（ScreenMemo 的 ai_image_meta.description），不引截图。
    try:
        from digitallife import devices as _dv3
        from digitallife.model import DEVICES as _DEVS
        check("T21a 两台手机是两个独立设备",
              "phone-1" in _DEVS and "phone-2" in _DEVS, str(_DEVS))
        check("T21b 设备真名分开", _dv3.name_of("phone-1") != _dv3.name_of("phone-2"),
              "%s vs %s" % (_dv3.name_of("phone-1"), _dv3.name_of("phone-2")))
        check("T21c 旧别名只映射到 phone-1，不吞掉 phone-2",
              _dv3.query_aliases("phone-2") == ["phone-2"],
              str(_dv3.query_aliases("phone-2")))

        # 视觉取文字原料：payload 是 dict 时也必须能取到 description
        # （实测 bug：只写 json.loads 会 TypeError，描述全丢，整片退化成「无观察来源」）
        from digitallife.vision import _collect_descriptions
        _ev = [{"kind": "screen", "start_utc_ms": 1_700_000_000_000, "app": "A",
                "payload": {"description": "界面显示了书架", "tags": ["阅读"]}}]
        _got = _collect_descriptions(_ev)
        check("T21d payload 为 dict 时能取到画面描述",
              len(_got) == 1 and "书架" in _got[0]["description"], str(_got)[:120])
        check("T21e payload 为 JSON 字符串时同样能取到",
              len(_collect_descriptions([{"kind": "screen", "start_utc_ms": 1,
                                          "payload": '{"description": "x"}'}])) == 1)
    except Exception as _e:  # noqa: BLE001
        check("T21 多手机", False, f"{type(_e).__name__}: {_e}")

    # ---------- T27 图片真的送到了模型（修「盲总结」后的常驻哨兵） ----------
    # 2026-10-23 实测：vision 已把截图读成字节并编成 data URL，但 _chat 传的是
    # zip 内的**相对成员名**（Screenshots/xxx.webp）。chat_images 对它做
    # Path(p).read_bytes() 必抛 OSError → 记 missing 跳过 → images_sent=0。
    # 库里 1,874 条 slice_result 就是这么"盲总结"出来的，而两层计数器互相看不见
    # （vision 层说"收集到 23 张"，llm 层说"缺失 23 张"），日志一路全绿。
    try:
        import base64 as _b64
        import io as _io
        from digitallife.llm import ChatClient as _CC
        from digitallife import vision as _vi

        _tiny = "data:image/webp;base64," + _b64.b64encode(b"RIFF0000WEBPVP8 ").decode()
        _cc = _CC.__new__(_CC)          # 不跑 __init__，绝不出网
        _cc._send = lambda messages, mt, th=None: {"content": "{}", "usage": {}}
        _r = _cc.chat_images("p", [_tiny], max_tokens=64)
        check("T27a data URL 不会被当成缺文件丢掉",
              _r.get("images_sent") == 1 and _r.get("images_skipped", {}).get("missing") == 0,
              "sent=%s skipped=%s" % (_r.get("images_sent"), _r.get("images_skipped")))

        import tempfile as _tf
        with _tf.NamedTemporaryFile(suffix=".webp", delete=False) as _f:
            _f.write(b"RIFF0000WEBPVP8 ")
            _name = _f.name
        _r2 = _cc.chat_images("p", [_name], max_tokens=64)
        check("T27b 磁盘真实路径仍然可送", _r2.get("images_sent") == 1,
              str(_r2.get("images_skipped")))
        os.unlink(_name)

        _cap = {}

        class _Stub:
            def chat(self, messages, max_tokens=0, thinking=None):
                return {"content": "{}"}

            def chat_images(self, text, image_paths, max_tokens=0, thinking=None):
                _cap["p"] = list(image_paths)
                return {"content": "{}"}

        _vi._chat(_Stub(), "p", [{"url": _tiny, "path": "Screenshots/x.webp"}], max_tokens=64)
        check("T27c _chat 传的是 data URL 而不是 zip 内相对路径",
              _cap.get("p") == [_tiny], str(_cap.get("p"))[:80])

        try:
            from PIL import Image as _Img
            _b1 = _io.BytesIO()
            _Img.new("RGB", (1920, 1080)).save(_b1, "WEBP")
            _w, _h = _Img.open(_io.BytesIO(_vi._downscale(_b1.getvalue()))).size
            check("T27d 缩图后长边不超过上限",
                  max(_w, _h) <= _vi.IMAGE_MAX_SIDE, "%dx%d" % (_w, _h))
            _b2 = _io.BytesIO()
            _Img.new("RGB", (200, 160)).save(_b2, "WEBP")
            _small = _b2.getvalue()
            check("T27e 小图原样返回不做放大", _vi._downscale(_small) == _small)
        except ImportError:
            pass
    except Exception as _e:  # noqa: BLE001
        check("T27 视觉图片送达", False, f"{type(_e).__name__}: {_e}")

    # ---------- T28 送模型的图片张数必须打顶 ----------
    # 实测（2026-10-23，同一片 18 图）：n=16 → 上游 400 "sensitive image"（2/2 次复现）；
    # n=8 → 108s/68s 正常出 8185/5718 字；n=4 → 107s 且推理 8089 token。
    # 即耗时由推理 token 决定、与张数无关，但张数决定会不会被上游判敏感。
    try:
        from digitallife.vision import MAX_SEND_IMAGES, _sample_evenly

        check("T28a 单片送图张数有上限", 0 < MAX_SEND_IMAGES <= 12, str(MAX_SEND_IMAGES))
        _arr = [{"n": i} for i in range(18)]
        _kept, _drop = _sample_evenly(_arr, 8)
        check("T28b 超限时抽样到上限", len(_kept) == 8 and _drop == 10,
              "kept=%d drop=%d" % (len(_kept), _drop))
        check("T28c 抽样保留首尾（最先与最后的画面都不能丢）",
              _kept[0]["n"] == 0 and _kept[-1]["n"] == 17,
              "%s..%s" % (_kept[0]["n"], _kept[-1]["n"]))
        check("T28d 抽样结果无重复", len({k["n"] for k in _kept}) == len(_kept))
        _small = [{"n": i} for i in range(5)]
        _k2, _d2 = _sample_evenly(_small, 8)
        check("T28e 未超限时原样返回", _k2 is _small and _d2 == 0)
    except Exception as _e:  # noqa: BLE001
        check("T28 送图上限", False, f"{type(_e).__name__}: {_e}")

    # ---------- T29 起始 max_tokens 必须小，只在空内容时才放大 ----------
    # 实测（2026-10-23，同一片 8 图，交替各 2 次）：
    #   mt=65536 → 269.9s / 217.0s，推理 23,439 / 20,568 token
    #   mt=8192  → 189.9s /  71.4s，推理  1,749 /  1,383 token
    # 预算越大模型越展开长篇推理，耗时翻 3–4 倍，而输出字数并没有变多。
    try:
        from digitallife import vision as _v3

        check("T29a 起始预算远低于硬上限",
              _v3.MAX_TOKENS <= 16384 and _v3.MAX_TOKENS_CEILING == 65536,
              "start=%s ceiling=%s" % (_v3.MAX_TOKENS, _v3.MAX_TOKENS_CEILING))
        _seq = []
        for _t in range(1, 8):
            _seq.append(min(_v3.MAX_TOKENS_CEILING, _v3.MAX_TOKENS * (2 ** (_t - 1))))
        check("T29b 放大序列单调且封顶于 65536",
              _seq == sorted(_seq) and max(_seq) == 65536, str(_seq))
    except Exception as _e:  # noqa: BLE001
        check("T29 预算策略", False, f"{type(_e).__name__}: {_e}")

    # ---------- T30 429 必须退避重试，不能整片判失败 ----------
    # 实测（2026-10-23，40 并发）：100 片里 18 次 429 直接成了「整片 FAIL」。
    # 429 是「这一秒太挤」，退避几秒重发通常就过；判整片失败等于白扔产能
    # （batch_vision 一轮要几分钟）。见 DL-11：429 的处置是「等」。
    # 同时必须保证：400 sensitive / 413 too large 这类**重发无意义**的错误不许重试。
    try:
        from digitallife import llm as _lm

        check("T30a 有退避次数常量", _lm.RATE_LIMIT_TRIES >= 1, str(_lm.RATE_LIMIT_TRIES))
        check("T30b _Retryable 是 LLMError 子类（上层 catch 兼容）",
              issubclass(_lm._Retryable, _lm.LLMError))

        class _FakeResp:
            def __init__(self, code, body='{"error":"inference exceeds tpm/rpm limit"}'):
                self.status_code = code
                self._b = body.encode()

            def read(self):
                return self._b

        def _drive(code, sends):
            cfg = _lm.ChatConfig(base_url="https://x/v1/chat/completions", api_key="k",
                                 model="m", timeout=1.0)
            cli = _lm.ChatClient(cfg)
            calls = []

            def _fake_post(self_, url, json=None, headers=None):
                calls.append(code)
                return _FakeResp(code)

            _lm.httpx.Client.post = _fake_post
            try:
                cli._post({"model": "m"})
            except _lm.LLMError:
                pass
            finally:
                sends.append(len(calls))

        _s = []
        # 测试里不退避，只数次数。**必须还原**（S16）：T31g 要读真实退避时长，
        # 被这里改成 0 会算出 total=0 而假红（本轮实测踩到）。
        _saved_max = _lm.RATE_LIMIT_MAX_S
        _lm.RATE_LIMIT_MAX_S = 0.0
        try:
            _drive(429, _s)
        finally:
            _lm.RATE_LIMIT_MAX_S = _saved_max
        check("T30c 429 会被重试（次数 = 1 + RATE_LIMIT_TRIES）",
              _s and _s[0] == _lm.RATE_LIMIT_TRIES + 1, str(_s))

        # 413 反直觉地属于可重试（2026-10-23 实测）：同一片 8 图、0.2MB、长边 1280，
        # 连续 5 次全 413；换 1 张、2 张成功；再换回 4 张也成功（328s）。
        # 请求体远低于任何合理上限 → 不是体积问题，是上游按并发/时刻判的容量错。
        _s2 = []
        _drive(413, _s2)
        check("T30d 413 也会退避重试（不是体积问题）",
              _s2 == [_lm.RATE_LIMIT_TRIES + 1], str(_s2))

        _s3 = []
        _drive(400, _s3)
        check("T30e 400（sensitive 一类）立即抛出、不重试", _s3 == [1], str(_s3))
    except Exception as _e:  # noqa: BLE001
        check("T30 限流退避", False, f"{type(_e).__name__}: {_e}")

    # ---------- T31 额度耗尽必须停下等，限流则不该停 ----------
    # 上游**不暴露额度**（2026-10-23 实测）：响应头无 x-ratelimit-*；
    # /v1/usage、/v1/quota、/v1/rate_limit、/v1/me 全 404；bridge 的 quota.remaining 全 null。
    # 所以只能探针间接判断，而探针失败的**两种语义必须分开**：
    #   rpm exhausted → 每分钟限流，等 90s 就恢复，停整批是浪费
    #   额度耗尽      → 5 小时 / 周额度，必须暂停等刷新，硬跑只会一直 429
    try:
        import sys as _sys
        _sys.path.insert(0, str(ROOT / "scripts"))
        import batch_vision as _bv

        check("T31a rpm 归为限流类",
              _bv._classify("上游返回 429：rpm exhausted") == "rpm")
        check("T31b 额度文案归为额度类",
              _bv._classify("429 exceeded your current quota") == "quota"
              and _bv._classify("insufficient balance") == "quota")
        check("T31c 网络类既不限流也不额度",
              _bv._classify("ReadTimeout") == "other")
        check("T31d 两种提示词集合不重叠（分类不会打架）",
              not (set(_bv._QUOTA_HINTS) & set(_bv._RPM_HINTS)))
        # 用**真实日志里出现过的报错串**回归，不是我自己造的样例
        check("T31e 真实 429 串分类正确（rpm exhausted 是限流，不是额度）",
              _bv._classify('上游返回 429（https://token.sensenova.cn/v1/chat/completions）：rpm exhausted') == "rpm")
        check("T31f 等待预算覆盖一个 5 小时窗（否则跑不完就得重来）",
              _bv.QUOTA_MAX_WAIT_H >= 5.0 and _bv.QUOTA_PAUSE_MIN > 0,
              "max_wait=%s pause=%s" % (_bv.QUOTA_MAX_WAIT_H, _bv.QUOTA_PAUSE_MIN))

        # 退避是阻塞式 sleep，占着线程槽位不产出 —— 总时长必须「够跨一个 60s 窗」且「别太长」。
        # 实测两端：95s（tries=5/max=65）→ 40 并发下一半槽位在睡觉，净产出掉到 3.96 片/分；
        # 6s（基准错写成 2.0）→ 根本跨不出窗口，重试必再撞。
        _seq = [min(_lm.RATE_LIMIT_MAX_S, _lm.RATE_LIMIT_BASE_S * (2 ** i))
                for i in range(_lm.RATE_LIMIT_TRIES)]
        _total = sum(_seq)
        check("T31g 退避总时长跨得出 60s 窗口且不过长（60–100s）",
              60.0 <= _total <= 100.0, "seq=%s total=%.0fs" % (_seq, _total))

        # --blind 的选片条件必须限定 reader='model'。
        # 手机侧 5,940 条是 reader='text'（用 ScreenMemo 自带画面描述，**设计上不送图**），
        # images_sent 天然为 0。不限定就会把它们全当"盲总结"重做，白烧 5,940 次调用。
        # 实测分母：sent=0 且 total>0 共 7,814 条，其中 reader=model 只有 1,874 条。
        import inspect as _insp
        _src = _insp.getsource(_bv.pick_pending)
        check("T31h --blind 选片限定 reader='model'（否则手机会被误重做）",
              "reader') = 'model'" in _src or "reader\') = \'model\'" in _src, _src[:80])

        # 退化图必须在前处理阶段滤掉（2026-10-23 实证）：
        # 一张 1280×20 的畸形 webp **单独发送就必触发 413**，
        # 而同片 6 张正常图一起发完全成功。这是上游对极端宽高比的解码拒绝，
        # 复用了 "payload too large" 文案 —— 不是体积问题，也不是并发问题。
        try:
            from PIL import Image as _I2
            checks_ = []
            for _wh, _want in (((1280, 20), True), ((1280, 704), False),
                               ((841, 508), False), ((1280, 100), True)):
                _b = _io.BytesIO()
                _I2.new("RGB", _wh).save(_b, "WEBP")
                checks_.append((_wh, _vi._is_degenerate(_b.getvalue()), _want))
            check("T31i 畸形图被识别、正常图不被误杀",
                  all(got == want for _wh, got, want in checks_), str(checks_))
            check("T31j 宽高比阈值足以放行 2:1 的真实截图",
                  _vi.MAX_ASPECT_RATIO >= 8.0, str(_vi.MAX_ASPECT_RATIO))
        except ImportError:
            pass

        # batch_vision 不得「写后即删」结构不合格的行。
        # 用户裁决（见 D.2）：有内容就入库，格式问题事后 prune，
        # 不要为了格式丢掉唯一的一次读图结果。
        # 实测事故：一次批量因此丢了 37 片已读到图的总结，只能重烧模型调用。
        # vision.enhance_slice 一直照裁决做（只记 schema_ok=False），batch_vision 却删行，
        # 两个实现互相矛盾 —— 这里钉住 batch_vision 不再出现 DELETE。
        import inspect as _insp2
        _bsrc = _insp2.getsource(_bv.run_one)
        check("T31k batch_vision 不删结构不合格的行（改由 prune_results 清理）",
              "DELETE FROM slice_result" not in _bsrc, _bsrc[:60])

        # 片层必须是日记体叙述，不能退回清单（2026-10-24 用户要求）。
        # 实测只靠提示词约束不住（3 片里 1 片仍输出「- 」清单），所以校验层必须拦住，
        # 否则 rework 循环没有机会纠正，坏格式直接入库。
        from digitallife.schema_check import validate_structured as _vs
        _prose = ("## 关键操作\n先打开页面逐个查看插件状态，接着回到对话区把会话重命名。"
                  "随后切到面板看配额，最后回来核对代码改动。\n"
                  "## 主要活动\n精力集中在对上下文系统的分析上，占了大半时间，"
                  "剩下零星花在查配额和读文章上。\n"
                  "## 重点内容\n围绕三个模块怎么合并成统一插件展开，"
                  "已经清理两个文件并把配置迁到新位置。")
        _bullets = ("## 关键操作\n- 打开页面查看插件\n- 回到对话区重命名会话\n- 切到面板看配额\n"
                    "## 主要活动\n- 上下文系统分析占大半时间\n- 查配额与读文章占少量\n"
                    "## 重点内容\n- 三模块合并成统一插件\n- 清理两个文件并迁配置\n")
        _vp = _vs({"overall_summary": _prose}, require_images=False)
        _vb = _vs({"overall_summary": _bullets}, require_images=False)
        check("T34a 叙述体通过 R5b（不被误判成清单）",
              not any(i.rule == "R5b.summary_prose" for i in _vp.errors),
              str([i.rule for i in _vp.errors][:3]))
        check("T34b 清单体被 R5b 拦下（提示词约束不住时靠校验兜底）",
              any(i.rule == "R5b.summary_prose" for i in _vb.errors),
              str([i.rule for i in _vb.errors][:3]))

        # 日报不得回退成「逐片粘贴」（R12）。2026-10-24 之前 751/753 篇都带
        # 「## 视觉总结」+ 每片原文，2026-08-13.md 达 1,477 行 / 138 KB。
        import inspect as _insp3
        from digitallife import daily as _daily
        _rsrc = _insp3.getsource(_daily.render_narrative)
        check("T34c render_narrative 不再逐片粘贴视觉总结（R12）",
              "## 视觉总结" not in _rsrc, _rsrc[:60])
        check("T34d 日报仍给出全量入口（展开看全量）",
              "展开看全量" in _rsrc, _rsrc[:60])

        # --force 的幂等水位线：必须只选「本次启动之前」产出的行。
        # 若直接全选，多轮循环里每轮都会把上一轮刚做完的片重新选中，永远跑不完
        # （改了提示词后要全量重做时正是这个场景）。
        import scripts.batch_vision as _bvm  # noqa: E402
        check("T34e --force 用启动水位线做幂等（不是全选）",
              "_FORCE_BEFORE" in _insp2.getsource(_bvm.pick_pending),
              _insp2.getsource(_bvm.pick_pending)[:60])
        # --force 必须真的传到 enhance_slice。曾经 submit 里传的是 args.blind，
        # 于是 --force 形同虚设：全量重做 3735 片 0.1 秒就"跑完"，其实命中已有结果
        # 直接返回（sum=0），模型一次都没调 —— 静默零产出，最难发现的一类 bug。
        _msrc = _insp2.getsource(_bvm.main)
        check("T34f --force 真的透传到 enhance_slice（不是只选片不重做）",
              "args.blind or args.force" in _msrc, _msrc[-80:])

        # 日报不入库（2026-10-24 用户裁决）：
        #   「日报不需要存储到数据库，包括时段也是，本身就是基于数据库内容进行的导出」
        # 于是 publish 必须**现算**，不能读库里的历史快照——否则改了渲染代码
        # 忘了重跑，就会把旧正文搬进 vault（这正是 AGENTS.md §G.10 记的老坑）。
        from digitallife import publish as _pub
        _psrc = _insp2.getsource(_pub)
        # 断言必须覆盖**调用点**而不只是 _daily_text 的函数体：
        # 第一版只查函数体，注入「调用点改回读库」时仍然全绿（S20 白做）。
        _body = _psrc.split("def _daily_text")[1].split("def ")[0]
        _call = _psrc.split("row = _daily_row")[0]
        check("T35a publish 日报走现算（调 build_daily），不再读库里的 output_text",
              "persist=False" in _body
              and "FROM daily_summary WHERE date_key" not in _body
              and "text = _daily_text(con, day)" in _psrc,
              "函数体或调用点不符")
        check("T35b build_daily 支持 persist=False（发布路径不写库）",
              "persist: bool = True" in _insp2.getsource(_daily.build_daily), "")
        # 缓存层：数据层指纹必须用全限定列名（join 后 created_at 会重名，
        # 实测直接抛 ambiguous column name: created_at，建页 rc=1 空转 0.4s）。
        import scripts.build_slice_html as _sd  # noqa: E402
        _bsrc2 = _insp2.getsource(_sd)
        check("T35c 数据层缓存指纹用 r.created_at（避免 ambiguous column）",
              "MAX(r.created_at)" in _bsrc2, "")
    except Exception as _e:  # noqa: BLE001
        check("T31 额度看门狗", False, f"{type(_e).__name__}: {_e}")

    con.close()
    print(f"\n通过 {len(passed)} / 失败 {len(failed)}")
    if failed:
        print("失败项：" + ", ".join(failed))
        return 1
    return 0


def _raises(fn) -> bool:
    try:
        fn()
    except Exception:  # noqa: BLE001
        return True
    return False


if __name__ == "__main__":
    sys.exit(main())
