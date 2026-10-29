"""日层汇总（原 daily.py，T4 拆分为 6 个文件）。

**对外 API 与拆分前完全一致**：\`from digitallife.daily import build_daily\` 照旧可用。
重导出让拆分对调用方透明——publish.py / cli.py / rebuild_daily.py / smoke.py 一行都不用改。
"""
from __future__ import annotations

from .render_body import _cjk_gap_before  # 兼容：拆分前模块级可访问
from .render import _EMPTY_SECTION  # 兼容：拆分前模块级可访问
from .render_body import _render_device, _no_visual_basis  # 兼容：拆分前模块级可访问
from .render import _md_link, _sites_summary, _span_seconds, _top_apps_section  # 兼容：拆分前模块级可访问
from .build import build_daily
from .prompt import DAILY_PROMPT
from .render_body import render_narrative, render_daily
from .structure import (day_window, device_structure, normalize_date,
                        slices_for_day)

__all__ = [
    "DAILY_PROMPT",
    "LAYER",
    "LAYERS",
    "build_daily",
    "day_window",
    "device_structure",
    "normalize_date",
    "render_daily",
    "render_narrative",
    "slices_for_day",
]