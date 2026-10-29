"""命令行入口 —— 每一阶段一个子命令，可独立跑、可断点续跑。

用法（cwd 任意，路径由 paths.py 锚定）：
    python -m digitallife.cli ingest --source all
    python -m digitallife.cli dbview --date 2026-10-20
    python -m digitallife.cli slice --device pc
    python -m digitallife.cli vision --device pc --limit 10 --reader model
    python -m digitallife.cli daily --date 2026-10-20
    python -m digitallife.cli publish
    python -m digitallife.cli verify
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime

from . import paths
from .ingest.clean import build_report
from .ingest.store import (
    count_events, count_kind, open_db, record_source_file,
    rebuild_fts, time_span, upsert_events,
)
from .ingest.tz import local_day, utc_ms_to_local


def _run_id() -> str:
    return datetime.now().strftime("%Y%m%dT%H%M%S")


def _load_adapters(names: list[str]):
    from .ingest.sources import ADAPTERS
    if not names or names == ["all"]:
        names = list(ADAPTERS)
    missing = [n for n in names if n not in ADAPTERS]
    if missing:
        raise SystemExit(f"未知源：{missing}；可选 {sorted(ADAPTERS)}")
    return [(n, ADAPTERS[n]()) for n in names]


def cmd_ingest(args: argparse.Namespace) -> int:
    con = open_db()
    run = _run_id()
    reports = []
    for name, adapter in _load_adapters(args.source):
        t0 = time.time()
        print(f"[ingest] {name} …", flush=True)
        fps, events, report = adapter.run()
        report.rows_read = max(report.rows_read, len(events))
        n = upsert_events(con, events, run)
        report.rows_kept = n
        for fp in fps:
            record_source_file(
                con, source_id=fp.source_id, source=name, device=adapter.device,
                path=fp.path, sha256=fp.sha256, bytes=fp.bytes, mtime=fp.mtime,
                window_start_utc_ms=report.window_start_utc_ms,
                window_end_utc_ms=report.window_end_utc_ms,
                row_count=n, note=fp.note,
            )
        con.commit()
        reports.append(report)
        lo, hi = time_span(con, name)
        print(f"[ingest] {name} 写入 {n} 条，用时 {time.time() - t0:.1f}s，"
              f"跨度 {utc_ms_to_local(lo):%Y-%m-%d} … {utc_ms_to_local(hi):%Y-%m-%d}"
              if lo and hi else f"[ingest] {name} 写入 {n} 条（无时间跨度）")
    payload = build_report(reports)
    payload["ingest_run"] = run
    payload["counts"] = {r.source: count_kind(con, r.source) for r in reports}
    paths.ensure_dirs()
    paths.clean_report_path().write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    rebuild_fts(con)
    con.commit()
    print(f"[ingest] 清洗报告 → {paths.clean_report_path()}")
    return 0


def cmd_dbview(args: argparse.Namespace) -> int:
    from .domain.dbview import build_day_structure
    con = open_db()
    data = build_day_structure(con, args.date, args.device)
    print(json.dumps(data, ensure_ascii=False, indent=2)[:4000])
    return 0


def cmd_slice(args: argparse.Namespace) -> int:
    from .domain.slicer import build_slices
    con = open_db()
    out = build_slices(con, args.device, args.date)
    con.commit()
    splits = sum(1 for s in out if s.get("split_of"))
    print(f"[slice] {args.device} 生成 {len(out)} 片（含二分子片 {splits}）")
    return 0


def cmd_vision(args: argparse.Namespace) -> int:
    from .produce.vision import enhance_slice
    con = open_db()
    sql = "SELECT slice_id FROM slice WHERE status='pending'"
    argsq: list = []
    if args.device:
        sql += " AND device=?"
        argsq.append(args.device)
    sql += " ORDER BY start_utc_ms LIMIT ?"
    argsq.append(args.limit)
    ids = [r[0] for r in con.execute(sql, argsq)]
    print(f"[vision] 待处理 {len(ids)} 片，reader={args.reader}")
    ok = fail = 0
    for sid in ids:
        try:
            enhance_slice(con, sid, reader=args.reader)
            con.commit()
            ok += 1
        except Exception as e:  # noqa: BLE001 —— 单片失败不中断整批
            con.rollback()
            fail += 1
            print(f"[vision] {sid} 失败：{e}")
    print(f"[vision] 成功 {ok} / 失败 {fail}")
    return 0 if fail == 0 else 1


def cmd_ocr(args: argparse.Namespace) -> int:
    from .produce.ocr import available, ocr_image
    if not available():
        raise SystemExit("本地 OCR 不可用：pip install -e .[ocr]")
    con = open_db()
    rows = con.execute(
        "SELECT e.event_id, e.path FROM event e LEFT JOIN capture_text c ON c.event_id=e.event_id "
        "WHERE e.kind='screen' AND c.event_id IS NULL AND e.path IS NOT NULL LIMIT ?",
        (args.limit,),
    ).fetchall()
    print(f"[ocr] 待识别 {len(rows)} 张")
    for eid, p in rows:
        try:
            txt = ocr_image(p)
        except Exception as e:  # noqa: BLE001
            print(f"[ocr] {p} 失败：{e}")
            continue
        con.execute(
            "INSERT INTO capture_text(event_id, text, engine, model, created_at) VALUES(?,?,?,?,?) "
            "ON CONFLICT(event_id) DO UPDATE SET text=excluded.text, engine=excluded.engine",
            (eid, txt, "rapidocr", None, int(time.time() * 1000)),
        )
        con.commit()
    rebuild_fts(con)
    con.commit()
    return 0


def cmd_daily(args: argparse.Namespace) -> int:
    from .daily import build_daily
    con = open_db()
    data = build_daily(con, args.date)
    con.commit()
    print(f"[daily] {args.date} 已生成")
    print(data.get("output_text", "")[:1200])
    return 0


def cmd_publish(args: argparse.Namespace) -> int:
    from .publish import publish_all
    con = open_db()
    days = [args.date] if args.date else None
    info = publish_all(con, day_keys=days, make_zip=not args.no_zip)
    print(json.dumps(info, ensure_ascii=False, indent=2)[:3000])
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    con = open_db()
    rows = con.execute("SELECT source, device, COUNT(*) FROM event GROUP BY source, device").fetchall()
    for src, dev, n in rows:
        lo, hi = time_span(con, src)
        span = ""
        if lo and hi:
            span = f"{utc_ms_to_local(lo):%Y-%m-%d} … {utc_ms_to_local(hi):%Y-%m-%d}"
        print(f"{src:12s} {dev:6s} {n:7d} 条  {span}")
    print(f"总计 event {count_events(con)} 条")
    return 0


def _device_choices() -> list[str]:
    """可选的 device 值：注册 id + 旧别名。

    不能写死成 ["pc", "phone"] —— 加第二台手机（phone-2）时那个 choices 会把
    合法值挡在 CLI 外面（DL-7 同类：设备扩容后硬编码清单静默失效）。
    """
    from .domain import devices as _dv
    out: list[str] = []
    for d in _dv.all_devices():
        out.append(d.device_id)
    out.extend(_dv.LEGACY_ALIASES.keys())
    return out

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="digitallife", description="个人数字痕迹统一数据集")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("ingest", help="五源清洗入库")
    s.add_argument("--source", nargs="*", default=["all"])
    s.set_defaults(func=cmd_ingest)

    s = sub.add_parser("dbview", help="数据库结构视图")
    s.add_argument("--date", required=True)
    s.add_argument("--device", default=None)
    s.set_defaults(func=cmd_dbview)

    s = sub.add_parser("slice", help="全局时间轴切片")
    s.add_argument("--device", required=True, choices=_device_choices())
    s.add_argument("--date", default=None)
    s.set_defaults(func=cmd_slice)

    s = sub.add_parser("vision", help="视觉增强")
    s.add_argument("--device", default=None, choices=_device_choices())
    s.add_argument("--limit", type=int, default=10)
    s.add_argument("--reader", default="model", choices=["model", "local", "both", "text"])
    s.set_defaults(func=cmd_vision)

    s = sub.add_parser("ocr", help="本地 OCR 回填")
    s.add_argument("--limit", type=int, default=100)
    s.set_defaults(func=cmd_ocr)

    s = sub.add_parser("daily", help="生成每日总结（只产日层）")
    s.add_argument("--date", required=True)
    s.set_defaults(func=cmd_daily)

    s = sub.add_parser("publish", help="落盘 + 留存包")
    s.add_argument("--date", default=None)
    s.add_argument("--no-zip", action="store_true")
    s.set_defaults(func=cmd_publish)

    s = sub.add_parser("verify", help="各源条数与跨度自证")
    s.set_defaults(func=cmd_verify)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args) or 0)


if __name__ == "__main__":
    sys.exit(main())