#!/usr/bin/env python
"""链路健康体检 —— 可随时人工跑的独立命令（2026-11-02）。

用户要求：「让程序有个检测机制，这个检测函数保持通用性」。

## 三种用法

    python scripts/check_linkhealth.py              # 快检（秒级，日常迭代）
    python scripts/check_linkhealth.py --full       # 全检（含遍历类，跑批前后）
    python scripts/check_linkhealth.py --diff A B   # 对拍两份报告
    python scripts/check_linkhealth.py --json       # 只出 JSON（给脚本消费）

## 与既有体系的关系（不造第三套判据）

    scripts/smoke.py              验**代码行为**（临时库，不碰真实产物）
    scripts/verify_vault.py       验**生成产物**（产物 vs 代码现算）
    scripts/check_linkhealth.py   验**真实链路的数据状态**（本文件）
    scripts/verify_vault.py 第 6 段 也调用同一内核 —— 验收仍看那一个出口

## 判据单一出处

所有口径定义在 digitallife/render/linkprobes.py，本文件**不重算任何数**，
只负责调用、展示、落盘、对拍。这是这轮验收反复扯皮的根因修复：
之前 12,377 / 39,283 / 7,452 / 897 / 32,024 各说一套，就是因为口径散落各处。

## 退出码

    0 = 全部 fail 级通过（warn/info 不影响）
    1 = 有 fail 级未通过
    2 = 检测本身出错
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

from digitallife import paths
from digitallife.render import linkhealth, linkprobes


def open_ro():
    con = sqlite3.connect(
        "file:" + str(paths.db_path()).replace(chr(92), "/") + "?mode=ro",
        uri=True)
    con.row_factory = sqlite3.Row
    return con


def show(rep) -> None:
    """人读输出：按分类分组，坏掉的直接给出「该修哪一环」。"""
    print()
    print("=" * 74)
    print("链路健康 | %s | 用时 %.2fs | %s"
          % ("全检" if not rep.fast else "快检",
             rep.finished - rep.started,
             "通过" if rep.passed else "未通过"))
    print("=" * 74)

    for cat in sorted({r.category for r in rep.results}):
        rows = [r for r in rep.results if r.category == cat]
        n_ok = sum(1 for r in rows if r.passed)
        print()
        print("[%s] %d/%d" % (cat, n_ok, len(rows)))
        for r in rows:
            if r.passed:
                mark = "PASS"
            elif r.severity == "fail":
                mark = "FAIL"
            else:
                mark = "WARN"
            print("  %-4s %-32s %s" % (mark, r.probe_id, _fmt(r.value)))
            if not r.passed and r.hint:
                print("       期望 %s | %s" % (r.expect, r.hint))
                if r.detail:
                    print("       %s" % r.detail[:100])

    print()
    print("-" * 74)
    print("关键计数（可用于前后对拍）：")
    for k, v in sorted(rep.counts().items()):
        print("  %-34s %s" % (k, _fmt(v)))

    # 真缺口指向
    if any(r.probe_id == "coverage.true_gap" for r in rep.results):
        try:
            con = open_ro()
            try:
                hosts = linkprobes.gap_by_host(con, 8)
            finally:
                con.close()
            if hosts:
                print()
                print("真缺口按域名（该等谁解封）：")
                for h, n in hosts.items():
                    print("  %-36s %d" % (h, n))
        except Exception as exc:  # noqa: BLE001
            print("  [warn] 域名分布查询失败: %s" % exc)


def _fmt(v) -> str:
    if isinstance(v, float):
        return "%.4f" % v
    if isinstance(v, dict):
        return json.dumps(v, ensure_ascii=False)[:60]
    s = str(v)
    return s[:60]


def diff(path_a: Path, path_b: Path) -> int:
    """对拍两份报告：结构相同的 counts 逐项给差异。"""
    a = json.loads(path_a.read_text(encoding="utf-8"))
    b = json.loads(path_b.read_text(encoding="utf-8"))
    ca, cb = a.get("counts", {}), b.get("counts", {})
    keys = sorted(set(ca) | set(cb))
    print()
    print("=" * 74)
    print("对拍：%s" % path_a.name)
    print("     vs %s" % path_b.name)
    print("=" * 74)
    print("%-34s %14s %14s %12s" % ("probe", "A", "B", "diff"))
    changed = 0
    for k in keys:
        va, vb = ca.get(k), cb.get(k)
        try:
            d = (vb - va) if (isinstance(va, (int, float))
                              and isinstance(vb, (int, float))) else "n/a"
        except Exception:  # noqa: BLE001
            d = "n/a"
        if va != vb:
            changed += 1
        print("%-34s %14s %14s %12s" % (k, _fmt(va), _fmt(vb), d))
    print()
    print("变化项: %d / %d" % (changed, len(keys)))
    print("A 通过=%s  B 通过=%s" % (a.get("passed"), b.get("passed")))
    if a.get("tag") or b.get("tag"):
        print("A tag=%r  B tag=%r" % (a.get("tag"), b.get("tag")))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--full", action="store_true", help="全检（含遍历类，慢）")
    ap.add_argument("--json", action="store_true", help="只输出 JSON")
    ap.add_argument("--tag", default="", help="跑批标识，写进日志名")
    ap.add_argument("--diff", nargs=2, metavar=("A", "B"),
                    help="对拍两份已落盘的报告")
    ap.add_argument("--no-log", action="store_true", help="不落盘")
    args = ap.parse_args()

    if args.diff:
        return diff(Path(args.diff[0]), Path(args.diff[1]))

    try:
        con = open_ro()
    except Exception as exc:  # noqa: BLE001
        print("打不开库: %s" % exc, file=sys.stderr)
        return 2
    try:
        rep = linkhealth.run(
            linkprobes.all_probes(), ctx=con, fast=not args.full,
            tag=args.tag, params={"full": args.full})
    except Exception as exc:  # noqa: BLE001
        print("检测出错: %s" % exc, file=sys.stderr)
        return 2
    finally:
        con.close()

    if args.json:
        print(rep.to_json())
    else:
        show(rep)

    if not args.no_log:
        try:
            path = linkhealth.log_report(rep, name="check")
            if not args.json:
                print()
                print("日志: %s" % path)
        except Exception as exc:  # noqa: BLE001
            print("[warn] 日志落盘失败: %s" % exc, file=sys.stderr)

    return 0 if rep.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
