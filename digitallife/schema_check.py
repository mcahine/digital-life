"""结构化产出校验器 —— AI 总结必须满足的硬性结构。

设计原则（用户要求「结构不合理就打回重做」）：
1. 校验与生成分离：本模块**不调用模型、不写库**，只回答「这份 structured 合不合格」。
2. 失败带**可执行的打回理由**（哪条规则、哪个字段、期望什么），而不是一句「格式错误」。
3. 分级：ERROR（必须打回重做）/ WARN（记入 _meta，不阻断）。

字段来源：digitallife/prompts/segment.py 的 REQUIRED_KEYS 与 OUTPUT 模板。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

REQUIRED_KEYS = (
    "apps", "categories", "timeline", "key_actions",
    "described_images", "image_tags", "image_descriptions", "overall_summary",
)

SUMMARY_HEADS = ("## 关键操作", "## 主要活动", "## 重点内容")

#: R5 的判定口径：每一节去掉标题后至少要有这么多字。
#: 2026-10-24 之前 R5 要求「每节 ≥3 条 Markdown 列表项」，那是清单体旧格式；
#: 用户要求视觉总结改成日记体（连贯叙述、不带时间戳前缀、不是清单）后，
#: 列表项天然为 0，旧规则会把所有合格式的片判成不合格，故改为按字数判。
MIN_SECTION_CHARS = 30
HMS = re.compile(r"^\d{1,2}:\d{2}:\d{2}$")


@dataclass
class Issue:
    rule: str
    message: str
    severity: str = "error"          # error | warn

    def as_dict(self) -> dict:
        return {"rule": self.rule, "message": self.message, "severity": self.severity}


@dataclass
class Verdict:
    ok: bool
    issues: list[Issue] = field(default_factory=list)

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warns(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "warn"]

    def reason(self) -> str:
        """给模型看的打回说明：只列 error，带规则名与期望。"""
        lines = [f"- [{i.rule}] {i.message}" for i in self.errors]
        return "\n".join(lines)

    def as_dict(self) -> dict:
        return {"ok": self.ok,
                "errors": [i.as_dict() for i in self.errors],
                "warnings": [i.as_dict() for i in self.warns]}


def _is_list(v) -> bool:
    return isinstance(v, list)


def _hms_ok(v) -> bool:
    return isinstance(v, str) and bool(HMS.match(v.strip()))


def validate_structured(structured: dict | None, *, image_count: int = 0,
                        db_apps: set[str] | None = None,
                        require_images: bool = False) -> Verdict:
    """检查结构化结果是否合格。不合格 -> ok=False，errors 里带可执行理由。"""
    issues: list[Issue] = []

    if not isinstance(structured, dict) or not structured:
        return Verdict(False, [Issue("root", "structured 为空或不是 JSON 对象，无法校验")])

    # R1 必备键齐全
    missing = [k for k in REQUIRED_KEYS if k not in structured]
    if missing:
        issues.append(Issue("R1.required_keys",
                            "缺少必备字段：%s（8 个字段缺一不可）" % "、".join(missing)))

    # R2 类型正确
    for k in ("apps", "categories", "timeline", "key_actions",
              "described_images", "image_tags", "image_descriptions"):
        if k in structured and not _is_list(structured[k]):
            issues.append(Issue("R2.type", "%s 必须是数组，实际是 %s"
                                % (k, type(structured[k]).__name__)))
    if "overall_summary" in structured and not isinstance(structured["overall_summary"], str):
        issues.append(Issue("R2.type", "overall_summary 必须是字符串"))

    summary = structured.get("overall_summary")
    if isinstance(summary, str):
        s = summary.strip()
        # R3 总结非空且像样
        if len(s) < 40:
            issues.append(Issue("R3.summary_len",
                                "overall_summary 只有 %d 字，明显不足以概括一个时间段（要求 ≥40 字）"
                                % len(s)))
        # R4 三节标题齐全且顺序正确
        pos = []
        for h in SUMMARY_HEADS:
            i = s.find(h)
            if i < 0:
                issues.append(Issue("R4.summary_heads",
                                    "overall_summary 缺少小节「%s」（三个二级标题缺一不可）" % h))
            else:
                pos.append((i, h))
        if len(pos) == len(SUMMARY_HEADS) and [h for _, h in sorted(pos)] != list(SUMMARY_HEADS):
            issues.append(Issue("R4.summary_heads",
                                "三个小节的顺序必须是 关键操作 → 主要活动 → 重点内容"))
        # R5 每节必须有实质内容
        #
        # 2026-10-24 改：原规则要求「每节至少 3 条 Markdown 列表项」，那是旧格式
        # （清单体）的产物。用户要求视觉总结「像写日记一样」，改成连贯叙述段落，
        # 于是列表项天然为 0 —— 旧规则会把所有合格式的片都判不合格。
        #
        # 新规则按「实质内容量」判：每节去掉标题与空白后至少 MIN_SECTION_CHARS 字。
        # 这样既能拦住空节与敷衍的一句话，又不强制某种排版。
        if all(h in s for h in SUMMARY_HEADS):
            for idx, h in enumerate(SUMMARY_HEADS):
                start = s.find(h) + len(h)
                end = s.find(SUMMARY_HEADS[idx + 1]) if idx + 1 < len(SUMMARY_HEADS) else len(s)
                body = s[start:end].strip()
                if len(body) < MIN_SECTION_CHARS:
                    issues.append(Issue("R5.summary_section",
                                        "「%s」只有 %d 字，内容明显不足（要求 ≥%d 字）"
                                        % (h, len(body), MIN_SECTION_CHARS)))
                # R5b 必须是日记体叙述，不能退回清单。
                # 实测（2026-10-24）：只靠提示词要求「不要列表」，模型仍会有一半概率
                # 输出「- 」开头的清单项，所以必须在校验层拦住，让 rework 循环
                # 把这条具体理由回灌模型重做 —— 这正是 schema_check 的设计用途。
                n_bullet = len(re.findall(r"^\s*[-*+]\s+\S", body, re.M))
                if n_bullet:
                    issues.append(Issue("R5b.summary_prose",
                                        "「%s」里有 %d 个列表项；要求写成连贯叙述段落（日记体），不要用 - 或 * 列出"
                                        % (h, n_bullet)))

    # R6 timeline 条目形状
    tl = structured.get("timeline")
    if _is_list(tl):
        for i, it in enumerate(tl[:50]):
            if not isinstance(it, dict):
                issues.append(Issue("R6.timeline_shape", "timeline[%d] 不是对象" % i)); continue
            if not (it.get("app") or it.get("title")):
                issues.append(Issue("R6.timeline_shape",
                                    "timeline[%d] 既无 app 也无 title" % i))
            st = it.get("start")
            if st is not None and not _hms_ok(st):
                issues.append(Issue("R6.timeline_time",
                                    "timeline[%d].start=%r 不是 HH:MM:SS" % (i, st)))

    # R7 不得编造 DB 中不存在的应用
    if db_apps:
        norm = {a.strip().lower() for a in db_apps if a and a.strip()}
        if norm and _is_list(structured.get("apps")):
            bad = []
            for it in structured["apps"]:
                name = ""
                if isinstance(it, dict):
                    name = str(it.get("name") or it.get("app") or "").strip()
                elif isinstance(it, str):
                    name = it.strip()
                if name and name.lower() not in norm:
                    bad.append(name)
            if bad:
                issues.append(Issue("R7.no_fabrication",
                                    "apps 里出现 DB 基线中不存在的应用：%s"
                                    % "、".join(bad[:5])))

    # R8 有图时必须覆盖全部图片序号
    if require_images and image_count > 0:
        for key in ("image_tags", "image_descriptions"):
            arr = structured.get(key)
            if not _is_list(arr) or not arr:
                issues.append(Issue("R8.image_coverage",
                                    "有 %d 张图，但 %s 为空（必须覆盖全部图片）"
                                    % (image_count, key)))
                continue
            idxs = set()
            for it in arr:
                if not isinstance(it, dict):
                    continue
                v = it.get("idx") or it.get("file") or it.get("from_file")
                try:
                    idxs.add(int(v))
                except (TypeError, ValueError):
                    pass
            if len(idxs) < image_count:
                issues.append(Issue("R8.image_coverage",
                                    "%s 只覆盖 %d/%d 张图" % (key, len(idxs), image_count)))

    # R9 key_actions 若存在要能解析时间区间（warn）
    ka = structured.get("key_actions")
    if _is_list(ka):
        for i, it in enumerate(ka[:50]):
            if isinstance(it, dict):
                rng = it.get("range") or ""
                if rng and "-" not in str(rng):
                    issues.append(Issue("R9.key_action_range",
                                        "key_actions[%d].range=%r 不是 HH:MM:SS-HH:MM:SS"
                                        % (i, rng), severity="warn"))

    ok = not any(i.severity == "error" for i in issues)
    return Verdict(ok, issues)