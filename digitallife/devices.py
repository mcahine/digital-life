"""设备注册表 —— 多设备（多台手机/多台电脑）的唯一真源。

为什么需要它（用户 2026-10-21 提出）：
1. 手机会同时用几部，时间上必然重叠；只有 device="phone" 无法区分是哪一部，
   重叠时段的记录会混在一起，也无法说清「哪段时间在哪台设备上」。
2. 电脑现在只有一台，但以后会有第二台；现在不标注，将来就要重导入全部历史。
3. 原始文件里没有可靠的设备标识（实测：ManicTime 截图路径只有分辨率，
   IQQZ3 CSV 无设备列），所以设备归属必须由导入时的配置声明，不能靠猜。

约定：
- device_id 是稳定标识，写进 event.device，一旦定下不要再改（它参与 event_id 计算，
  改了等于把所有历史记录判成新记录）。
- kind 是设备类别（pc/phone），用于「同类设备并行记账」这类统计口径；
  多台手机同属 phone，但 device_id 各自独立。
- 用 DL_DEVICE_<SOURCE>=<device_id> 覆盖单次导入的归属（同一份数据换机重灌时用）。
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass

PC = "pc"
PHONE = "phone"
DEVICE_KINDS = (PC, PHONE)


@dataclass(frozen=True, slots=True)
class Device:
    """一台设备。device_id 形如 pc-main / phone-1：<类别>-<短名>。"""

    device_id: str
    kind: str                 # pc | phone
    label: str                # 类别型名字（主力电脑 / 手机 1），日报里成句用
    note: str = ""
    name: str = ""            # ★ 真实设备名（D-001 / iQQQZ3），网页里展示用

    def __post_init__(self) -> None:
        if self.kind not in DEVICE_KINDS:
            raise ValueError("unknown device kind: %r（只允许 %s）" % (self.kind, (DEVICE_KINDS,)))
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{1,31}", self.device_id):
            raise ValueError(
                "非法 device_id: %r（只允许小写字母数字与 -_，2-32 位）" % (self.device_id,))


# ---- 注册表：新增设备只改这里 -------------------------------------------
# 当前只有一台电脑、一部手机；将来加设备 = 这里加一行，历史数据不受影响。
# name 是**机器自己的名字**（用户 2026-10-22 指定：电脑 = D-001，手机 = iQQQZ3），
# 网页与界面上一律显示它；label 只用于「主力电脑」这类需要成句的场合。
DEVICES: tuple[Device, ...] = (
    Device("pc-main", PC, "主力电脑", "当前唯一在用的 Windows 机器", name="D-001"),
    Device("phone-1", PHONE, "手机 1", "只装 IQQZ3（App 使用记录）", name="iQQQZ3"),
    Device("phone-2", PHONE, "手机 2", "只装 ScreenMemo（截图 + AI 画面描述）", name="iQQQ Neo11"),
)

# 旧数据用的是粗粒度值；读旧库时映射到默认设备，避免历史记录失联。
LEGACY_ALIASES: dict[str, str] = {
    "pc": "pc-main",
    "phone": "phone-1",
}

_BY_ID = {d.device_id: d for d in DEVICES}


def name_of(device_id: str) -> str:
    """真实设备名（D-001 / iQQQZ3）；未登记或没写 name 时退回 device_id。"""
    try:
        dev = get(device_id)
    except ValueError:
        return device_id
    return dev.name or device_id


def all_devices() -> tuple[Device, ...]:
    return DEVICES


def get(device_id: str) -> Device:
    """按 id 取设备；接受旧别名（pc/phone）。"""
    key = LEGACY_ALIASES.get(device_id, device_id)
    dev = _BY_ID.get(key)
    if dev is None:
        raise ValueError(
            "未注册的设备 %r。请在 digitallife/devices.py 的 DEVICES 里加一行，"
            "或用环境变量 DL_DEVICE_<SOURCE> 指定。已注册：%s"
            % (device_id, [d.device_id for d in DEVICES]))
    return dev


def query_aliases(device_id: str) -> list[str]:
    """查询时该用哪些 device 值。

    历史库里存的是旧粗粒度值（pc/phone），新导入的是注册 id（pc-main/phone-1）。
    按注册 id 查询时必须同时列出它的旧别名，否则会查不到历史数据
    （实测：改 id 后日报直接变成「当日无数据」）。
    """
    dev = get(device_id)
    out = [dev.device_id]
    for old, new in LEGACY_ALIASES.items():
        if new == dev.device_id and old not in out:
            out.append(old)
    return out


def sql_device_filter(device_id: str, column: str = "device") -> tuple[str, list[str]]:
    """返回可直接拼进 SQL 的设备过滤片段与参数。

    库内同一台设备可能以旧值（pc/phone）或注册 id（pc-main/phone-1）存在；
    任何按设备过滤的地方都必须走这里，否则改造后历史数据整类查不到。
    实测受害点：日报时段数变 0、事件数变 0、时间线为空、视觉识别选不到片。
    """
    aliases = query_aliases(device_id)
    return column + " IN (" + ",".join(["?"] * len(aliases)) + ")", aliases


def kind_of(device_id: str) -> str:
    return get(device_id).kind


def label_of(device_id: str) -> str:
    return get(device_id).label


def by_kind(kind: str) -> tuple[Device, ...]:
    return tuple(d for d in DEVICES if d.kind == kind)


def resolve_for_source(source: str, default_id: str) -> str:
    """导入时决定这条数据属于哪台设备。

    优先级：DL_DEVICE_<SOURCE> 环境变量 > 适配器默认值。
    用途：同一份原始数据换机重灌、或从另一台同型号手机导出时，指定归属而不改代码。
    """
    env_key = "DL_DEVICE_" + source.upper().replace("-", "_")
    raw = (os.environ.get(env_key) or "").strip()
    return get(raw or default_id).device_id