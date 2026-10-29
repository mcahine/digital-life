"""统一事件模型 —— 五源唯一的共同语汇。

event_id = sha1(device|source|kind|start|end|key)，因此重复导入天然幂等，
不需要「先查再插」的竞态逻辑。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from ..domain import devices as _devices

KINDS = ("app", "web", "screen", "power", "battery", "notify", "install")
SOURCES = ("manictime", "screenmemo", "iqqz3", "tai", "shion", "chat-archive")
# 设备不再是 pc/phone 两个硬编码值：多台手机/多台电脑各自有独立 id，
# 注册表在 devices.py（唯一真源）。这里保留元组形态以兼容既有调用。
DEVICES = tuple(d.device_id for d in _devices.all_devices())


@dataclass(slots=True)
class Event:
    device: str
    source: str
    kind: str
    start_utc_ms: int
    end_utc_ms: int | None = None
    app: str | None = None
    app_id: str | None = None
    title: str | None = None
    url: str | None = None
    path: str | None = None
    category: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # 接受旧别名（pc/phone）与已注册的新 id；未注册即报错并指路 devices.py
        self.device = _devices.get(self.device).device_id
        if self.source not in SOURCES:
            raise ValueError(f"unknown source: {self.source}")
        if self.kind not in KINDS:
            raise ValueError(f"unknown kind: {self.kind}")
        if self.end_utc_ms is not None and self.end_utc_ms < self.start_utc_ms:
            self.end_utc_ms = self.start_utc_ms

    @property
    def duration_s(self) -> int | None:
        if self.end_utc_ms is None:
            return None
        return max(0, (self.end_utc_ms - self.start_utc_ms) // 1000)

    def dedupe_key(self) -> str:
        """同源内部用于判重的稳定键。

        原则：key 必须包含「区分两条事件所必需的维度」，否则真实事件会被合并掉。
        payload 整体不参与（payload 变化不算新事件），但**有语义的 payload 字段要显式取出**。
        """
        if self.kind == "screen":
            return self.path or self.title or ""
        if self.kind == "web":
            return self.url or self.title or ""
        if self.kind == "battery":
            # 电量必须参与：实测 iqqz3 有 10,089 组「同秒同 Note 不同电量」的记录，
            # 不带 level 会被合并掉 11,075 行（真实数据丢失，非去重）。
            return "|".join([self.app or "", self.title or "",
                             str(self.payload.get("battery_pct", ""))])
        if self.kind == "notify":
            # 通知数必须参与：同一 App 同一天可能有多条不同计数（实测 244 组）。
            return "|".join([self.app or "", self.title or "",
                             str(self.payload.get("count", ""))])
        # app / power：标题参与，因为同一应用的不同窗口是不同行为
        return "|".join(x or "" for x in (self.app, self.title))

    @property
    def event_id(self) -> str:
        raw = "|".join([
            self.device, self.source, self.kind,
            str(self.start_utc_ms), str(self.end_utc_ms or ""),
            self.dedupe_key(),
        ])
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()

    def as_row(self) -> tuple:
        return (
            self.event_id, self.device, self.source, self.kind,
            self.start_utc_ms, self.end_utc_ms, self.duration_s,
            self.app, self.app_id, self.title, self.url, self.path, self.category,
            json.dumps(self.payload, ensure_ascii=False, sort_keys=True),
        )


EVENT_COLUMNS = (
    "event_id", "device", "source", "kind",
    "start_utc_ms", "end_utc_ms", "duration_s",
    "app", "app_id", "title", "url", "path", "category", "payload",
)
