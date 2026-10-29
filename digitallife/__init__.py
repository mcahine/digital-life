"""digital-life —— 个人数字痕迹统一数据集。

五源（manictime / screenmemo / iqqz3 / tai / shion）清洗成一张统一 event 表，
落在唯一的时间轴上；device 只是事件的维度字段，不是分区。
"""
__version__ = "0.1.0"

# ---- N6 兼容层（2026-10-25）：旧路径继续可用，新代码请用 digitallife.ingest.*
# 搬迁：model store schema clean tz sources -> digitallife/ingest/
from .ingest import clean, model, schema, sources, store, tz  # noqa: F401

import sys as _sys

_ALIASES = {
    "model": model, "store": store, "schema": schema,
    "clean": clean, "tz": tz, "sources": sources,
}
for _n, _m in _ALIASES.items():
    _sys.modules.setdefault("digitallife." + _n, _m)
del _n, _m, _ALIASES, _sys

# ---- N6 兼容层 2（2026-10-25）：domain 层旧路径继续可用
from .domain import dbview, devices, slicer, timeline_merge  # noqa: F401

import sys as _s2

_D = {
    "slicer": slicer, "dbview": dbview,
    "devices": devices, "timeline_merge": timeline_merge,
}
for _n, _m in _D.items():
    _s2.modules.setdefault("digitallife." + _n, _m)
del _n, _m, _D, _s2

# ---- N6 兼容层 3（2026-10-25）：produce 层旧路径继续可用
from .produce import llm, ocr, prompts, schema_check, vision  # noqa: F401

import sys as _s3

_P = {
    "vision": vision, "llm": llm, "schema_check": schema_check,
    "ocr": ocr, "prompts": prompts,
}
for _n, _m in _P.items():
    _s3.modules.setdefault("digitallife." + _n, _m)
del _n, _m, _P, _s3

# ---- N6 兼容层 4（2026-10-25）：emit + render 层；旧包名 slice_pages 继续可用
from . import render  # noqa: F401
from .emit import publish  # noqa: F401

import sys as _s4

_s4.modules.setdefault('digitallife.publish', publish)

# slice_pages -> digitallife.render（N6-d 整包改名，旧 import 一律保活）
_RENDER_SUB = ['apps', 'base', 'contract',                'hourbar', 'linktitle', 'timeline',                'vision', 'web']
_s4.modules.setdefault('slice_pages', render)
for _n in _RENDER_SUB:
    _m = getattr(render, _n, None)
    if _m is not None:
        _s4.modules.setdefault('slice_pages.' + _n, _m)
del _n, _m, _RENDER_SUB, _s4
