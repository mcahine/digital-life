"""路径锚定（__file__ 锚，与 cwd 无关）。

代码在 01-Projects/digital-life/，数据在 04-Areas/数字人生/（v3 代码/数据分离）。
索引类产物一律落 vault 的 _index/ 下，并被根 .gitignore 整体排除。
"""
from __future__ import annotations

import os
from pathlib import Path

PACKAGE = Path(__file__).resolve().parent          # digitallife/
PROJECT = PACKAGE.parent                            # 01-Projects/digital-life
WORKSPACE = PROJECT.parents[1]                      # 知识库 根

AREAS = WORKSPACE / "04-Areas"
INBOX = WORKSPACE / "00-Inbox"
VAULT = AREAS / "数字人生"
# vault 里只留给人看的东西（md + manifest）；库/NDJSON/解包中间件是**中间物**，
# 移出 vault 到工作区级 .data/：Obsidian 不会索引、git 已整体排除（用户 2026-10-21 要求）。
INDEX_DIR = VAULT / "_index"          # manifest.json 仍在 vault 内（发布清单，人要看）
DATA_DIR = WORKSPACE / ".data" / "digital-life"   # 库、NDJSON、work 落这里

DAILY_DIR = VAULT / "20-日报"
SLICE_DIR = VAULT / "10-时段"          # 一天一页 HTML：10-时段/YYYY-MM-DD.html
LAYERED_DIR = SLICE_DIR / "片层"       # 片层 md：10-时段/片层/YYYY-MM-DD/HHMM-HHMM.md

GTD_VAULT = AREAS / "GTD"
DIARY_DIR = GTD_VAULT / "40-执行" / "日记"
REVIEW_DIR = GTD_VAULT / "50-回顾"


def work_dir() -> Path:
    """解包中间件目录；DL_WORK_DIR 可覆盖（测试/异地跑用）。"""
    override = os.environ.get("DL_WORK_DIR")
    return Path(override) if override else DATA_DIR / "work"


def db_path() -> Path:
    """索引库位置；DL_DB 可覆盖。"""
    override = os.environ.get("DL_DB")
    return Path(override) if override else DATA_DIR / "digital-life.sqlite"


def manifest_path() -> Path:
    return INDEX_DIR / "manifest.json"


def clean_report_path() -> Path:
    return INDEX_DIR / "clean_report.json"


def exports_dir() -> Path:
    return DATA_DIR / "exports"


def ensure_dirs() -> None:
    for d in (VAULT, INDEX_DIR, DATA_DIR, work_dir()):
        d.mkdir(parents=True, exist_ok=True)
