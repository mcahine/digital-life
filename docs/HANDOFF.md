# 交接说明

> **[历史快照 2026-10-23]** 本文是当时那次收尾的交接记录，**不作为当前状态依据**。
> 文中指向的 `FINAL-2026-10-23.md` **从未产出**；**不补写**（补写等于发明历史），链接已改指 `postmortem/2026-10-23-delivery.md`。
> 当前状态请看：`04-Areas/数字人生/AGENTS.md` §B 现状快照（带采集日期）；
> 文档索引见 `docs/README.md`（本文已被标 🟡 降级为历史）。


> 2026-10-23。**视觉总结已 100% 完成**，收尾三步（建页/建日报/发布）已跑通，验收全绿。
> 最终数字见 `postmortem/2026-10-23-delivery.md`（**原文指向的 FINAL-2026-10-23.md 从未产出，链接已就地改指实际存在的文件**）；归因过程见 `postmortem/` 下四份复盘。

## 一、当前状态

| 项 | 值 |
|---|---|
| 视觉总结 | **9,679 条** ｜ pc-main 3,739 ｜ phone-2 5,940 |
| 待做 | **0**（两设备都是 0）|
| 三段齐全 | 9,678 / 9,679 |
| 空总结 | 1 片（`e8b1fd0ba281385a`，JSON 含非法控制字符 + 修复重试遇超时；已单独重跑中）|
| 日报 | 753 篇 |
| 时段页 | 753 个 html，211.9 MB |
| 片层 md | 9,928 |
| 冒烟 | **181 / 0** |
| git | 模块独立仓库，7 次提交，数据不入库 |

## 二、日常重跑（已全部并行化）

```bash
cd 01-Projects/digital-life
export PYTHONUTF8=1
unset DL_DB DL_WORK_DIR

python scripts/build_slice_html.py --all --workers 6   # 753 页约 6 分钟
python scripts/rebuild_daily.py --workers 4           # 753 天约 3.7 分钟
python -m digitallife.cli publish --no-zip
python scripts/smoke.py                               # 必须 181/0
python scripts/prune_results.py                       # 不合规必须 0
```

**顺序不可倒**：建页/建日报之后才能 `publish`（它只搬运不重算）。

## 三、视觉批量（若将来补新数据）

```bash
# 常规：32 并发 + 超时 240 + 60s 退避（净 5.7 片/分）
DL_LLM_TIMEOUT=240 python scripts/batch_vision.py --all --device pc \
    --reader model --workers 32 --rounds 14

# 重做「有图但一张没送到模型」的历史片（已限定 reader='model'，不误伤手机侧）
DL_LLM_TIMEOUT=240 python scripts/batch_vision.py --all --device pc \
    --reader model --workers 32 --rounds 10 --blind
```

额度看门狗默认开启（探针判定限流/额度耗尽，额度耗尽最多等 6.5 小时）。

## 四、五个别踩的坑

1. **不要用 `git add -A`**。753 个 html 与 753 篇日报在数据区，不在本仓库。
2. **不要照搬旧的并发结论**。「8 并发安全线」测于旧参数，已作废；现为 32 并发 + 60s 退避。
3. **不要按 429 的 `type` 字段判断**。它一律是 `quota_exceeded_error`，必须读 `message`：
   `rpm exhausted` 是限流（等 90s），无 rpm 字样才是额度耗尽（停下等刷新）。
4. **不要相信「payload too large」这个文案**。真因可能是畸形截图（1280×20 一类），
   已在前处理过滤（宽高比 >12 或短边 <64）。
5. **`batch_vision` 不删结构不合格的行**。有内容就入库，清理统一走 `prune_results.py`。

## 五、复盘索引

| 文档 | 主题 |
|---|---|
| [postmortem/2026-10-23-delivery.md](postmortem/2026-10-23-delivery.md) | 本轮最终数字与结论汇总（**原写作 FINAL-2026-10-23.md，该文件从未产出**）|
| `perf-regression-2026-10-23.md` | 60 倍性能退化：逐片查库（DL-9）|
| `vision-perf-and-blind-fix-2026-10-23.md` | 盲总结成因 + 各项提速实测 |
| `413-degenerate-image-2026-10-23.md` | 畸形图毒掉整片（三组对照闭合）|
| `quota-and-ratelimit-2026-10-23.md` | 额度为何读不到 + 探针看门狗 |

## 六、可选后续

- `categories` 字段 1,463 个不同值、77% 只出现一次，且**无人消费**（已 grep 确认
  `daily.py` 与 `slice_pages/` 都不读它）—— 是否收窄词表待定。
- 那 1 片空总结若重跑仍失败，可跑一次 `prune_results.py` 让它进入待清理清单。
