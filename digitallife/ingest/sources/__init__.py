"""源适配器注册表。"""
from __future__ import annotations

from .base import SourceAdapter
from .manictime import ManicTimeSource
from .screenmemo import ScreenMemoSource
from .iqqz3 import Iqqz3Source
from .tai import TaiSource
from .shion import ShionSource

ADAPTERS: dict[str, type[SourceAdapter]] = {
    "manictime": ManicTimeSource,
    "screenmemo": ScreenMemoSource,
    "iqqz3": Iqqz3Source,
    "tai": TaiSource,
    "shion": ShionSource,
}

__all__ = ["SourceAdapter", "ADAPTERS"]
