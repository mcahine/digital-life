"""链路健康检测 —— **通用内核**（2026-11-02）。

用户原话：「让程序有个检测机制，这个检测函数保持通用性」。

## 为什么需要（这轮验收的痛）

验收被反复卡住，根因是**没有唯一判据**：
    12,377 / 39,283 / 7,452 / 4,925 各说一套，每次都靠手工拼 SQL 复算，
    而且没人能一眼说清「现在到底哪里坏了」。

## 设计三条

1. **检测器不认识业务**。它只认一种东西：被测项描述（Probe）。
   新增一类检查 = 加一条 Probe 描述，**不改检测本体**。

       Probe(id, title, category, measure, expect, severity, hint)
           id       稳定标识（报告与对拍都靠它）
           title    人读名称
           category 分类（coverage/reachability/policy/integrity）
           measure  取值函数 (ctx) -> 数值/字符串
           expect   期望（AtLeast/AtMost/Between/Equals/Truthy）
           severity fail/warn/info —— fail 影响退出码，warn 只提示
           hint     不满足时**指向该修哪一环**

2. **判据单一出处**。同一个量的定义只写一次（见 LINK_PROBES），
   脚本、测试、体检命令、交付报告都引用它 —— 不再各算一套。
   历史教训：\`link_title\` 单表统计得出「可回填上限 897」，
   真相是 32,024（标题躺在 \`event\` 表里）。口径写在一处就不会再分叉。

3. **检测只判断，不行动**。熔断、降并发、中止跑批由 Strategy 读
   \`Report.vetoes\` 自己决定。两层分开，改判断不影响行为。
   \`DomainGuard\` 就是这样一个 Strategy 的消费者。

## 快检 vs 全检

    fast=True  只跑不联网、不扫盘的行数类（秒级，日常迭代）
    fast=False 额外跑需要遍历/统计的分类（跑批前后全量核对）
两者**判据同源**，只是深度不同 —— 不造第二套标准。
"""
from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

# ============================================================ 期望值

@dataclass(frozen=True)
class Expect:
    """期望值的统一表达。子类决定如何判定。"""

    def check(self, value) -> tuple[bool, str]:
        raise NotImplementedError

    def describe(self) -> str:
        raise NotImplementedError


@dataclass(frozen=True)
class AtLeast(Expect):
    """必须 >= n。"""
    n: float

    def check(self, v) -> tuple[bool, str]:
        try:
            ok = float(v) >= self.n
        except (TypeError, ValueError):
            return False, "取值不是数值: %r" % (v,)
        return ok, "" if ok else "实测 %s < 下限 %s" % (v, self.n)

    def describe(self) -> str:
        return ">= %s" % self.n


@dataclass(frozen=True)
class AtMost(Expect):
    """必须 <= n。"""
    n: float

    def check(self, v) -> tuple[bool, str]:
        try:
            ok = float(v) <= self.n
        except (TypeError, ValueError):
            return False, "取值不是数值: %r" % (v,)
        return ok, "" if ok else "实测 %s > 上限 %s" % (v, self.n)

    def describe(self) -> str:
        return "<= %s" % self.n


@dataclass(frozen=True)
class Between(Expect):
    """必须落在 [lo, hi]。"""
    lo: float
    hi: float

    def check(self, v) -> tuple[bool, str]:
        try:
            x = float(v)
        except (TypeError, ValueError):
            return False, "取值不是数值: %r" % (v,)
        ok = self.lo <= x <= self.hi
        return ok, "" if ok else "实测 %s 不在 [%s, %s]" % (v, self.lo, self.hi)

    def describe(self) -> str:
        return "in [%s, %s]" % (self.lo, self.hi)


@dataclass(frozen=True)
class Equals(Expect):
    want: object

    def check(self, v) -> tuple[bool, str]:
        ok = v == self.want
        return ok, "" if ok else "实测 %r != 期望 %r" % (v, self.want)

    def describe(self) -> str:
        return "== %r" % (self.want,)


@dataclass(frozen=True)
class Truthy(Expect):
    """非空 / 非零 / 非 False。"""

    def check(self, v) -> tuple[bool, str]:
        ok = bool(v)
        return ok, "" if ok else "取值为空"

    def describe(self) -> str:
        return "非空"


# ============================================================ 被测项

@dataclass
class Probe:
    """一条被测项描述。**检测器本体只认这个结构，不认识任何业务。**"""
    id: str
    title: str
    category: str                     # coverage | reachability | policy | integrity
    measure: Callable                 # (ctx) -> 数值/字符串
    expect: Expect
    severity: str = "fail"            # fail | warn | info
    hint: str = ""                    # 不满足时指向该修哪一环
    slow: bool = False                # True = 只在全检时跑


@dataclass
class Result:
    probe_id: str
    title: str
    category: str
    severity: str
    value: object
    expect: str
    passed: bool
    detail: str = ""
    hint: str = ""
    ms: float = 0.0

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class Report:
    """一次检测的完整结论。**可 JSON 序列化，便于前后对拍。**"""
    started: float
    finished: float
    fast: bool
    tag: str = ""                      # 跑批标识（如 run-20261102-1530）
    params: dict = field(default_factory=dict)
    results: list = field(default_factory=list)
    error: str = ""

    # ---- 汇总 ----
    def by_severity(self, sev: str) -> list:
        return [r for r in self.results if r.severity == sev]

    @property
    def failed(self) -> list:
        return [r for r in self.results if not r.passed and r.severity == "fail"]

    @property
    def warned(self) -> list:
        return [r for r in self.results if not r.passed and r.severity == "warn"]

    @property
    def passed(self) -> bool:
        return not self.failed

    def by_category(self) -> dict:
        out: dict = {}
        for r in self.results:
            d = out.setdefault(r.category, {"pass": 0, "fail": 0, "warn": 0})
            if r.passed:
                d["pass"] += 1
            elif r.severity == "fail":
                d["fail"] += 1
            else:
                d["warn"] += 1
        return out

    def vetoes(self) -> dict:
        """**给 Strategy 读的结构化结论**（检测自己不用它做事）。

        key = probe_id, value = {"value":…, "detail":…, "hint":…}
        Strategy（如 DomainGuard）按 id 取自己关心的量决定行动。
        """
        return {r.probe_id: {"value": r.value, "detail": r.detail, "hint": r.hint}
                for r in self.results if not r.passed}

    def counts(self) -> dict:
        """可直接对拍的关键计数（改动前后结构一致）。"""
        out: dict = {}
        for r in self.results:
            out[r.probe_id] = r.value
        return out

    def to_json(self) -> str:
        return json.dumps({
            "started": self.started,
            "finished": self.finished,
            "fast": self.fast,
            "tag": self.tag,
            "params": self.params,
            "elapsed_s": round(self.finished - self.started, 3),
            "passed": self.passed,
            "counts": self.counts(),
            "by_category": self.by_category(),
            "results": [r.as_dict() for r in self.results],
            "error": self.error,
        }, ensure_ascii=False, indent=2, default=str)

    def summary(self) -> str:
        cats = self.by_category()
        parts = ["%s 通过%d/%d" % (c, d["pass"], d["pass"] + d["fail"] + d["warn"])
                 for c, d in sorted(cats.items())]
        return "｜".join(parts)


# ============================================================ 检测器

def run(probes, ctx=None, fast: bool = True, tag: str = "",
        params: dict | None = None) -> Report:
    """跑一组 Probe。**这是检测器的全部**——不认识任何业务。

    fast=True 跳过 Probe.slow 的项（判据同源，只是深度不同）。
    单条 Probe 抛异常不中断整批：记为该条失败并带异常名。
    """
    t0 = time.time()
    rep = Report(started=t0, finished=t0, fast=fast, tag=tag,
                 params=dict(params or {}))
    for p in probes:
        if fast and p.slow:
            continue
        s = time.time()
        try:
            v = p.measure(ctx)
            ok, detail = p.expect.check(v)
        except Exception as exc:  # noqa: BLE001 —— 单条坏了不影响其余
            v, ok, detail = None, False, "%s: %s" % (type(exc).__name__, str(exc)[:120])
        rep.results.append(Result(
            probe_id=p.id, title=p.title, category=p.category,
            severity=p.severity, value=v, expect=p.expect.describe(),
            passed=ok, detail=detail, hint="" if ok else p.hint,
            ms=round((time.time() - s) * 1000, 1)))
    rep.finished = time.time()
    return rep


def log_report(rep: Report, out_dir: Path | None = None,
               name: str = "linkhealth") -> Path:
    """把结论落盘（带时间戳与跑批标识），供跨版本对拍。

    2026-11-02 约定：日志落 tmp/（项目既有约定），不进 git。
    """
    from digitallife import paths
    d = Path(out_dir) if out_dir else (Path(paths.PROJECT) / "tmp" / "linkhealth")
    d.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    suffix = ("-" + rep.tag) if rep.tag else ""
    p = d / ("%s-%s%s.json" % (name, stamp, suffix))
    p.write_text(rep.to_json(), encoding="utf-8")
    return p
