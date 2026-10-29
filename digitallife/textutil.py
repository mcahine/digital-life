"""跨层共享的小工具（T3 收敛点）。

为什么建这个模块：`_parse_json` 在 `daily.py:88` 与 `publish.py:209` **逐字相同**地
各写了一份。两份实现的直接后果是「改一处忘另一处」——属于 layering.md P1-5 判决的
「同一概念多套实现」。收敛到这里后，改 JSON 容错策略只有一个地方。

**只收真正重复的**：本模块目前只有 `parse_json`。不要因为"建了个模块"就顺手
把只有一处使用的小函数也搬进来——那会让 import 图无谓变复杂。
"""
from __future__ import annotations

import json


def parse_json(s):
    """尽力把值解析成 dict/list；解析不了返回 None（**不抛异常**）。

    容错语义（三条都要保持，历史调用方依赖它们）：
      1. 空值 / False / 0 等假值 → None（不是 {}，调用方用 `if x:` 判空）
      2. 已经是 dict/list → **原样返回**（库里同一列既有 JSON 文本也有已解析对象）
      3. 文本解析失败（非法 JSON、类型不对）→ None，不抛
    """
    if not s:
        return None
    if isinstance(s, (dict, list)):
        return s
    try:
        return json.loads(s)
    except (TypeError, ValueError):
        return None
