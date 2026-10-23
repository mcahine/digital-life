# digital-life 任务分解与状态（2026-10-22 更新）

| 批次 | 内容 | 状态 |
|---|---|---|
| B0 | 工程骨架 + `docs/spec` 五件套 + 接口契约冻结 | 完成 |
| B1-core | paths / model / tz / schema / store / clean / sources.base | 完成 |
| B1-flows | 五源适配器（manictime/screenmemo/iqqz3/tai/shion） | 完成（1,123,058 条）|
| B2 | dbview + slicer | 完成（86,193 片，截图零丢失）|
| B3 | llm / ocr / prompts / vision / schema_check | 完成（已跑真实模型）|
| B4 | daily + publish | 完成（753 篇；**日层已改为模型叙事**）|
| B5 | 日记系统改造（POST/PATCH /api/diaries + 周期生成 + /api/dl/*）| 完成（4 套测试 27/20/14/11 全绿）|
| B6 | 视觉识别实跑 | **进行中**（并发长跑，3,6xx 片待处理）|
| B7 | 前端 `/dl/*` 页面 + 今日页数字人生卡片 | 未开始 |
| B8 | 设备注册表（`pc-main`/`phone-1` + 旧值别名兼容） | 完成（冒烟 T16/T18 共 14 项）|
| B9 | 日报叙事化（模型二次总结替代 Python 拼接） | 完成（`DAILY_PROMPT` + `render_narrative`）|
| B10 | 时段网页（黑白双主题，od-design clean） | 完成（旧结构 751 个单日页，2026-10-22 被 B12 取代并删除）|
| B11 | 中间物迁出 vault 到 `.data/` | 完成（`paths.DATA_DIR`）|
| B12 | 时段网页包（每日 9 页：index + 应用×2 + 网站×2 + 时间线×2 + 视觉×2）| **已被 B15 取代**（2026-10-22 同日推翻）|
| B13 | **时间线四层归并**（网页 / 路径孪生 / 同区间同义 / 同页回声）| 完成（910 → 534 行，重复区间 0）|
| B14 | 日报瘦身（应用/网站改摘要 + 指路全量；时间线整行链接）| 完成（443 → 272 行）|
| B15 | **单日单页**（`10-时段/<日期>.html`，四 section + 设备 tab，视觉照搬 `00-Inbox/index.html`）| 完成（753 天全量 203.7 MB；片层 md 迁 `10-时段/片层/`；死模块 8 个已删）|

## 交付实测（2026-10-22 现场查询）

| 指标 | 值 |
|---|---|
| 入统一事件表 | **1,123,058** 条（manictime 771,802 / iqqz3 203,461 / screenmemo 103,424 / shion 28,805 / tai 15,566）|
| 按设备 | `pc-main` 816,173 ｜ `phone-1` 306,885 |
| 幂等 | 全量重跑两次总数不变 |
| 时间片 | 86,193 片（phone-1 53,064 / pc-main 33,129）|
| 截图守恒 | phone-1 103,424 / pc-main 55,527，与源完全一致 |
| 日报 | 753 天 |
| 时段网页 | **753** 个 html（一天一页 = 753 个文件，203.7 MB；中位 248 KB）|
| 时段 md | 只写有视觉结果的片，随视觉长跑增长 |
| 视觉结果 | `slice_result` 124 片且持续增长（长跑队列 3,667 片，3600s 超时后 0 失败率显著下降）|
| 冒烟 | **120 项全绿**（含 14 条缺陷回归哨兵 T19a–T19j + T20a–T20j，均注入验红）|
| 留存包 | NDJSON 530 MB / 1,123,058 行 + manifest（513 源指纹 + 文件哈希）|
| 事件跨度 | 2024-09-28 → 2026-10-21（本地时区）|

## 已修的 18 个真实缺陷

**解析与清洗**
1. `model.dedupe_key` 漏电量 → 会丢 11,075 行电池记录；
2. `clean.min_duration_s=30` 默认 → 丢 15.6% 真实时长；
3. `merge_adjacent` 单一流 → 合并失效（687 vs 46,665）；
4. 手机屏幕态在 IQQZ3 里是 `kind=app` → 锁屏算成使用（73%）；
5. 源数据 3.7 天假锁屏 → 用截图证伪；
6. `iqqz3` 多快照 `by_kind[k]=f` 覆盖 → 丢 Install 244 / Notification 99 行（改 list 收集后 rows_read 384,741→440,686）；

**查询与存储**
7. `slicer` 按全跨度枚举窗口 → 20 小时（改按天整取切窗，2.4 分钟）；
8. 区间谓词 `end IS NULL` 单列 → 每天捞全量（2.69s→0.10s）；
9. `IN (?)` 超 999 → 片数上万必崩（两处各分批 500）；
10. 跨窗口事件展示未裁剪 → 同段行为在相邻窗口重复；
11. **WAL 模式 SQLite 用 `cp` 复制 → 目标库损坏**（改用 `backup()` API）；

**展示与接口**
12. `shion` 28,805 条 100% 无 end → 整类被 dbview 丢弃（补 `POINT_EVENT_MS`）；
13. 日报缺 `import re` → 被 `cli daily` 吞掉，表现为「改了没生效」；
14. `publish` 用库内旧 `output_text` 不重新生成 → 必须先 `daily` 再 `publish`；

**设备改造（B8）**
15. 设备 id 改造后 `label = "电脑" if dev=="pc" else "手机"` 恒假 → 全标手机（8 项冒烟红）；
16. **9 处 `device=?` 精确匹配**（daily×4 / slicer×3 / vision×1 / publish×2）→ 时段 0 片、事件 0、视觉选不到片（全改别名 IN）；
17. `vision.pending_slice_ids` 漏 `from . import devices` → `NameError`（视觉选片第一步就崩）；

**工程卫生**
18. 残留后台进程用旧代码写 vault → 膨胀到 6,528 篇空壳（按 PID 杀 + 清理）。

## 环境变量（覆盖用，收尾必须 unset）

| 变量 | 作用 |
|---|---|
| `DL_DB` | 覆盖索引库路径（测试隔离用；同一持久 shell 里会传染，见缺陷 11 的排查过程）|
| `DL_WORK_DIR` | 覆盖解包目录（同理会传染）|
| `DL_PUBLISH_ALL_SLICES=1` | 让 publish 写出全部 86,193 片（默认只写有视觉结果的）|
| `DL_LLM_BASE_URL` / `DL_LLM_API_KEY` / `DL_LLM_MODEL` / `DL_LLM_TIMEOUT` | 视觉通道；回退 `second-brain/config/llm.json` |
| `DL_DEVICE_<SOURCE>` | 导入时指定设备注册 id（如 `DL_DEVICE_SCREENMEMO=phone-2`）|
| `PYTHONUTF8=1` | Windows GBK 控制台必需（S33）|

## 待决

1. **视觉识别长跑未收尾**：`batch_vision.py --all --workers 5 --rounds 8`，PC 侧 3,6xx 片待处理；
2. **753 天日报尚未用新叙事逻辑全量重跑**（`rebuild_daily.py` 约 2 分钟，等片层结果齐再跑）；
3. **时段网页未用新格式重生成**（`build_slice_html.py --all`）；
4. 前端 `/dl/*` 页面未做；
5. 周/月/季/年触发方式（当前人点按钮）；
6. 手机 103,424 张截图在本机不存在（安卓路径），无法送模型——除非用户提供图片文件。
