# phone-2 视觉总结 —— 最终交付报告模板

> 由 tmp/monitor.py 在批量完成时自动填充关键数字，人工只需补「结论」段。
> 用法：把 {{...}} 替换为 tmp/vision_done_report.txt 里的实测值。

## 一句话结论

{{DONE}}/{{TOTAL}} 片全部完成，schema 不合规 {{BAD}} 条、空总结 {{EMPTY}} 条，
三段齐全率 {{THREE_PCT}}%，覆盖 {{DAYS}} 天（2026-05-28 → 2026-08-07）。

## 数据来源

| 项 | 值 |
|---|---|
| 原始件 | `00-Inbox/screen_memo_database_backup_2026-08-07T22-07-00-855864.zip`（699.8 MB）|
| 设备 | `phone-2` = **iQQQ Neo11**（与 `phone-1`=iQQQZ3 是两台不同手机）|
| 观察来源 | ScreenMemo 自带的 `ai_image_meta.description`（中文画面描述），**不读图** |
| 事件 | 105,197 条 = 103,424 张截图 + 1,773 段合成 app 事件 |

## 关键口径（三个容易搞错的）

1. **覆盖率是 21.3%，不是 99%**
   `ai_image_meta` 表内有效描述率 99%（分母 26,742），但截图有 103,424 张。
   按片算 92% 的片至少有一条描述，788 片靠 DB 时间线。
2. **并发安全线 8，不是 12**
   上游限的是 rpm。12 并发时 429 累计 164 次、批次直接压死；8 并发 40 条 0 FAIL。
3. **app 事件是合成的**
   `segments.app_packages` 666 条全空，改用截图自带的 app 名合成（1,773 段）。

## 修掉的 4 个真 bug

| bug | 后果 | 修法 |
|---|---|---|
| `payload` 已是 dict 却 `json.loads` | 描述全丢，整片退化成「无观察来源」 | 兼容 dict/str 两形态 |
| panel id `dev.split("-")[0]` | phone-1/phone-2 撞同一 id，**页面少一台设备** | 用完整 device_id |
| 设备遍历写死 `(PC_DEVICE, PHONE_DEVICE)` | phone-2 整天被漏 | 遍历 `all_devices()` |
| `max_tokens` 放大无上限 | 65536→262,144 → 400 → 整片 FAIL | `MAX_TOKENS_CAP` + 到顶同预算重发 |

## 产出

```
10-时段/<日期>.html         753 个单日页（视觉 section 每天多出 iQQQ Neo11 的卡片）
10-时段/片层/<日期>/*.md     片层 md
20-日报/<日期>.md            「## iQQQ Neo11 使用」节
```

## 验收

- 冒烟 `python scripts/smoke.py` → **129 / 0**
- 新增断言：T20k（panel 唯一）、T21a–T21e（两台手机独立 + payload 双形态）、T22a–T22c（max_tokens 上限）
- 全部按 S20 注入验红过

## 未完成项（如实列出）

- **压缩**：`/compact` 是宿主斜杠命令，agent 无对应工具，需用户手动执行。
