# 交接说明

> 2026-10-23。手机侧已 100% 完成；PC 侧视觉批量在跑，带额度看门狗。
> 本文件只写「现在什么状态 + 下一步做什么」，实测数字与归因见同目录另外三份复盘。

## 一、当前状态

| 项 | 值 |
|---|---|
| 视觉总结（手机侧） | **5,940 / 5,940（100%）**，不合规 0、空总结 0、三段齐全 100% |
| 视觉总结（PC 侧） | 3,735 片含图；已完成约 2,700，**剩余约 1,000**（在跑）|
| ⚠️ PC 侧历史结果 | 1,879 条里 **1,874 条是"盲总结"**（一张图都没送到模型）；已修，需用 `--blind` 重做 |
| 冒烟 | **170 / 0** |
| 时段页 | 753 个 html 已全量重建（208.4 MB）|
| 日报 | 753 篇（本轮重跑后需再验）|

## 二、跑着的任务

```bash
cd 01-Projects/digital-life
export PYTHONUTF8=1
DL_LLM_TIMEOUT=240 python scripts/batch_vision.py --all --device pc \
    --reader model --workers 32 --rounds 14 --pause-min 10 --max-wait-h 6.5
```

日志：`tmp/v_pc_wd.log`。**额度看门狗已启用**：探针失败且判定为额度耗尽时，
每 10 分钟探一次、最多等 6.5 小时；恢复即继续，超时则干净收工（已完成的都已入库）。

## 三、跑完之后依次做

```bash
# 1) 重做那 1,874 条盲总结（判据：_meta.images_sent=0 且 images_total>0）
DL_LLM_TIMEOUT=240 python scripts/batch_vision.py --all --device pc \
    --reader model --workers 32 --rounds 14 --blind

# 2) 重生成日报（--workers 4，753 天约 9 分钟）
python scripts/rebuild_daily.py --workers 4

# 3) 重生成时段页（--workers 6，753 页约 6.4 分钟）
python scripts/build_slice_html.py --all --workers 6

# 4) 发布 + 复核
python -m digitallife.cli publish --no-zip
python scripts/smoke.py            # 必须 170/0
python scripts/prune_results.py     # 不合规必须 0
```

**顺序不可倒**：`rebuild_daily` 必须在视觉批量之后（否则日报是旧快照），
`publish` 必须在 `rebuild_daily` 之后（它只搬运不重算）。

## 四、三份复盘（数字与归因都在里面）

| 文档 | 讲了什么 |
|---|---|
| `perf-regression-2026-10-23.md` | 60 倍性能退化：逐片查库 → 单日 103s，753 页 5 小时 |
| `vision-perf-and-blind-fix-2026-10-23.md` | 盲总结的成因与修复；视觉链路各项提速的实测数字 |
| `quota-and-ratelimit-2026-10-23.md` | 额度为什么读不到（三项实测证据）；限流 vs 额度耗尽的判别 |

## 五、别踩的三个坑

1. **不要用 `git add -A`**。本模块是独立仓库，`git status` 里只应有代码与文档；
   753 个 html 与 753 篇日报都在数据区，不在本仓库（用户裁决：代码进 git，数据不进）。
2. **不要照搬旧的并发结论**。2026-10-22 记的"8 并发安全线"测于旧参数
   （24 张原图 + max_tokens=65536），已作废；现在 40 并发 + 超时 240 是实测最优。
3. **不要按 `type` 字段判断 429**。429 的 `type` 一律是 `quota_exceeded_error`，
   必须读 `message`：`rpm exhausted` 是限流（等 90s），无 rpm 字样才是额度耗尽（停下等刷新）。
