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


def pick_pending(day: str | None, device: str = "pc",
                 blind: bool = False) -> list[tuple[str, int]]:
    """选出要跑的片。blind=True 时改选"有图但一张没送到模型"的历史结果（重做它们）。

    blind 的判据是 slice_result._meta 里的 images_sent=0 且 images_total>0 ——
    这正是 2026-10-23 修掉的那个 bug 留下的痕迹（图在 zip 里，_chat 却传相对路径，
    chat_images 读不到就静默跳过）。这类结果看着像看图的总结，其实全凭 DB 时间线。
    """
    con = sqlite3.connect(paths.db_path())
    con.row_factory = sqlite3.Row
    if blind:
        sql = """
          SELECT s.slice_id, s.image_count FROM slice s
          JOIN slice_result r ON r.slice_id = s.slice_id
          WHERE s.device IN ({ph}) AND s.status != 'split' AND s.image_count > 0
            AND json_extract(r.structured_json, '$._meta.images_total') > 0
            AND COALESCE(json_extract(r.structured_json, '$._meta.images_sent'), 0) = 0
            -- 必须限定 reader='model'：手机侧 5,940 条是 reader='text'（用 ScreenMemo 自带的
            -- 画面描述，**设计上就不送图**），images_sent 天然为 0。不限定就会把它们
            -- 全部当成"盲总结"重做一遍 —— 白白烧掉 5,940 次模型调用（实测分母：
            -- sent=0 且 total>0 共 7,814 条，其中 reader=model 只有 1,874 条）。
            AND json_extract(r.structured_json, '$._meta.reader') = 'model'
        """
    else:
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


def run_one(item: tuple[str, int], force: bool = False) -> tuple[str, int, bool, float, str]:
    sid, imgs = item
    con = sqlite3.connect(paths.db_path(), check_same_thread=False)
    con.row_factory = sqlite3.Row
    t0 = time.time()
    try:
        res = enhance_slice(con, sid, reader=_READER, force=force)
        con.commit()
        # 复核结构，但**不删行**（用户裁决，见 AGENTS.md D.2）：
        # 「有内容就入库，格式问题可事后 prune，不要为了格式丢掉唯一的一次读图结果」。
        # 这里原先写后即 DELETE，与 vision.enhance_slice 的「照常写库 + 记 schema_ok=False」
        # 自相矛盾 —— 实测有一次批量因此丢了 37 片已经读到图的总结（28 片缺字段、
        # 4 片某节要点不足、4 片缺 image_descriptions），只能重烧一遍模型调用。
        # 现在只分类上报，行保留；要清理跑 scripts/prune_results.py（它才是清理口径）。
        row = con.execute(
            "SELECT structured_json FROM slice_result WHERE slice_id = ?", (sid,)
        ).fetchone()
        import json as _json
        st = _json.loads(row["structured_json"]) if row and row["structured_json"] else None
        v = validate_structured(st, require_images=False)
        if not v.ok:
            return (sid, imgs, True, time.time() - t0,
                    "sum=%d 保留但结构不合格(待 prune): %s"
                    % (len(res.get("overall_summary") or ""),
                       "; ".join(e.message[:50] for e in v.errors[:2])))
        return (sid, imgs, True, time.time() - t0,
                "sum=%d rework=%s" % (len(res.get("overall_summary") or ""),
                                      res.get("rework_rounds")))
    except Exception as exc:  # noqa: BLE001 —— 单片失败不拖累其他片
        con.rollback()
        return (sid, imgs, False, time.time() - t0, "%s: %s" % (type(exc).__name__, str(exc)[:110]))
    finally:
        con.close()


# ── 额度看门狗 ────────────────────────────────────────────────────────────
# 上游**不暴露额度**（2026-10-23 实测）：响应头无 x-ratelimit-*；
# /v1/usage、/v1/quota、/v1/rate_limit、/v1/me 全部 404；
# dsh-bridge 的 quota.remaining 四个通道都是 null。
# 所以只能用「极小请求探针」间接判断：成功 = 还有额度，连续失败 = 打到上限。
# 分清两种限制（它们文案很像，处置完全不同）：
#   rpm exhausted  → 每分钟限流，跨 60s 窗口即恢复，不该停整批（llm._post 已内联退避）
#   额度耗尽        → 5 小时 / 周额度，必须停下等刷新，硬跑只会一直 429 且浪费墙钟

#: 额度耗尽后每多少分钟探一次；最多等多少小时（默认覆盖一个 5 小时窗口 + 余量）
QUOTA_PAUSE_MIN = 10.0
QUOTA_MAX_WAIT_H = 6.5

_QUOTA_HINTS = ("quota", "exceeded your current", "insufficient", "balance", "billing",
                "credit", "额度", "exceeded_quota")
_RPM_HINTS = ("rpm", "rps", "tpm", "rate limit", "too many requests")


def _classify(err: str) -> str:
    """把错误分成 rpm / quota / other。额度与限流必须分开，处置不同。"""
    low = (err or "").lower()
    if any(h in low for h in _RPM_HINTS):
        return "rpm"
    if any(h in low for h in _QUOTA_HINTS):
        return "quota"
    return "other"


def probe_quota() -> tuple[bool, str]:
    """发一个极小的纯文本请求探额度。返回 (是否可用, 错误)。"""
    try:
        from digitallife.llm import ChatClient
        cli = ChatClient()
        cli.chat([{"role": "user", "content": "hi"}], max_tokens=8)
        return True, ""
    except Exception as exc:  # noqa: BLE001
        return False, "%s: %s" % (type(exc).__name__, str(exc)[:160])


def wait_for_quota(pause_min: float, max_wait_h: float) -> bool:
    """额度不可用时按 pause_min 间隔轮询，直到恢复或超出 max_wait_h。"""
    ok, err = probe_quota()
    if ok:
        print("  [额度] 探针通过", flush=True)
        return True
    kind = _classify(err)
    print("  [额度] 探针失败（判定 %s）：%s" % (kind, err), flush=True)
    if kind == "rpm":
        print("  [额度] 这是每分钟限流，等 90s 再探（不停整批）", flush=True)
        time.sleep(90)
        ok2, err2 = probe_quota()
        if ok2:
            print("  [额度] 限流已放行，继续", flush=True)
            return True
        err = err2

    t0 = time.time()
    deadline = t0 + max_wait_h * 3600
    while time.time() < deadline:
        remain = int((deadline - time.time()) / 60)
        print("  [额度] 判定为额度耗尽，暂停 %.0f 分钟后再探（剩余等待预算 %d 分钟）"
              % (pause_min, remain), flush=True)
        time.sleep(pause_min * 60)
        ok, err = probe_quota()
        if ok:
            print("  [额度] 已恢复，继续（已等 %.0f 分钟）"
                  % ((time.time() - t0) / 60), flush=True)
            return True
        print("  [额度] 仍未恢复：%s" % err, flush=True)
    print("  [额度] 超出最大等待 %.1f 小时，停止本批（已完成的都已提交，可随时续跑）"
          % max_wait_h, flush=True)
    return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", nargs="*", default=None)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--rounds", type=int, default=5)
    ap.add_argument("--device", default="pc")
    ap.add_argument("--reader", default="model",
                    choices=["model", "local", "both", "text"])
    ap.add_argument("--blind", action="store_true",
                    help="重做「有图但一张没送到模型」的历史结果（_meta.images_sent=0）")
    ap.add_argument("--pause-min", type=float, default=QUOTA_PAUSE_MIN,
                    help="额度耗尽后每多少分钟探一次（默认 %.0f）" % QUOTA_PAUSE_MIN)
    ap.add_argument("--max-wait-h", type=float, default=QUOTA_MAX_WAIT_H,
                    help="额度耗尽后最多等多少小时就收工（默认 %.1f，覆盖一个 5 小时窗口）"
                         % QUOTA_MAX_WAIT_H)
    ap.add_argument("--no-watchdog", action="store_true",
                    help="关闭额度看门狗（默认开启）")
    args = ap.parse_args()
    global _DEVICE, _READER
    _DEVICE = args.device
    _READER = args.reader

    days = args.days if args.days else ([None] if args.all else ["2026-08-13"])
    grand_ok = grand_fail = 0
    t_all = time.time()

    for day in days:
        for rnd in range(1, args.rounds + 1):
            ids = pick_pending(day, _DEVICE, blind=args.blind)
            if not ids:
                break
            if not args.no_watchdog and not wait_for_quota(args.pause_min, args.max_wait_h):
                # 额度恢复不了就干净收工：已完成的都已 commit，pick_pending 幂等，随时可续跑
                print("额度恢复失败，本轮提前结束（已完成的都已入库）", flush=True)
                break
            label = (day or "ALL") + (" [重做盲总结]" if args.blind else "")
            print("\n=== %s 第 %d 轮：待处理 %d 片，并发 %d ===" % (label, rnd, len(ids), args.workers),
                  flush=True)
            ok = fail = quota_fail = 0
            with ThreadPoolExecutor(max_workers=args.workers) as ex:
                futs = {ex.submit(run_one, it, args.blind): it for it in ids}
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
                        if _classify(info) == "quota":
                            quota_fail += 1
                        print("  [%d/%d] %s %2d图 %6.1fs FAIL %s" % (i, len(ids), sid[:12], imgs, dt, info),
                              flush=True)
            print("%s 第 %d 轮：成功 %d / 失败 %d（其中额度类 %d）"
                  % (label, rnd, ok, fail, quota_fail), flush=True)
            if fail == 0:
                break
            # 整轮全失败且多数是额度类 → 不必硬跑下一轮，让看门狗去等
            if quota_fail and quota_fail >= max(1, fail * 0.6):
                print("本轮多数失败属额度类，交给看门狗判断", flush=True)

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