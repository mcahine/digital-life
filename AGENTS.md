# AGENTS.md —— digital-life 工程说明书

> 读者：任何进入本目录改代码的 AI 代理。
> 本文件**只写"怎么改才不闯祸"**，不重复设计细节。
> 设计真源：`docs/spec/design.md`｜接口契约：`docs/spec/interfaces.md`｜任务状态：`docs/spec/tasks.md`
> 数据视角说明书：`../../04-Areas/数字人生/AGENTS.md`（与本文件互补，不重复）。
> 通用卡点（CP-1…CP-6）与防错标准（S1–S59）见工作区根 `AGENTS.md` —— **只引用，不复制**。

---

## §A 这个模块是什么

把**电脑/手机的原始使用痕迹**（ManicTime 数据库、截图、App 使用记录）统一成
**单一时间轴上的结构化事件**，再分两层加工成总结：

```
截图 + DB 事实  →  [片层] 视觉模型写完整总结（每 15 分钟一片）  →  slice_result.overall_summary
                       ↓ 当天所有片总结整篇喂给模型
                  [日层] 模型二次总结，写成自然段叙事          →  daily_summary.output_text
```

| 项 | 值 |
|---|---|
| 代码 | `01-Projects/digital-life/`（**数据一律不落此处**）|
| 给人看的数据 | `04-Areas/数字人生/`（`10-时段/`、`20-日报/`、`_index/manifest.json`）|
| 大体量中间物 | `知识库/.data/digital-life/`（库 / NDJSON / 解包件，1.21 GB，git 由 `.data/` 规则排除）|
| 入口 | `python -m digitallife.cli <子命令>` |
| 依赖 | Python 3.11+；`httpx`；本地 OCR 为可选 extra `[ocr]` |

三条不可动摇的主轴（改代码前先确认没有违背）：

1. **时间是线性主轴**。`device` 只是事件的一个字段，**不是分区**。跨设备必须能按同一 UTC 时间轴对齐。
2. **DB 是骨架，截图只补画面内容**。结构字段（应用名、起止时间）一律以数据库为准；
   视觉模型与 DB 冲突时以 DB 为准，冲突数记进 `conflict_count`（可观测，不是错误）。
3. **本模块只产日总结**。周/月/季/年由日记系统自己滚，**这里不许产出**。

**第四条（2026-10-22 用户裁决，最容易违背）**：**Python 只搬运原料，不做后处理拼接**。
片层总结由模型写全量（不截断、不加省略号）；日层由模型对片层做二次总结。
**禁止用模板把时间轴罗列成日报**——那是被用户明确否掉的旧做法。

---

## §B 目录与职责

```
digitallife/
  paths.py        INDEX_DIR(vault) / DATA_DIR(.data) / DB 路径；DL_DB / DL_WORK_DIR 可覆盖
  devices.py      设备注册表（唯一真源）：Device / DEVICES / LEGACY_ALIASES /
                  all_devices get kind_of label_of by_kind resolve_for_source
                  query_aliases sql_device_filter
  model.py        Event 数据类 + dedupe_key()（幂等主键的来源）
  tz.py           LOCAL_TZ=UTC+8；local_to_utc_ms / parse_flexible / parse_hms_to_seconds
  schema.py       建表 DDL（meta/source_file/event/capture_text/slice/slice_result/daily_summary + FTS5）
  store.py        open_db / upsert_events / record_source_file / events_between / search_events ...
  clean.py        is_empty_event / normalize_event / merge_adjacent / build_report
  sources/        五个源适配器（manictime / screenmemo / iqqz3 / tai / shion）+ ADAPTERS 注册表
  dbview.py       build_day_structure：把 DB 事实整理成"当日结构"（应用时长、离开、电源）
  slicer.py       build_slices：按 15 分钟 UTC 网格切片，超限递归二分
  llm.py          ChatClient：直连上游 / dsh-bridge 网关两种模式
  prompts/        segment.py：screenmemo-v1 提示词（字段与硬约束的真源）
  schema_check.py 结构化校验器 R1–R9：返回 Verdict(ok, issues)，供打回重做用
  vision.py       enhance_slice：截图 → 结构化 → 写 slice_result
  ocr.py          本地 OCR（可选）
  timeline_merge.py  四层 timeline 归并（网页/路径孪生/同区间同义/同页回声）→ 一个时间戳一行
  daily.py        build_daily：日层叙事（DAILY_PROMPT + render_narrative）+ 突出部分摘要
  publish.py      publish_all：产出 md / NDJSON / manifest / zip
  cli.py          子命令：ingest / dbview / slice / vision / ocr / daily / publish / verify
scripts/
  smoke.py            主回归套件（**143 项**，含构造性探查与注入验红哨兵）
  batch_vision.py     并发批量视觉识别（--all/--days/--workers/--rounds；写后复核删不合格）
  prune_results.py    按 screenmemo-v1 约定清理不合规 slice_result（默认只报告）
  rebuild_daily.py    批量重生成日报（753 天约 6 分钟；逐片查库时曾退化到 50+ 分钟）
  build_slice_html.py 生成 10-时段/YYYY-MM-DD.html 单日页（一天一页，四 section）
  slice_pages/        单日页的 section 模块（一天一页）
    base.py           设计 token + page/esc/link/seg/more_button/card/fmt_dur
    contract.py       Ctx / load_slices / day_range_ms / device_label
    apps.py           应用 section（全量，前 12 个之后折叠）
    web.py            网站 section（按域名 + <details> 逐次访问，无「代表页面」）
    timeline.py       时间线 section（全量，merge 后整行链接）
    vision.py         视觉 section（逐片卡片，全量不折叠）
tmp/                  运行产物（.gitignore 已排除）——**中间文件放这里，不要放系统临时目录**
```

---

## §C 动手前必读的九条

| # | 规则 | 为什么（都是实测踩出来的）|
|---|---|---|
| **DL-1** | **禁止用 `cp` 复制正在用的 SQLite 库**。要复制必须用 `sqlite3.Connection.backup()` | 库开 WAL；`cp` 只拷主文件，WAL 里未检查点的数据没合并，目标库 `integrity_check` 直接报 `database disk image is malformed`（本轮实测毁过一次 617MB 库）|
| **DL-2** | 幂等主键改动必须同时改 `model.dedupe_key` **和**冒烟里的幂等断言 | 曾漏掉电量字段，同秒同标题不同电量的记录被合并，静默丢 11,075 行 |
| **DL-3** | `merge_adjacent` 必须按 `(device, kind)` **分流**，不能对单一混合流合并 | 混流会被交错类型切断，合并次数 687 vs 应有 46,665（差 67 倍）|
| **DL-4** | `min_duration_s` 默认必须为 **0**，不得过滤短事件 | 曾默认 30 秒，丢掉 23,998 段 = **15.6% 真实使用时长** |
| **DL-5** | 区间查询必须写 `end > lo OR (end IS NULL AND start >= lo)` | 写成单列 `end IS NULL` 会把全表捞进来（单天 218,356 行，2.69s vs 0.10s）|
| **DL-6** | `IN (?,…)` 必须分批（≤500） | SQLite 变量上限 999，片数上万必抛 `too many SQL variables` |
| **DL-7** | 按 `device` 过滤**不得写 `device = ?`**，必须走 `devices.sql_device_filter()`（生成 `IN (?,…)` 并把旧别名一并列入） | 设备 id 从 `pc`/`phone` 改成 `pc-main`/`phone-1` 后，9 处精确匹配（daily×4 / slicer×3 / vision×1 / publish×2）全部静默返回空：时段 0 片、事件 0 条、视觉选不到片。库里历史行仍是旧值，靠别名 IN 才对得上 |
| **DL-8** | 点事件（`end_utc_ms IS NULL` 的 app/web）消费时必须补时长 `POINT_EVENT_MS = 60_000` | shion 28,805 条 100% 无 end，`ce > cs` 恒假，整类被 dbview 丢掉——表现为"明明有 web 记录却没展示" |
| **DL-9** | **循环里不得出现 SQL 调用**。需要「每片/每行一个派生值」时，先一次性批量查出原料，再在内存里比较 | 给「无画面描述」加标注时逐片查 event 表，EXPLAIN 显示走 `ix_event_kind` 而非时间索引（`kind='screen'` 命中 10 万行），单日 `load_slices` 从 1.6s 退化到 **103s**，753 页重建从 40 分钟变 5 小时。改为「一次取当日有描述的时间点 + 内存比较」后回到 1.6s。详见 `docs/perf-regression-2026-10-23.md` |

---

## §D 视觉通道（最容易出错的一段）

### D.1 参数定论（来自官方文档 + 多变量实测）

| 参数 | 定论 |
|---|---|
| 端点 | `https://token.sensenova.cn/v1/chat/completions`（`base_url` **必须带全路径**，只写 `/v1` 会 404）|
| 模型 | `sensenova-6.8-flash-lite` |
| `thinking` | 官方文档：**必填 object，默认 `{enabled:true}`**。必须显式传 `{"enabled": false}` |
| 图片 | **只能** `{"type":"image_url","image_url":{"url":"data:image/webp;base64,…"}}`；传 `image_base64` 字段形式会被拒 400 |
| `max_tokens` | 上游支持到 **65536**（实测 200）。`MAX_TOKENS` 常量即 65536，**不要调小** |

**关于 `thinking.enabled` 的真相**（两轮子代理都判错过，这里写死）：

- 它不是硬开关，**推理量仍会随机暴涨**（同一请求两次分别 565 / 1131 个推理 token，偶发飙到 16384）。
- 但实测 `{enabled:false}` 下 **20 次请求 0 次空内容**；真正会让 `content` 为空的是 **`max_tokens` 太小**：
  `max_tokens=512` 时 5 次里空 4 次。**归因要归到预算，不是归到参数失灵。**

### D.2 兜底策略（三层，缺一不可）

1. **空内容重试**：`vision.py` 里 `while` 循环最多 **40 次**，每轮逐步放大 `max_tokens`
   ——**必须有内容产出**，不允许放弃成空。
2. **结构打回重做**：`schema_check` 判不合格 → 把**具体违规理由**回灌模型重做，最多 `REWORK_ROUNDS = 5` 轮。
   仍不合格 **照常写库**，只记 `_meta.schema_ok=False` + `_meta.schema_check`（用户裁决：
   有内容就入库，格式问题可事后 prune，不要为了格式丢掉唯一的一次读图结果）。
3. **并发**：模型侧可并发，但**有上限**。实测（2026-10-22，sensenova-6.8-flash-lite）：

   | workers | 单条耗时 | 失败率 | 吞吐 |
   |---|---|---|---|
   | 1 | 21 图以上频繁超时 | 高 | 不可用 |
   | 6 | 28–35s | 0 / 89 | 7.7–9.7 片/分 |
   | **8** | 25–30s | **0 / 40** | **11.0 片/分** ← 当前采用 |
   | 12 | 80–95s | 10 / 12 | 被 `rpm exhausted` 压死 |

   上游限的是 **rpm（每分钟请求数）**，不是并发连接数。判断能否加并发要看 429 的**频次**：
   8 并发时 40 条 0 FAIL、429 极少 → 还没触顶；12 并发时 429 累计 164 次、批次直接退出。
   瓶颈实为**单条耗时**（中位 34s），6→8 只快 20%，再加收益递减。

**批量实跑特征**（别当成代码 bug 去"修"）：`ReadTimeout`（19–21 图大片）、`429 rps exhausted`（并发限流）。
两者都靠 `--rounds` 多轮重试消化。

### D.3 screenmemo-v1 格式约定（唯一真源）

`prompts/segment.py` 的 `PROMPT_VERSION` + `OUTPUT_FIELDS`，8 个字段缺一不可：

```
apps, categories, timeline, key_actions,
described_images, image_tags, image_descriptions, overall_summary
```

`overall_summary` 必须是三节、顺序固定、每节 ≥3 条要点：

```
## 关键操作
## 主要活动
## 重点内容
```

校验规则在 `schema_check.py`（R1–R9）。**改字段或改小节，必须同时改这两处 + 冒烟 T13。**

### D.4 时间线合并：一个时间戳就够（用户 2026-10-22 反复强调）

同一次上网会被两条记录各记一遍，必须合并成**一行 + 整行做链接**：

```text
X 19:30:10-19:32:42  Microsoft Edge · 罗辑得了MVP…和另外 3 个页面 - 个人
X 19:30:18-19:32:42  www.bilibili.com · <同一个 bilibili 链接>      ← 重复时间戳，禁止
V 19:30:10-19:32:42  [Microsoft Edge · 罗辑得了MVP…和另外 3 个页面 - 个人](https://www.bilibili.com/video/BV1FFABeuEnb/)
```

实现真源：`digitallife/timeline_merge.py`。四层归并，缺一层就会漏重复：

| 层 | 判据 | 落点 |
|---|---|---|
| 网页事件 | `url` 以 http/https 开头 | **仅当与窗口行起止时间完全相同**才挂：第一条当 `link_url`（整行链接），同窗其余页进 `sub`；对不上一律独立成行 |
| 路径孪生 | 标题是盘符路径 / `/home/` / `/Users/` | 记 `path_twin`，**不单独成行**（ManicTime 的文档时间线影子） |
| 同区间同义 | 同 `start/end` + 都无链接 + 一条标题包含另一条 | 收进 `alt_of`，只留信息最全的一条 |
| 同页回声 | 页面身份同 + 区间重叠 ≥ 较短者 90% + 一侧无链接（或跨类型同标题） | 折成一行，保留有链接的一方 |

**父行判据只有一个：时间窗完全相同。** 五天 2,560 个网页事件实测：
起止完全相同 1,282 ｜ start 差 ≤2s 363 ｜ 差 ≤60s 788 ｜ 差 >60s 127 —— 从「差 60 秒」起
就是不同行为。曾按「重叠最长」配对，把 20:59:11 的 Obsidian 窗口塞进 12 个豆包会话
（tai 源按整点聚合、起点全是 `21:00:00`），用户原话：「都不是一个事件的为什么要搞一个
次级列表，搞得好像它们是一回事似的」。**宁可时间线长几行，也不许把不同事件说成一件事。**

实测（2026-08-13 / pc-main）：910 条原始 → **534 行，重复区间 0**，300 行带链接，26 行有子行。
**改这个文件等于同时改日报、时段网页、以及任何消费 timeline 的地方——先跑冒烟再跑两个样本日。**

性能（2026-10-22 用户批准剪枝）：`_drop_page_echoes` 原先每行与全部已保留行两两比对，
单日 `build_daily` 里占 **97%** 耗时（2025-02-17：14.57s/14.99s，`identity()` 被调
668,038 次）。改为按「身份 + 标题」建倒排索引后单日降到 **0.17s**。
剪枝的语义契约由冒烟 **T19a–T19j** 常驻把守（含 90%/89%/60% 重叠边界、跨类型标题对齐、
候选插入顺序、槽位代次失效、3000 行不退化反向哨兵、锚文本 www 变体去重）——**改这个函数必须先让 T19 全绿，
并按 S20 逐个注入缺陷确认它能变红**。

### D.5 单日页（全量明细的唯一归属）

日报只留突出部分，**全量在这里**。生成：

```bash
python scripts/build_slice_html.py --date 2026-08-13   # 单日
python scripts/build_slice_html.py --all               # 全部有日报的日期
python scripts/build_slice_html.py --all --prune        # 顺带删旧日期目录与 _html/
```

产出 **`10-时段/YYYY-MM-DD.html` 一个文件**：header（日期+星期+规模）+ 四个 section
（时间线 / 应用 / 网站 / 视觉），每个 section 内按设备用 tab 切换，右下 fab 目录浮钮跳锚点。
片层 md 不在这个文件里，落 `10-时段/片层/YYYY-MM-DD/HHMM-HHMM.md`。

| 文件 | 职责 |
|---|---|
| `scripts/slice_pages/base.py` | 设计 token + `page/esc/link/seg/more_button/card/fmt_dur` |
| `scripts/slice_pages/contract.py` | `Ctx` 数据结构 + `load_slices` + `device_label` |
| `scripts/build_slice_html.py` | 编排：装 Ctx、逐 section 渲染、设备 tab、目录锚点 |
| `slice_pages/{apps,web,timeline,vision}.py` | 各 section 的 `panel(...)` → HTML 片段 |

section 模块契约（新增 section 照此写）：模块暴露 `ANCHOR` / `LABEL`，
以及 `panel(pid, ctx, hidden=...) -> (html, 行数)`（vision 是跨设备 section，签名略有不同）。
**渲染顺序与目录锚点由编排器统一控制**；页面外壳（header/footer/浮钮/主题）只在 `base.py`。

**设备 tab 的取舍**：某个 section 里设备 0 行时**不生成这个 tab**（实测「手机 0」
点开只有一句「没有网页访问记录」，纯噪音）。只有 1 个设备有内容时整条 tab 控件不出现。

### D.6 截图可达性（一个已确认的边界）

- **PC 侧（ManicTime zip 内）可读**：`event.path` 形如 `Screenshots/2026-08-13/xxx.webp`，
  由 `_read_image` 从 zip 直读。
- **手机侧（ScreenMemo）不可读，但不需要读**：`path` 是 `/data/user/0/com.fqyw.screen_memo/...`
  这种**安卓设备路径**，本机不存在。

  **替代路径（2026-10-22 定稿）**：ScreenMemo 自己已经给截图写过中文画面描述
  （主库 `ai_image_meta.description`，26,742 条），适配器把它读进 `event.payload["description"]`，
  视觉用 `enhance_slice(..., reader="text")` 消费 —— **全程不碰图片**，
  也不让截图进 vault（用户要求：area 不能臃肿，有文字就够）。

  ⚠️ 覆盖率不是 100%：描述只覆盖 **21.3%** 的截图（26,742 / 103,424）。
  按片算 92% 的片至少有一条描述，788 片全靠 DB 时间线（会标「本片无画面描述」）。
  **不要把 ai_image_meta 的「表内有效描述率 99%」误读成「全量截图覆盖 99%」**——
  这两个分母不同，是本轮最容易搞错的一点。

### D.7 日报写法（日层，2026-10-22 定稿）

`daily.DAILY_PROMPT` 把当天**所有片层的完整总结**按时间序整篇喂给模型，要求：

- 用**自然段叙事**，不用列表、不用时间轴罗列；
- **不省略、不用省略号**（用户点名的问题：旧版输出里出现省略号）；
- 不编造、不写周期回顾、篇幅随内容长短。

`render_narrative` 存在时优先于模板渲染。**改日报先改提示词，不要在 Python 里拼句子。**

---

## §E 数据源与设备

### E.1 设备（`devices.py` 是唯一真源）

`device_id` 形如 `<类别>-<短名>`（`pc-main` / `phone-1`），类别 ∈ {`pc`, `phone`}。
旧值 `pc`/`phone` 经 `LEGACY_ALIASES` 只读兼容，**新写入不得再用裸值**。
设备参与 `event_id` 计算，**定下不要改**。

三个名字字段，别混用：

| 字段 | 例 | 用在哪 |
|---|---|---|
| `device_id` | `pc-main` | 库、`event_id`、SQL 过滤 |
| `name` | `D-001` / `iQQQZ3` / `iQQQ Neo11` | **网页与界面上一律显示它**（用户 2026-10-22 指定）|
| `label` | `主力电脑` / `手机 1` / `手机 2` | 日报这类需要成句的地方 |

**当前三台设备**（`iQQQ Neo11` 与 `iQQQZ3` 是**两台不同的手机**，不是同一台的别名）：

| device_id | name | 装了什么 |
|---|---|---|
| `pc-main` | D-001 | ManicTime + 浏览器历史 |
| `phone-1` | iQQQZ3 | IQQZ3（App 使用记录）|
| `phone-2` | iQQQ Neo11 | ScreenMemo（截图 + AI 画面描述）|

**加设备时必须同时改三处**，漏一处就静默失效：
`devices.py` 的 `DEVICES`、`sources/` 对应适配器的 `device`、以及
`scripts/build_slice_html.py` 里的设备遍历（**不得写死 `(PC_DEVICE, PHONE_DEVICE)`**，
panel id 也**不得用 `dev.split("-")[0]`** —— 那会把 phone-1/phone-2 截成同一个 id，
两个 panel 撞号后页面上直接少一台设备）。

`contract.device_label()` 返回的是 `name`（`D-001` / `iQQQZ3`），
**不要**再退回「电脑 / 手机」——用户明确否掉了那个写法。

### E.2 源

| 源 | 设备 | 形态 | 注意 |
|---|---|---|---|
| `manictime` | pc-main | 单个 zip，内含 `Ar_Activity` 等表 + 截图 | 结构字段的权威来源 |
| `screenmemo` | **phone-2**（iQQQ Neo11） | 主库 `ai_image_meta` 有 26,742 条**中文画面描述**；分片库 43 个含 `shots` + `ocr_text` | 截图路径是安卓路径、本机无文件；视觉走 `reader="text"` 用描述，**不送图**（见 D.6）|
| `iqqz3` | phone-1（iQQQZ3） | 108 顶层目录 + `daily/`（287 条）| **多快照**：一天 2–3 个快照必须全读；**假锁屏**：06-12→06-16 连续 3.7 天 locked，区间内有 965 张截图，需截图证伪 |
| `tai` | pc-main | 历史浏览器记录 | 与 `shion` 有 6,214 条交集，**跨源归并后恰好 33,967** |
| `shion` | pc-main | 历史浏览器记录 | 100% 无 end，见 DL-8 |

新增源 = 在 `sources/` 加适配器 + 在 `ADAPTERS` 注册 + 在 `model.SOURCES` 加名 + 补冒烟断言
+ 若引入新设备则在 `devices.py` 加一行。**不要改 `event` 表结构去容纳源特有字段**，
特有信息进 `payload` JSON。详细格式约定见 `docs/spec/data-sources.md`。

---

## §F 常用命令

```bash
cd 01-Projects/digital-life
export PYTHONUTF8=1                # Windows GBK 控制台会 UnicodeEncodeError（S33）
unset DL_DB DL_WORK_DIR            # 这两个变量在同一个 shell 里会传染，收尾必须清

python -m digitallife.cli ingest   --source manictime --path <zip>   # 入库
python -m digitallife.cli dbview   --date 2026-08-13                # 当日结构
python -m digitallife.cli slice    --device pc-main                 # 15 分钟切片（可加 --date）
python -m digitallife.cli vision   --device pc-main --limit 10      # 视觉识别
python -m digitallife.cli daily    --date 2026-08-13                # 日叙事
python -m digitallife.cli publish  --no-zip                         # 产出 md + NDJSON + manifest
python -m digitallife.cli verify                                    # 各源计数与跨度

python scripts/smoke.py                                     # 143 项主回归
python scripts/batch_vision.py --all --workers 5 --rounds 8 # 批量视觉（长跑）
python scripts/rebuild_daily.py                             # 批量重生成日报
python scripts/build_slice_html.py --all                    # 重生成时段网页
python scripts/prune_results.py                             # 只报告；加 --apply 真删
```

### 视觉识别需要的环境变量

```bash
export DL_LLM_BASE_URL="https://token.sensenova.cn/v1/chat/completions"   # 必须带全路径
export DL_LLM_API_KEY="<key>"
export DL_LLM_MODEL="sensenova-6.8-flash-lite"
export DL_LLM_TIMEOUT=900            # 大图片片很慢，180 秒不够
export DL_WORK_DIR="<ManicTime 解包目录>"
```

---

## §G 改完必须自己验的十件事

1. `python scripts/smoke.py` → **必须 143/0**（不是"大概绿了"）。
2. 幂等：同一批数据跑两遍，`count_events` 不变。
3. 截图守恒：叶子片 `SUM(image_count)` == 源 `kind='screen'` 事件数（逐设备对齐）。
4. 结构合规：`python scripts/prune_results.py` → 不合规必须 0（注意：这只是报告口径，
   不再代表"入库被阻断"）。
5. 注入验红（S20）：新增断言后，**故意注入缺陷必须变红**，还原后复绿。只跑绿不算通过。
6. 发布后回显：`20-日报/` 真有文件、`manifest.json` 的 counts 与库内一致。
7. 日报抽样：新日报里省略号计数为 0、无 `HH:MM` 行首时间列、确有自然段；且**没有**
   `### 应用明细（全量` 与 `代表页面`（全量表已迁出到时段网页）。
8. 网页包：`python scripts/build_slice_html.py --date <某双设备日>` → **`10-时段/<日期>.html` 存在**
   且 > 50 KB、含四个 `<section>`、无 `尚未实现`；双设备日必须真有 `role="tablist"` 与 hidden panel。
9. 时间线不重复：`merge_browser_web(dbview 的 timeline)` 输出里
   **同一 (start, end) 只能出现一次**；用两个样本日（一个纯 PC、一个 PC+Phone）各验一次。
10. **改了 `daily.py` 必须重跑产出**：`rebuild_daily.py` 再 `cli publish`。
    改代码 ≠ 改数据——库里 `output_text` 和 `20-日报/*.md` 都是早先跑出来的快照，
    `publish` 只搬运不重算。**验收必须抽查 md 文件本身，不能只看代码或库表。**

---

## §H 已知边界（不要当成 bug 去"修"）

| 现象 | 真相 |
|---|---|
| `slice.status` 永远是 `pending`/`split` | 全库无人回写；幂等判据是 `NOT EXISTS(slice_result)`，别用 status 选片 |
| `10-时段/` 有 753 个 html、`片层/` 下只有 900 多篇 md | 分工如此：**一天一页 html**（753 天 = 753 个文件）；md 是片层，只有出了视觉总结的片才写（`DL_PUBLISH_ALL_SLICES=1` 可全量），所以远少于 86,193 片 |
| 单页里某设备整段不见 | 该设备当日**该 section 0 行**，tab 就不生成（不是渲染失败）。真的两台都没数据时整页只剩一句「这一天没有任何设备使用记录」 |
| `10-时段/_html/` 与日期目录都不见了 | 2026-10-22 两轮升级：先删 `_html/`，再把「日期目录 + 9 页」并成 `<日期>.html`。`--prune` 可重复执行 |
| `slice_result` 里有 `schema_ok=False` | 设计如此：格式不阻断入库（见 D.2）。历史行 `_meta.schema_ok` 可能是 `null` |
| 日报指路链接曾是死链（2026-10-23 修）| 2026-10-22 把「日期目录 + 9 页」并成「一天一个 `<日期>.html`」后，`daily.py` 的链接模板仍写 `10-时段/<日期>/apps-phone.html` —— **752 篇日报全部带坏链接**。已改为 `10-时段/<日期>.html#apps` / `#sites`。教训：**目录结构升级必须全文检索旧路径形态**（S50），只改产出代码不改消费代码就会留下系统性死链 |
| 手机 103,424 张截图本机没有文件 | 见 D.6。视觉走 `reader="text"` 用 ScreenMemo 自带的画面描述，**不需要图片** |
| `phone-2` 的 `kind='app'` 事件是合成的 | `segments.app_packages` 实测 **666 条全空**，一段都产不出。改用截图自带的 app 名合成（同一 app、相邻间隔 ≤120s 连成一段），共 1,773 段。没有它 `dbview` 的 apps/timeline 永远是空数组 |
| 库/NDJSON 不在 vault | 已移 `.data/digital-life/`；`_index/` 只留 `manifest.json`。别再用 `_index/digital-life.sqlite` 这个旧路径 |
| `_index/clean_report.json` 不存在 | 只在跑 `cli ingest` 时产出；清洗账在 `source_file`（513 条） |
| `00-Inbox/IQQZ3 Appusage` 计数会变 | 可能被外部进程持续解压，计数是快照 |
| 残留旧代码进程会写 vault | 曾把 vault 膨胀到 6,528 篇空壳。收尾必须按 PID/端口确认无残留（S49） |
| 无模型时重跑日报，内容变模板 | **已有叙事会被复用**（`build_daily` 读 `structured_json.narrative`），不会因缺模型而丢掉已写好的正文 |
| 753 天里只有 1 天是自然段叙事 | 历史只有 2026-08-13 跑过模型（9,461 字）；其余 752 天是模板渲染。**这是数据现状，不是 bug**——要变成叙事需逐天跑模型 |

---

## §I 与工作区其他部分的接口

- **注册中心**：`01-Projects/second-brain/config/pipelines.json` 有 `digital-life` 一行（唯一登记处）。
- **只读查询面**：`second-brain` 的 `apps/api/routers/digital_life.py` 暴露 `/api/dl/days|slices|stats`，
  只读 vault（`20-日报`/`10-时段`/`_index/manifest.json`），**不碰 `.data/`**。
- **日记系统消费**：`PATCH /api/diaries/{date}` 把日总结写进
  `04-Areas/GTD/40-执行/日记/*.md` 的 `<!-- DIGITAL-LIFE-BEGIN/END -->` 锚点区。
  **该锚点独立于 `AI-SUMMARY`，不要复用**。
- **数据区说明书**：`04-Areas/数字人生/AGENTS.md`（面向数据侧，与本文件互补）。
