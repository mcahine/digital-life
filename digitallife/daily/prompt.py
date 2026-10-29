"""日层叙事的提示词与 LLM 调用。

从 daily.py 拆出（T4，2026-10-25 纯搬迁，不改任何函数体）。
"""
from __future__ import annotations

import json
from .render import _strip_md_heads
from .render_body import _device_label


def _collect_slice_summaries(slices_by_dev: dict, limit_chars: int = 0) -> list[dict]:
    """收集当天各片的**完整**总结（供模型写日报）。

    不截断、不去小标题 —— 用户明确要求：片层总结必须由模型写全，
    日报再由模型对这些片段做二次总结。Python 只负责搬运原料。
    """
    out: list[dict] = []
    for dev, sl in (slices_by_dev or {}).items():
        for s in sorted(sl, key=lambda x: str(x.get("start") or "")):
            summary = (s.get("overall_summary") or "").strip()
            if not summary:
                continue
            item = {"device": dev, "from": s.get("start") or "", "to": s.get("end") or "",
                    "summary": summary}
            if limit_chars and len(summary) > limit_chars:
                item["summary"] = summary[:limit_chars]
            out.append(item)
    return out


# 按设备叙事用的提示词（2026-10-25）。与整天版 DAILY_PROMPT 的差别有两处必须记住：
#   ① 明令禁止输出任何 `#` 开头的行 —— 日报骨架的标题由程序产出，模型写一个 `##` 就顶乱层级
#      （旧版只写「不要写标题」，实测 2026-08-13 模型仍写了 `## 凌晨启动：…`）。
#   ② 明确「只写这一台设备当天做了什么」—— 料是按 device 切好的，越界就是把另一台的事挪过来。
DAILY_PROMPT = """你在为「{date}」写一篇个人数字人生日报。
现在只写**一台设备**（{device}）当天的部分，不是整天。

下面是这台设备这一天里，各个时间段（片段）由视觉模型已经写好的总结原文。
请**只依据这些原文**，把它们编织成连贯的中文自然段。

写作要求（务必遵守）：
0. **只写 {device} 这一台设备当天做了什么**。原文里若提到别的事，也不要扩写成别的设备的内容。
1. **不要写任何以 # 开头的行**。不要写标题、不要写小节名、不要写「日报」「总结」这类开场，
   直接进入第一段。标题（含 `#` 与 `###`）一律由程序生成，你写了就会顶乱文档骨架。
2. **用自然段写，不要用列表、不要用时间轴、不要逐条罗列**。读者要能像读日记一样读下去。
3. **不要省略内容**。所有片段里出现过的重要事情都要写进去；绝对不要用「……」或「等等」跳过。
4. 不要编造原文里没有的应用、网站或事件。
5. 不要写周/月/季/年总结，不要给建议，不要写「总的来说」这类空话。
6. 分段建议：先写这一天的状态（在做什么、投入在哪），再按事情本身的发展讲，最后收束到今天的结果。
7. 篇幅随内容而定，宁可长一些，也不要压缩掉细节。

片段总结原文（按时间顺序）：
{segments}
"""


def _llm_narrative(client, date_key: str, breakdown: dict,
                   slices_by_dev: dict | None = None,
                   device: str | None = None) -> tuple[str | None, str | None]:
    """交给模型写日报正文。未配置或失败一律降级为纯模板，不阻塞日报生成。

    device 非空时按**单台设备**写（原料只取该设备的片层总结），是三级降级的第 1 级；
    device 为空时沿用整天写法（兼容旧调用点）。
    """
    if client is None or not hasattr(client, "chat"):
        return None, None

    segs = _collect_slice_summaries(
        {device: (slices_by_dev or {}).get(device, [])} if device else (slices_by_dev or {}))
    if segs:
        seg_text = "\n\n".join(
            "[%s %s-%s]\n%s" % (s["device"], s["from"], s["to"], s["summary"]) for s in segs)
        prompt = DAILY_PROMPT.format(date=date_key,
                                     device=_device_label(device) if device else "全部",
                                     segments=seg_text)
    elif device:
        # 该设备当天没有片层总结：不调模型（省一次调用），交给上级降级文案
        return None, None
    else:
        # 没有视觉总结时，退化为只用统计事实（这种情况日报内容本来就很有限）
        facts = {
            "date": date_key,
            "devices": {
                d: {"active_s": v["active_s"], "event_count": v["event_count"],
                    "top_apps": [a["app"] for a in v.get("apps_top", [])[:6]]}
                for d, v in breakdown["devices"].items()
            },
        }
        prompt = ("只依据下面事实，用自然段写一段中文日报正文（不要列表、不要时间轴、"
                  "不要编造、不要写任何以 # 开头的行）：\n"
                  + json.dumps(facts, ensure_ascii=False))

    try:
        resp = client.chat([{"role": "user", "content": prompt}], max_tokens=8192)
    except Exception:  # noqa: BLE001 —— 模型故障不阻塞日报
        return None, None
    if not isinstance(resp, dict):
        return None, None
    text = (resp.get("content") or "").strip()
    # 双保险：提示词已明令禁止，但实测旧版仍会写 `## 凌晨启动：…`。
    # 这里再剥一次（转成加粗而不是删除 —— 删了会丢掉叙事的层次，且不可重算）。
    return (_strip_md_heads(text) or None), (resp.get("model") or None)


# ---------------------------------------------------------------- 主入口
