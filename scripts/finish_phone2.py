"""phone-2 批量完成后的**一键收尾**（可重复执行，幂等、自包含）。

用法：
    cd 01-Projects/digital-life
    export PYTHONUTF8=1
    python scripts/finish_phone2.py

做的事：
  1. 质量核对（未跑完就提示并退出，不会误改数据）
  2. 重生成全部时段页（build_slice_html --all）
  3. 重生成全部日报（rebuild_daily）
  4. 发布到 vault（publish --no-zip）
  5. 质量核对 + 端到端验收（库片数 vs 日报声明）

设计要点：**不依赖 tmp/ 下的任何脚本**。tmp/ 被 .gitignore 排除、可能被清理，
收尾逻辑必须自包含，否则关键时刻会因缺文件而断掉。

为什么需要它：本轮实测踩过三次「改代码 ≠ 改数据」——改完 daily.py / publish.py 后，
库和 vault 里的仍是旧快照，必须重跑产出再发布。
"""
from __future__ import annotations

import re
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# T1 (2026-10-25): sys.path.insert removed -- package is installable via pip install -e .


def quality() -> tuple[int, int, int, int, int, int, int, int]:
    """返回 (done, total, bad, empty, three, with_desc, no_desc, days)。"""
    from digitallife import paths
    # T3（2026-10-25）：原先是 from digitallife.vision import _collect_descriptions,
    # _slice_events —— 运维脚本反向依赖加工层**私有符号**。私有符号改签名时无约束，
    # 而本脚本是「一键收尾」的闸门，它静默算错会让闸门失效。改用公开 API。
    from digitallife.produce.vision import slice_has_description

    con = sqlite3.connect(paths.db_path())
    con.row_factory = sqlite3.Row
    q = lambda sql: con.execute(sql).fetchone()[0]  # noqa: E731
    done = q("SELECT COUNT(*) FROM slice_result r JOIN slice s ON s.slice_id=r.slice_id "
             "WHERE s.device='phone-2'")
    total = q("SELECT COUNT(*) FROM slice WHERE device='phone-2' AND status!='split' "
              "AND image_count>0")
    bad = q("SELECT COUNT(*) FROM slice_result r JOIN slice s ON s.slice_id=r.slice_id "
            "WHERE s.device='phone-2' AND (r.structured_json IS NULL "
            "OR json_extract(r.structured_json,'$._meta.schema_ok') IS NOT 1)")
    empty = q("SELECT COUNT(*) FROM slice_result r JOIN slice s ON s.slice_id=r.slice_id "
              "WHERE s.device='phone-2' AND (r.overall_summary IS NULL "
              "OR TRIM(r.overall_summary)='')")
    three = q("SELECT COUNT(*) FROM slice_result r JOIN slice s ON s.slice_id=r.slice_id "
              "WHERE s.device='phone-2' AND r.overall_summary LIKE '%## 关键操作%' "
              "AND r.overall_summary LIKE '%## 主要活动%' "
              "AND r.overall_summary LIKE '%## 重点内容%'")
    days = q("SELECT COUNT(DISTINCT substr(datetime(start_utc_ms/1000,'unixepoch',"
            "'+8 hours'),1,10)) FROM slice WHERE device='phone-2' "
            "AND image_count>0 AND status!='split'")
    rows = con.execute("SELECT s.slice_id, s.image_count, s.start_utc_ms, s.end_utc_ms "
                       "FROM slice_result r JOIN slice s ON s.slice_id=r.slice_id "
                       "WHERE s.device='phone-2'").fetchall()
    nd = sum(1 for r in rows if slice_has_description(con, r["slice_id"], r))
    con.close()
    return done, total, bad, empty, three, nd, len(rows) - nd, days


def acceptance() -> tuple[int, list]:
    """库里有 N 片总结 vs 日报声明 N 片，必须一致。返回 (一致数, 不一致列表)。"""
    from digitallife import paths

    con = sqlite3.connect(paths.db_path())
    con.row_factory = sqlite3.Row
    rows = con.execute("""
        SELECT d, SUM(ok) ok, COUNT(*) tot FROM (
          SELECT substr(datetime(s.start_utc_ms/1000,'unixepoch','+8 hours'),1,10) d,
            EXISTS(SELECT 1 FROM slice_result x WHERE x.slice_id=s.slice_id) ok
          FROM slice s WHERE s.device='phone-2'
            AND s.image_count>0 AND s.status!='split')
        GROUP BY d""").fetchall()
    # 注意：**不能只查「已完成日」**。2026-10-23 实测：只查已完成日时，
    # 07-19 / 07-20 / 07-21 三天因为「当天还有片没跑完」被跳过，
    # 而它们的日报声明为 0 片 —— 漏检。这里对**全部有数据的日期**都查。
    bad = []
    full = rows
    for r0 in full:
        d = r0["d"]
        n = r0["ok"]
        t = con.execute("SELECT output_text FROM daily_summary WHERE date_key=?",
                        (d,)).fetchone()
        sm = (t["output_text"] if t else "") or ""
        k = sm.find("## iQQQ Neo11")
        m = re.search(r"视觉总结 (\d+) 片", sm[k:k + 500]) if k >= 0 else None
        dec = int(m.group(1)) if m else -1
        if dec != n:
            bad.append((d, n, dec))
    con.close()
    return len(full) - len(bad), bad


def check(title: str, ok: bool, detail: str = "") -> bool:
    print("  [%s] %s%s" % ("PASS" if ok else "FAIL", title,
                           "" if not detail else "  " + detail), flush=True)
    return ok


def gate() -> bool:
    done, total, bad, empty, three, nd, nz, days = quality()
    print("质量: %d/%d | 不合规 %d | 空总结 %d | 三段齐全 %d | 覆盖 %d 天 | 有描述 %d / 无描述 %d"
          % (done, total, bad, empty, three, days, nd, nz), flush=True)
    if done < total:
        print("未跑完，剩余 %d 片" % (total - done), flush=True)
        return False
    ok = check("全部片有总结", done == total)
    ok = check("无 schema 不合规", bad == 0) and ok
    ok = check("无空总结", empty == 0) and ok
    ok = check("三段齐全率 100%", three == done, "%d/%d" % (three, done)) and ok
    return ok


def run(desc: str, cmd: list[str]) -> int:
    print("\n=== %s ===" % desc, flush=True)
    print("$ " + " ".join(cmd), flush=True)
    r = subprocess.run(cmd, cwd=str(ROOT))
    if r.returncode != 0:
        print("!! 失败（退出码 %d），中止" % r.returncode, flush=True)
    return r.returncode


def main() -> int:
    print("步骤 1/5 质量闸门", flush=True)
    if not gate():
        print("\n质量未达标，不继续。若只是「未跑完」，先续跑：", flush=True)
        print("  python scripts/batch_vision.py --all --device phone-2 "
              "--reader text --workers 8 --rounds 4", flush=True)
        return 1

    for i, (desc, cmd) in enumerate([
        # 必须带 --workers：两者都是纯 CPU 的渲染/归并，串行只用 1 核。
        # 实测 753 页 15min → 6min、753 天日报 18min → 3.7min（见 AGENTS.md D.2）。
        ("重生成时段页", [sys.executable, "scripts/build_slice_html.py", "--all",
                     "--workers", "6"]),
        ("重生成日报", [sys.executable, "scripts/rebuild_daily.py",
                    "--workers", "4"]),
        ("发布到 vault", [sys.executable, "-m", "digitallife.cli", "publish", "--no-zip"]),
    ], start=2):
        print("\n步骤 %d/5 %s" % (i, desc), flush=True)
        rc = run(desc, cmd)
        if rc != 0:
            return rc

    print("\n步骤 5/5 复核", flush=True)
    if not gate():
        return 1
    n, bad = acceptance()
    check("库↔日报声明一致", not bad, "%d 天一致%s" % (n, "" if not bad else "，%d 天不符" % len(bad)))
    if bad:
        for x in bad[:10]:
            print("    %s 库=%d 日报=%d" % x, flush=True)
        return 1
    print("\n=== 全部完成 ===", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())