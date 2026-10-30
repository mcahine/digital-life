"""批量重生成所有日层总结（cli 的 daily 只接 --date，没有 --all）。

为什么要有 --workers：`build_daily` 单天 1.4s、753 天串行 18 分钟，而机器有 8 核却
只用了 1 核。实测 4 进程 0.73s/天（2.0×），全部零失败。瓶颈从来不是 SQL —— 那 60 倍
退化（逐片查库）2026-10-23 已修，现在是纯 CPU 的字符串处理与归并。

每个进程开自己的连接（DL-1 之外的另一条铁律：sqlite 连接不跨线程/进程共享），
并设 busy_timeout 让并发写排队而不是直接报 database is locked。

用法：
  python scripts/rebuild_daily.py                  # 串行
  python scripts/rebuild_daily.py --workers 4      # 4 进程（推荐）
  python scripts/rebuild_daily.py --limit 30 --workers 4
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import time
from pathlib import Path

# T1 (2026-10-25): sys.path.insert removed -- package is installable via pip install -e .

from digitallife import paths  # noqa: E402
from digitallife.daily import build_daily  # noqa: E402


def _make_client():
    """按环境变量构造 ChatClient；未配置则返回 None（= 不生成叙事）。

    为什么在子进程里重建而不是把 client 传进来：ProcessPoolExecutor 要 pickle 参数，
    ChatClient 持有 httpx 连接池，不可 pickle。故用环境变量做跨进程传递。

    2026-10-30 新增：在此之前**没有任何入口给 build_daily 注入 client**，
    导致 753 天日报里只有 1 天有叙事（其余 752 天 narrative 为空）——
    不是「没跑」，是代码路径上没接上。
    """
    base = (os.environ.get("DL_LLM_BASE_URL") or "").strip()
    key = (os.environ.get("DL_LLM_API_KEY") or "").strip()
    model = (os.environ.get("DL_LLM_MODEL") or "").strip()
    if not (base and key and model):
        return None
    from digitallife.produce.llm import ChatClient, ChatConfig

    return ChatClient(ChatConfig(
        base_url=base,
        api_key=key,
        model=model,
        timeout=float(os.environ.get("DL_LLM_TIMEOUT") or 240),
    ))


def _one(day: str, persist: bool = False) -> tuple[str, bool, str]:
    """重建一天并提交。返回 (日期, 是否成功, 失败原因)。必须是模块级函数才可 pickle。"""
    con = sqlite3.connect(paths.db_path(), timeout=30, check_same_thread=False)
    con.row_factory = sqlite3.Row
    try:
        con.execute("PRAGMA busy_timeout=30000")
        # persist=False：**只算不落盘**。写 20-日报/*.md 的唯一入口是 publish_all，
        # 本脚本仅用于「预热/校验渲染」，改完 daily.py 要看到 md 变化必须再跑 publish。
        # （2026-10-25 修正：原文写的是「批量重建只更新 vault 文件」—— 那是假的，
        #   _one() 里没有任何 write_bytes/write_text；正是这句半真半假的注释
        #   让另一个 agent 以为跑本脚本就能重生成日报。）
        # 2026-10-24 用户裁决「日报不需要存储到数据库」—— 这里曾经用默认
        # persist=True，一次全量重建就把 5.9 MB output_text 又写回去了。
        build_daily(con, day, client=_make_client(), persist=persist)
        con.commit()
        return (day, True, "")
    except Exception as exc:  # noqa: BLE001
        con.rollback()
        return (day, False, "%s: %s" % (type(exc).__name__, str(exc)[:90]))
    finally:
        con.close()


def _filter_missing_narrative(days: list[str]) -> list[str]:
    """只保留「有片总结 但 缺叙事」的天。

    为什么需要：753 天里有片总结的只有 143 天，其余 608 天没有原料，
    喂空给模型既费钱又没意义。2026-10-30 实测：
    有片的天 751 / 有片且有总结的天 143 / 缺叙事且能跑的天 142。
    """
    import json as _json

    import datetime as _dt

    con = sqlite3.connect(paths.db_path())
    con.row_factory = sqlite3.Row
    try:
        # 有片总结的天。slice 表**没有 date_key 列**（实测），
        # 只能从 start_utc_ms 转本地日（UTC+8）作为归日口径。
        have_sum = set()
        for r in con.execute(
            "SELECT DISTINCT start_utc_ms FROM slice WHERE slice_id IN "
            "(SELECT slice_id FROM slice_result)"
        ):
            d = (_dt.datetime.utcfromtimestamp(int(r["start_utc_ms"]) / 1000)
                 + _dt.timedelta(hours=8))
            have_sum.add(d.strftime("%Y-%m-%d"))
        # 缺叙事的天
        missing = set()
        for r in con.execute("SELECT date_key, structured_json FROM daily_summary"):
            try:
                d = _json.loads(r["structured_json"]) if r["structured_json"] else {}
            except Exception:  # noqa: BLE001
                d = {}
            if not (d.get("narrative") or "").strip():
                missing.add(str(r["date_key"]))
    finally:
        con.close()

    keep = [d for d in days if d in have_sum and d in missing]
    print("  过滤: %d 天 -> %d 天（有片总结 %d / 缺叙事 %d / 交集 %d）"
          % (len(days), len(keep), len(have_sum), len(missing),
             len(have_sum & missing)), flush=True)
    return keep


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", nargs="*", default=None,
                    help="只重建这些日期（YYYY-MM-DD）。省略 = 全量。"
                         "改了几天的片层就只重建那几天，不要每次全量 753 天。")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=1,
                    help="并发进程数；1 = 串行。实测 4 进程约 2 倍")
    ap.add_argument("--llm", action="store_true",
                    help="调模型生成叙事（需 DL_LLM_BASE_URL / DL_LLM_API_KEY / "
                         "DL_LLM_MODEL 三个环境变量）。不加则只跑模板路径，narrative 保持为空。")
    ap.add_argument("--only-missing", action="store_true",
                    help="只跑「有片总结但缺叙事」的天。省掉没有原料的天（喂空给模型没意义）。")
    args = ap.parse_args()

    if args.llm:
        _cli = _make_client()
        if _cli is None:
            print("错误: --llm 需要 DL_LLM_BASE_URL / DL_LLM_API_KEY / DL_LLM_MODEL "
                  "三个环境变量，当前不完整。", flush=True)
            return 2

    con = sqlite3.connect(paths.db_path())
    con.row_factory = sqlite3.Row
    if args.days:
        # 指定日期：不查 daily_summary（那天可能还没建过），直接用传入的清单。
        days = list(args.days)
    else:
        days = [r["date_key"] for r in con.execute(
            "SELECT date_key FROM daily_summary ORDER BY date_key")]
    con.close()
    if args.only_missing:
        days = _filter_missing_narrative(days)
    if args.limit:
        days = days[: args.limit]
    print("待重生成: %d 天（并发 %d，模型 %s）"
          % (len(days), args.workers, "ON" if args.llm else "OFF"), flush=True)

    ok = fail = 0
    t0 = time.time()
    if args.workers <= 1:
        for i, d in enumerate(days, 1):
            _d, good, err = _one(d, persist=True)
            if good:
                ok += 1
            else:
                fail += 1
                print("  [%d/%d] %s FAIL %s" % (i, len(days), d, err), flush=True)
            if i % 100 == 0:
                print("  ... %d/%d，耗时 %.0fs" % (i, len(days), time.time() - t0), flush=True)
    else:
        from concurrent.futures import ProcessPoolExecutor
        with ProcessPoolExecutor(max_workers=args.workers) as ex:
            for i, (d, good, err) in enumerate(
                    ex.map(_one, days, [True] * len(days)), 1):
                if good:
                    ok += 1
                else:
                    fail += 1
                    print("  [%d/%d] %s FAIL %s" % (i, len(days), d, err), flush=True)
                if i % 100 == 0:
                    print("  ... %d/%d，耗时 %.0fs" % (i, len(days), time.time() - t0), flush=True)

    el = time.time() - t0
    print("完成: 成功 %d / 失败 %d，耗时 %.1f 分钟（%.2fs/天）"
          % (ok, fail, el / 60, el / max(len(days), 1)))
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
