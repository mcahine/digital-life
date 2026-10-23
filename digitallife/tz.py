"""时间归一：一律存 UTC epoch 毫秒；原始本地串留在 payload 里可追溯。

本机时区实测 Asia/Shanghai（ManicTime 的 Local 与 Utc 列相差 8 小时）。
不回退到系统 tz —— Windows 上 zoneinfo 需要 tzdata，固定偏移更稳且可断言。
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta

LOCAL_TZ = timezone(timedelta(hours=8), "Asia/Shanghai")

MANICTIME_FMT = "%Y-%m-%d %H:%M:%S"


def local_to_utc_ms(dt_local: datetime) -> int:
    """本地 naive datetime → UTC epoch ms。"""
    if dt_local.tzinfo is None:
        dt_local = dt_local.replace(tzinfo=LOCAL_TZ)
    return int(dt_local.timestamp() * 1000)


def utc_ms_to_local(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, LOCAL_TZ)


def local_day(ms: int) -> str:
    """UTC ms → 本地日期 YYYY-MM-DD（日记/日报的分组口径）。"""
    return utc_ms_to_local(ms).strftime("%Y-%m-%d")


def parse_local(s: str) -> datetime:
    """ManicTime 的 'YYYY-MM-DD HH:MM:SS' 本地串。"""
    return datetime.strptime(s.strip(), MANICTIME_FMT)


def parse_flexible(s: str) -> datetime:
    """容忍多种本地串：2025/1/4 15:56:23 / 1/4/2025 15:56:23 / ISO。"""
    s = s.strip()
    if not s:
        raise ValueError("empty datetime")
    for fmt in (MANICTIME_FMT, "%Y/%m/%d %H:%M:%S", "%m/%d/%Y %H:%M:%S",
                "%m/%d/%Y %H:%M", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return datetime.fromisoformat(s)


def parse_hms_to_seconds(s: str) -> int:
    """'11:49:40' / '0:00:10' → 秒。IQQZ3 的 Duration 与 Usage time 是这个形态。"""
    s = (s or "").strip()
    if not s:
        return 0
    parts = s.split(":")
    try:
        nums = [int(p) for p in parts]
    except ValueError:
        return 0
    if len(nums) == 3:
        return nums[0] * 3600 + nums[1] * 60 + nums[2]
    if len(nums) == 2:
        return nums[0] * 60 + nums[1]
    if len(nums) == 1:
        return nums[0]
    return 0
