"""按 screenmemo-v1 约定清理 slice_result：不合规的删除，合规的保留。

约定真源：digitallife/prompts/segment.py 的 PROMPT_VERSION + OUTPUT_FIELDS，
校验实现：digitallife/schema_check.py（同一套 R1-R8 规则）。

用法：
  python scripts/prune_results.py           # 只报告（dry-run）
  python scripts/prune_results.py --apply   # 真删除
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from digitallife import paths  # noqa: E402
from digitallife.schema_check import validate_structured  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="真删除不合规行（默认只报告）")
    ap.add_argument("--db", default=None)
    args = ap.parse_args()

    db = args.db or str(paths.db_path())
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    rows = list(con.execute(
        "SELECT slice_id, structured_json, overall_summary FROM slice_result"))

    keep: list[str] = []
    drop: list[tuple[str, str]] = []
    for r in rows:
        try:
            st = json.loads(r["structured_json"]) if r["structured_json"] else None
        except (TypeError, ValueError):
            st = None
        v = validate_structured(st, require_images=False)
        if v.ok:
            keep.append(r["slice_id"])
        else:
            reason = "; ".join(e.message for e in v.errors[:2]) or "未知"
            drop.append((r["slice_id"], reason))

    print(f"库: {db}")
    print(f"总计 {len(rows)} 条 | 符合 screenmemo-v1 约定 {len(keep)} 条 | 不符合 {len(drop)} 条")
    for sid, reason in drop:
        print(f"  [DROP] {sid}  {reason[:110]}")

    if not args.apply:
        if drop:
            print("\n（dry-run）确认后用 --apply 真删除")
        return 0

    if not drop:
        print("\n无需删除")
        return 0

    for sid, _ in drop:
        con.execute("DELETE FROM slice_result WHERE slice_id = ?", (sid,))
    con.commit()
    after = con.execute("SELECT COUNT(*) FROM slice_result").fetchone()[0]
    con.close()
    print(f"\n已删除 {len(drop)} 条，剩余 {after} 条")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())