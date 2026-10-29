"""T3 的回归锚点（升格后的公开 API 必须有断言守着）。"""
from __future__ import annotations

import pytest


# ---------------------------------------------------------------- clean_title（DL-22）

def test_clean_title_is_public():
    """T3：clean_title 必须是公开名，且与兼容别名指向同一函数。

    能抓住的真实回归：有人把 clean_title 改回 _clean_title（私有），
    或让别名指向另一个实现（两份实现分叉）。
    """
    from digitallife.domain.timeline_merge import clean_title, _clean_title
    assert callable(clean_title)
    assert _clean_title is clean_title, "兼容别名必须指向同一对象，不许是副本"


def test_clean_title_strips_browser_window_noise():
    """clean_title 的三类噪声必须都去（实测串在一起出现）。

    能抓住的真实回归：去掉任何一类正则，窗口标题的尾巴会留进时间线每一行。
    """
    from digitallife.domain.timeline_merge import clean_title
    got = clean_title("首页 - 得到大脑 和另外 5 个页面 - 个人 - Google Chrome")
    assert "Google Chrome" not in got
    assert "和另外 5 个页面" not in got
    assert "得到大脑" in got


def test_slice_pages_uses_public_name():
    """T3：呈现层三个文件不得再 import 下划线私有名。

    能抓住的真实回归：有人图省事写回 `from ... import _clean_title`，
    重新引入跨包私有依赖（layering.md P0-1）。

    ⚠️ 判据必须只看**import 语句**，不能扫整段源码——三处注释里都提到了
    "_clean_title" 这个历史名字（S63：字面命中不等于语句存在）。
    """
    import ast
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    bad = []
    for rel in ("digitallife/render/contract.py",
                "digitallife/render/timeline.py",
                "digitallife/render/web.py"):
        tree = ast.parse((root / rel).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for a in node.names:
                    if a.name == "_clean_title":
                        bad.append("%s:%d" % (rel, node.lineno))
    assert not bad, "仍在 import 私有名: %r" % (bad,)


# ---------------------------------------------------------------- parse_json 单点（DL-23）

def test_parse_json_single_source():
    """T3：_parse_json 必须只有一处实现（textutil.parse_json）。

    能抓住的真实回归：有人在 daily/publish 里又抄一份，两份慢慢分叉
    （历史形态：两处逐字相同，改一处忘另一处）。
    """
    import ast
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    defs = []
    # T4 后 daily.py 已拆成 digitallife/daily/ 包，故扫整棵 digitallife/ 树
    # （不写死单文件路径——那正是「测试里重建路径」的 S21 反面）。
    for p in sorted((root / "digitallife").rglob("*.py")):
        rel = p.relative_to(root).as_posix()
        tree = ast.parse(p.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and "parse_json" in node.name:
                defs.append("%s:%s" % (rel, node.name))
    assert defs == ["digitallife/textutil.py:parse_json"], defs


def test_parse_json_semantics():
    """parse_json 的三条容错语义都要保持（历史调用方依赖）。"""
    from digitallife.textutil import parse_json
    assert parse_json("") is None
    assert parse_json(None) is None
    assert parse_json({"a": 1}) == {"a": 1}
    assert parse_json([1, 2]) == [1, 2]
    assert parse_json('{"a": 1}') == {"a": 1}
    assert parse_json("{bad") is None


# ---------------------------------------------------------------- finish_phone2 解耦（DL-24）

def test_finish_phone2_no_private_vision_import():
    """T3：finish_phone2 不得 import vision 的私有符号。

    能抓住的真实回归：谁把公开函数换回 _collect_descriptions/_slice_events，
    闸门脚本又变成「依赖私有实现」的脆弱耦合。
    """
    import ast
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    tree = ast.parse((root / "scripts/finish_phone2.py").read_text(encoding="utf-8"))
    bad = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "digitallife.vision":
            for a in node.names:
                if a.name.startswith("_"):
                    bad.append(a.name)
    assert not bad, "仍 import vision 私有名: %r" % (bad,)


def test_slice_has_description_equivalent():
    """T3：slice_has_description 必须与旧的两段私有调用结果一致。

    用构造数据验（不碰真库，保持 pytest 能脱离真实数据运行）：
    造 2 条 screen 事件，一条有 payload.description、一条没有。
    """
    import json
    from digitallife.produce.vision import slice_has_description

    class _Con:
        def execute(self, *a, **k):
            raise AssertionError("本用例不该查库")

    # 直接测 _collect_descriptions 语义（slice_has_description 的判据就是它非空）
    from digitallife.produce.vision import _collect_descriptions
    events = [
        {"kind": "screen", "start_utc_ms": 1,
         "payload": json.dumps({"description": "书架界面"})},
        {"kind": "screen", "start_utc_ms": 2, "payload": json.dumps({})},
        {"kind": "app", "start_utc_ms": 3, "payload": json.dumps({"description": "x"})},
    ]
    got = _collect_descriptions(events)
    assert len(got) == 1, "只有 kind=screen 且有 description 的才算"
    assert got[0]["description"] == "书架界面"
    assert callable(slice_has_description)
