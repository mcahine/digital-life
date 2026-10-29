#!/usr/bin/env python
"""把**已有**视觉总结的文本改写成日记体 —— 不碰图片，不重新识别。

为什么需要这个脚本（2026-10-24 用户纠正）：
  改了片层提示词之后，我想让历史片符合新格式，做法是 batch_vision --force
  重新送图识别。那是**错的**：slice_result.overall_summary 里已经有完整的文本总结，
  模型当时已经把图看过了。重新送图既慢（每片 350-460s，全量 65 小时）又浪费额度，
  而且**没有任何信息增益** —— 图没变，DB 事实没变。

  用户原话：「已经视觉识别的片不是已经有了吗，直接拿已有的片然后进行总结不是更好吗？
  你视觉识别出来的片和日报里的视觉总结是两码事」。

正确做法：只把 overall_summary 这段文本喂回去，要求改写成日记体。
不送图 → 单次请求小两个数量级 → 快得多，且内容不会丢（输入就是全部素材）。

用法::

    python scripts/rewrite_style.py                # 处理全部清单体
    python scripts/rewrite_style.py --dry-run      # 只看有多少片要改
    python scripts/rewrite_style.py --days 2026-08-13
    python scripts/rewrite_style.py --workers 16

判定「需要改写」的口径：overall_summary 里出现行首 - / * / + 列表项。
叙述体不动（幂等，可反复跑）。
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# T1 (2026-10-25): sys.path.insert removed -- package is installable via pip install -e .

from digitallife import paths  # noqa: E402
from digitallife.produce.llm import ChatClient

BULLET_RE = re.compile(r"^\s*[-*+]\s+\S", re.M)
FENCE = chr(96) * 3   # 三个反引号；用 chr 拼，避免本文件出现代码围栏字符
HEADS = ("## 关键操作", "## 主要活动", "## 重点内容")

REWRITE_PROMPT = """下面是一段已经写好的「某个时间段做了什么」的总结，格式是三节清单。
请把它改写成**日记体**：内容完全保留，只改写法。

要求：
1. 保留且只有这三个二级标题，顺序不变：## 关键操作 / ## 主要活动 / ## 重点内容
2. **每一节写成连贯的叙述段落**，不要用 Markdown 列表项（不要以 - 或 * 开头），
   也不要每句单独占一行 —— 用逗号或句号把句子连成一段。
3. **不要添加原文没有的信息**，不要编造画面细节，不要写时间戳前缀。
4. 保留原文里的具体名词（应用名、文档名、主题、结论），这些是内容不是格式。
5. 篇幅与原文相当，可略作合并，但不许删掉实质性内容。

【正确示例】
## 关键操作
先打开官网进入 API 文档首页，查看接入说明和基础参数表，接着跳到更新日志页，
逐条读过版本变更、缓存与接口更新的条目。随后切到哔哩哔哩，在同一个视频页面
持续往下滑评论区，看大家围绕版本发布和缓存功能的讨论。

【错误示例（不要这样写）】
## 关键操作
- 先打开官网进入 API 文档首页，查看接入说明和基础参数表。
- 接着跳到更新日志页，逐条阅读版本变更内容。

下面是需要改写的原文：
<<<
{summary}
>>>

只输出改写后的 Markdown，不要解释，不要输出 JSON，不要用代码围栏包裹。"""


def needs_rewrite(summary: str) -> bool:
    return bool(BULLET_RE.search(summary or ""))


def pick_targets(day: str | None, device: str) -> list[tuple[str, str]]:
    con = sqlite3.connect(paths.db_path())
    con.row_factory = sqlite3.Row
    from digitallife import devices as _dv

    aliases = _dv.query_aliases(device)
    sql = (
        "SELECT s.slice_id, r.overall_summary FROM slice s "
        "JOIN slice_result r ON r.slice_id = s.slice_id "
        "WHERE s.device IN (%s)" % ",".join("?" * len(aliases))
    )
    args: list = list(aliases)
    if day:
        sql += " AND date(s.start_utc_ms/1000,'unixepoch','+8 hours') = ?"
        args.append(day)
    sql += " ORDER BY s.start_utc_ms"
    out = []
    for r in con.execute(sql, args):
        if needs_rewrite(r["overall_summary"]):
            out.append((r["slice_id"], r["overall_summary"]))
    con.close()
    return out


def rewrite_one(client: ChatClient, sid: str, summary: str) -> tuple[str, str, str]:
    """返回 (sid, 新文本或空, 说明)。不碰数据库，由调用方落库。"""
    t0 = time.time()
    prompt = REWRITE_PROMPT.replace("{summary}", summary[:12000])
    try:
        resp = client.chat([{"role": "user", "content": prompt}], max_tokens=4096)
    except Exception as exc:  # noqa: BLE001 —— 单片失败不拖累其他片
        return sid, "", "%s: %s" % (type(exc).__name__, str(exc)[:100])

    text = str((resp or {}).get("content") or "").strip()
    if text.startswith(FENCE):
        text = text[len(FENCE):]
    if text.endswith(FENCE):
        text = text[: -len(FENCE)]
    text = text.strip()
    if text.startswith("md"):
        text = text[2:].lstrip()
    if not text:
        return sid, "", "空内容"
    if needs_rewrite(text):
        return sid, "", "改写后仍含列表项"
    for h in HEADS:
        if h not in text:
            return sid, "", "缺少小节 %s" % h
    if len(text) < len(summary) * 0.4:
        return sid, "", "内容疑似丢失（%d -> %d 字）" % (len(summary), len(text))
    return sid, text, "%.1fs %d->%d 字" % (time.time() - t0, len(summary), len(text))


def save(sid: str, new_summary: str) -> None:
    con = sqlite3.connect(paths.db_path())
    con.row_factory = sqlite3.Row
    row = con.execute(
        "SELECT structured_json, overall_summary FROM slice_result WHERE slice_id = ?", (sid,)
    ).fetchone()
    if not row:
        con.close()
        return
    data = json.loads(row["structured_json"] or "{}")
    data["overall_summary"] = new_summary
    meta = data.setdefault("_meta", {})
    # 记清楚这一条是「文本改写」而不是「重新读图」：两者证据强度不同。
    meta["style_rewritten"] = True
    meta["style_rewrite_ts"] = int(time.time() * 1000)
    meta["summary_before_len"] = len(row["overall_summary"] or "")
    con.execute(
        "UPDATE slice_result SET overall_summary = ?, structured_json = ? WHERE slice_id = ?",
        (new_summary, json.dumps(data, ensure_ascii=False), sid),
    )
    con.commit()
    con.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", nargs="*", default=None)
    ap.add_argument("--device", default="pc")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    days = args.days if args.days else [None]
    client = ChatClient()
    total_ok = total_fail = 0
    t_all = time.time()

    for day in days:
        targets = pick_targets(day, args.device)
        label = day or "ALL"
        print("=== %s：需要改写 %d 片 ===" % (label, len(targets)), flush=True)
        if args.dry_run or not targets:
            continue
        ok = fail = 0
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            futs = {ex.submit(rewrite_one, client, sid, s): sid for sid, s in targets}
            for i, f in enumerate(as_completed(futs), 1):
                sid, new, info = f.result()
                if new:
                    save(sid, new)
                    ok += 1
                    total_ok += 1
                    print("  [%d/%d] %s %s" % (i, len(targets), sid[:12], info), flush=True)
                else:
                    fail += 1
                    total_fail += 1
                    print("  [%d/%d] %s FAIL %s" % (i, len(targets), sid[:12], info), flush=True)
        print("%s：成功 %d / 失败 %d" % (label, ok, fail), flush=True)

    print("\n完成：成功 %d / 失败 %d，耗时 %.1f 分钟"
          % (total_ok, total_fail, (time.time() - t_all) / 60), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
