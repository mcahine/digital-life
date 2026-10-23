"""时段（slice）结构化提示词 —— 从 ScreenMemo 的 raw_request 取回后逐条固化。

硬约束的来源不是设计偏好，是实测：ScreenMemo 当初把 OCR 文本喂给模型时，模型会复述
OCR 里的按钮文字当作"行为"；改成直接看图后 key_actions 才落到真实操作序列。所以
「禁止使用OCR文本」是本提示词的第一原则，不是可选项。
"""
from __future__ import annotations

import json

PROMPT_VERSION = "screenmemo-v1"

OUTPUT_FIELDS = (
    "apps", "categories", "timeline", "key_actions",
    "described_images", "image_tags", "image_descriptions", "overall_summary",
)

CONSISTENCY_RULE = "timeline[] 与 apps[] 的应用名与时间必须与随请求附带的 DB 结构一致"


def _fmt_images(images: list[dict]) -> str:
    if not images:
        return "（本片无截图，只有 DB 侧活动记录）"
    rows = []
    for item in images:
        rows.append(
            "  - 图 %s｜时间 %s｜DB 记录的应用：%s"
            % (item.get("idx", "?"), item.get("time", "?"), item.get("app") or "（未知）")
        )
    return "\n".join(rows)


def _fmt_timeline(db_timeline: list[dict]) -> str:
    if not db_timeline:
        return "（DB 侧本窗无事件）"
    rows = []
    for item in db_timeline:
        rows.append(
            "  - %s-%s｜%s｜%s"
            % (item.get("start", "?"), item.get("end", "?"),
               item.get("app") or "（未知应用）", item.get("title") or "")
        )
    return "\n".join(rows)


def build_prompt(slice_meta: dict) -> str:
    """按片元数据生成完整提示词；返回的字符串会被 chat_images / chat 直接发出。"""
    start = slice_meta.get("start", "?")
    end = slice_meta.get("end", "?")
    images = list(slice_meta.get("images") or [])
    db_timeline = list(slice_meta.get("db_timeline") or [])
    db_apps = sorted({(t.get("app") or "").strip() for t in db_timeline if (t.get("app") or "").strip()})
    total = len(images)
    described_cap = total // 3
    idx_list = ", ".join(str(i.get("idx", "?")) for i in images) or "（无）"
    return _TEMPLATE.replace("<START>", start).replace("<END>", end).replace(
        "<TOTAL>", str(total)
    ).replace(
        "<DB_APPS>", json.dumps(db_apps, ensure_ascii=False)
    ).replace(
        "<DB_TIMELINE>", _fmt_timeline(db_timeline)
    ).replace(
        "<CONSISTENCY_RULE>", CONSISTENCY_RULE
    ).replace(
        "<IMAGES>", _fmt_images(images)
    ).replace(
        "<INDICES>", idx_list
    ).replace(
        "<CAP>", str(described_cap)
    )


_TEMPLATE = """你是一个严格按数据库事实工作的屏幕活动分析器。本次任务：理解 <START>-<END> 这 <TOTAL> 张截图，产出一份结构化 JSON。

## 一、绝对禁令（违反即本次输出作废）

1. 禁止使用OCR文本：不要读取、引用、复述任何 OCR／文字识别结果；即便我在下面贴了 OCR 内容，也只作校对参考，绝不得据此编造画面。请直接理解图片本身——看界面布局、窗口、按钮、内容区域来判断用户到底在做什么。
2. 只输出一个 JSON 对象，不要 JSON 之外的任何解释、前言、结语、代码围栏。
3. 不得编造：画面不足以支撑某个字段时，把该字段留空（空字符串／空数组），不要为了填满而想象。
4. 不要统计你没有看到的界面；不要把"窗口标题里出现的词"当作已发生的操作。

## 二、DB 结构（唯一的事实基线）

本窗 DB 侧应用名单（只允许出现这些应用名）：
<DB_APPS>

本窗 DB 侧时间线：
<DB_TIMELINE>

一致性硬约束：<CONSISTENCY_RULE>；不得新增 DB 中不存在的应用；DB 与画面冲突时以 DB 为准，把差异写进 described_images 说明，不要改写 timeline 去迁就画面。

## 三、随本次请求附带的截图

<IMAGES>

截图序号全集：<INDICES>

## 四、输出字段（缺一不可，顺序照此）

{
  "apps": [{"name": "应用名（必须来自上面的 DB 名单）", "duration": "HH:mm:ss 或秒数", "note": "该应用在本窗做了什么"}],
  "categories": [{"category": "类别名", "share": "占比或时长", "evidence": "画面依据"}],
  "timeline": [{"start": "HH:mm:ss", "end": "HH:mm:ss", "app": "应用名", "title": "窗口/页面标题"}],
  "key_actions": [{"range": "HH:mm:ss-HH:mm:ss", "action": "行为描述", "detail": "更具体的说明"}],
  "described_images": [{"idx": 1, "description": "这张图里到底有什么"}],
  "image_tags": [{"idx": 1, "tags": ["标签1", "标签2"]}],
  "image_descriptions": [{"idx": 1, "description": "尽量具体的画面描述，末行写 关键词：…", "keywords": "关键词1,关键词2"}],
  "overall_summary": "见 4.4 的三节格式"
}

### 4.1 key_actions
每条的 range 必须是区间格式 HH:mm:ss-HH:mm:ss（中间一个半角连字符），例如 09:15:00-09:22:30。
只写你从画面里确凿看到的操作；区间必须落在 <START>-<END> 之内。

### 4.2 described_images（数量硬上限）
本片共 <TOTAL> 张图，described_images 的条目数不得超过总图数的三分之一（向下取整）= <CAP>。
挑最能代表本窗的关键画面写；其余图片不写进 described_images，但仍必须出现在 image_tags 与 image_descriptions 里。
超出上限即本次输出作废。

### 4.3 image_tags 与 image_descriptions（必须全覆盖）
两者都必须覆盖全部图片序号：<INDICES>，一个都不能漏，也不得出现不存在的序号。
image_descriptions 每一项的 description 末尾必须追加一行「关键词：…」，即最后一行形如
关键词：浏览器,代码编辑器,报错信息
这一行是检索用的，宁可多写几个词。

### 4.4 overall_summary（格式硬约束）
必须是 Markdown，且有且只有下面三个二级标题，一个都不能多、不能少、不能改名、不能出现其他二级标题：

## 关键操作
每条一个 Markdown 列表项，至少 3 条；每条以区间开头，形如 - 09:15:00-09:22:30：在浏览器里查阅报错信息

## 主要活动
每条一个 Markdown 列表项，至少 3 条；按时长从多到少排列

## 重点内容
每条一个 Markdown 列表项，至少 3 条；写具体的主题或结论，不要空泛形容词

三个小节都必须存在，且各自至少 3 条列表要点；内容不足时宁可写更细的观察，也不要编造与画面无关的东西。

## 五、再次强调
只输出 JSON。禁止使用OCR文本，直接理解图片。所有应用名与时间以 DB 结构为准。
"""
