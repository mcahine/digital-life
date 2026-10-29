"""源适配器协议 —— 五源唯一的入口形状。

一个适配器只需要回答两件事：
  1. fingerprint()：这份原始件是什么（sha256/字节/时间窗），供 source_file 表与 manifest 审计；
  2. iter_events()：逐条产出统一 Event。
清洗规则（丢弃/合并/去重）不在适配器里各写一遍，统一走 clean.py。
"""
from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from ..clean import SourceReport
from ..model import Event


@dataclass(slots=True)
class Fingerprint:
    source_id: str
    path: str
    sha256: str
    bytes: int
    mtime: float
    note: str = ""


def sha256_file(p: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


class SourceAdapter(ABC):
    """五源适配器基类。

    属性约定：
      name    —— 与 model.SOURCES 一致（manictime/screenmemo/iqqz3/tai/shion）
      device  —— **具体设备 id**（pc-main / phone-1 / phone-2 ...），不是 pc|phone
      root    —— 原始件所在目录（00-Inbox 下的原地路径，不复制）

    多设备（用户 2026-10-21 要求）：手机会几部并行使用、时间必然重叠，
    只有类别（pc/phone）无法区分是哪一台。设备归属按下面优先级解析：
      DL_DEVICE_<SOURCE> 环境变量  >  子类 device 默认值
    注册表在 digitallife/devices.py；未注册的 id 会直接报错并指路。
    """

    name: str = ""
    device: str = ""
    root: Path | None = None

    def __init__(self, root: Path | None = None, device: str | None = None) -> None:
        if root is not None:
            self.root = Path(root)
        # 显式参数 > 环境变量 > 类默认值；三者都经 devices.get() 校验
        from ...domain import devices as _dev
        raw = device or _dev.resolve_for_source(self.name, self.device)
        self.device = _dev.get(raw).device_id

    @abstractmethod
    def fingerprint(self) -> list[Fingerprint]:
        """登记原始件。重复导入时用于判断「源是否变化」。"""

    @abstractmethod
    def iter_events(self, report: SourceReport) -> Iterator[Event]:
        """逐条产出 Event，同时把丢弃/合并计数写进 report。"""

    def run(self) -> tuple[list[Fingerprint], list[Event], SourceReport]:
        """便捷入口：一次跑完指纹 + 事件。"""
        fps = self.fingerprint()
        report = SourceReport(source=self.name, device=self.device)
        events = list(self.iter_events(report))
        return fps, events, report
