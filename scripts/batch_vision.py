"""批量视觉识别：并发 + 自动重试，只写合格的 slice_result。

设计要点
1. **并发**：模型侧可并发，默认 8 路；每线程独立 sqlite 连接（check_same_thread=False）。
2. **重试**：单片最多 5 轮；每轮内部已含「空内容重试 5 次 + 结构打回重做 5 次」。
   实测该模型推理量随机暴涨，重试是唯一有效手段（同一片连败 3 次后第 4 次 64s 成功）。
3. **幂等**：已有 slice_result 的片直接跳过，可随时中断/续跑。
4. **只留合格**：写完即用 schema_check 复核，不合格立刻删掉该行（不污染库）。

用法：
  python scripts/batch_vision.py --days 2026-08-13 --workers 8 --rounds 5
  python scripts/batch_vision.py --all --workers 8            # 全部 PC 侧含图片的片
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from digitallife import paths  # noqa: E402
from digitallife.schema_check import validate_structured  # noqa: E402
from digitallife.vision import enhance_slice  # noqa: E402

_DEVICE = "pc"
_READER = "model"


def pick_pending(day: str | None, device: str = "pc") -> list[tuple[str, int]]:
    con = sqlite3.connect(paths.db_path())
    con.row_factory = sqlite3.Row
    sql = """
      SELECT s.slice_id, s.image_count FROM slice s
      WHERE s.device IN ({ph}) AND s.status != 'split' AND s.image_count > 0
        AND NOT EXISTS (SELECT 1 FROM slice_result r WHERE r.slice_id = s.slice_id)
    """
    from digitallife import devices as _dv
    _aliases = _dv.query_aliases(device)
    sql = sql.replace('{ph}', ','.join('?' * len(_aliases)))
    args: list = list(_aliases)
    if day:
        sql += " AND date(s.start_utc_ms/1000,'unixepoch','+8 hours') = ?"
        args.append(day)
    sql += " ORDER BY s.start_utc_ms"
    out = [(r["slice_id"], r["image_count"]) for r in con.execute(sql, args)]
    con.close()
    return out


def run_one(item: tuple[str, int]) -> tuple[str, int, bool, float, str]:
    sid, imgs = item
    con = sqlite3.connect(paths.db_path(), check_same_thread=False)
    con.row_factory = sqlite3.Row
    t0 = time.time()
    try:
        res = enhance_slice(con, sid, reader=_READER)
        con.commit()
        # 复核：写进去的必须合格，不合格立刻删
        row = con.execute(
            "SELECT structured_json FROM slice_result WHERE slice_id = ?", (sid,)
        ).fetchone()
        import json as _json
        st = _json.loads(row["structured_json"]) if row and row["structured_json"] else None
        v = validate_structured(st, require_images=False)
        if not v.ok:
            con.execute("DELETE FROM slice_result WHERE slice_id = ?", (sid,))
            con.commit()
            return (sid, imgs, False, time.time() - t0,
                    "written-then-rejected: " + "; ".join(e.message[:60] for e in v.errors[:2]))
        return (sid, imgs, True, time.time() - t0,
                "sum=%d rework=%s" % (len(res.get("overall_summary") or ""),
                                      res.get("rework_rounds")))
    except Exception as exc:  # noqa: BLE001 —— 单片失败不拖累其他片
        con.rollback()
        return (sid, imgs, False, time.time() - t0, "%s: %s" % (type(exc).__name__, str(exc)[:110]))
    finally:
        con.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", nargs="*", default=None)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--rounds", type=int, default=5)
    ap.add_argument("--device", default="pc")
    ap.add_argument("--reader", default="model",
                    choices=["model", "local", "both", "text"])
    args = ap.parse_args()
    global _DEVICE, _READER
    _DEVICE = args.device
    _READER = args.reader

    days = args.days if args.days else ([None] if args.all else ["2026-08-13"])
    grand_ok = grand_fail = 0
    t_all = time.time()

    for day in days:
        for rnd in range(1, args.rounds + 1):
            ids = pick_pending(day, _DEVICE)
            if not ids:
                break
            label = day or "ALL"
            print("\n=== %s 第 %d 轮：待处理 %d 片，并发 %d ===" % (label, rnd, len(ids), args.workers),
                  flush=True)
            ok = fail = 0
            with ThreadPoolExecutor(max_workers=args.workers) as ex:
                futs = {ex.submit(run_one, it): it for it in ids}
                for i, f in enumerate(as_completed(futs), 1):
                    sid, imgs, good, dt, info = f.result()
                    if good:
                        ok += 1
                        grand_ok += 1
                        print("  [%d/%d] %s %2d图 %6.1fs %s" % (i, len(ids), sid[:12], imgs, dt, info),
                              flush=True)
                    else:
                        fail += 1
                        grand_fail += 1
                        print("  [%d/%d] %s %2d图 %6.1fs FAIL %s" % (i, len(ids), sid[:12], imgs, dt, info),
                              flush=True)
            print("%s 第 %d 轮：成功 %d / 失败 %d" % (label, rnd, ok, fail), flush=True)
            if fail == 0:
                break

    el = time.time() - t_all
    con = sqlite3.connect(paths.db_path())
    total = con.execute("SELECT COUNT(*) FROM slice_result").fetchone()[0]
    remain = con.execute("""
        SELECT COUNT(*) FROM slice s
        WHERE s.device='pc' AND s.status!='split' AND s.image_count>0
          AND NOT EXISTS (SELECT 1 FROM slice_result r WHERE r.slice_id=s.slice_id)
    """).fetchone()[0]
    con.close()
    print("\n完成：成功 %d / 失败 %d，耗时 %.1f 分钟" % (grand_ok, grand_fail, el / 60))
    print("slice_result 总数：%d，PC 剩余：%d" % (total, remain))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())