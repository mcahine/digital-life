"""T1 冒烟：包可导入、可安装（这是 pytest 收集成功的前提）。"""
from __future__ import annotations


def test_digitallife_importable():
    import digitallife
    assert digitallife.__file__


def test_slice_pages_importable():
    """呈现层物理上在 scripts/slice_pages/，但必须能以顶层 slice_pages 导入。

    这是 pyproject 的 package-dir 映射生效的证明（T1 目标之一）。
    """
    import slice_pages
    assert slice_pages.__file__


def test_core_modules_import():
    from digitallife import clean, daily, devices, model, paths, store, tz  # noqa: F401
    from digitallife.render import base, contract, hourbar, timeline, vision, web  # noqa: F401
