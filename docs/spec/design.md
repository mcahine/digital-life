# digital-life 设计说明

> 采集口径：本文所有数字均为本轮现场采样（2026-10-21），非引述他人文档。

## 1. 为什么是「一张表 + 单一时间轴」

五个源的原始形态毫无共性：ManicTime 是双 SQLite + 5.5 万张 webp，ScreenMemo 是 43 个分片 SQLite，
IQQZ3 是 108 天 × 9 类 CSV，Tai 是 3 个 CSV，shion 是浏览器历史 SQLite。
但它们描述的是同一件事：**某个时刻我在某台设备上用某个应用做了某事**。

因此统一到一张 `event` 表，字段语义固定：

| 字段 | 语义 | 五源如何填 |
|---|---|---|
| `device` | 设备注册 id（`pc-main` / `phone-1`） | 源级常量；见 `digitallife/domain/devices.py` |
| `source` | 五个源之一 | 源级常量 |
| `kind` | app / web / screen / power / battery / notify / install | 见下表 |
| `start_utc_ms` | 起点（UTC 毫秒，唯一主轴） | ManicTime 用 UtcTime 列；ScreenMemo/shion 是 epoch ms；IQQZ3/Tai 是本地串 → `tz.local_to_utc_ms` |
| `end_utc_ms` | 终点；瞬时事件（截图/通知）为 None | |
| `app` / `app_id` | 显示名 / 包名·可执行名·域名 | C11 归一 |
| `title` / `url` / `path` | 窗口标题 · 网页 · 文件 | |
| `payload` | 源特有字段 JSON | 保可追溯，不参与判重 |

### 1.1 kind 的映射

| 源 | 原始 | kind |
|---|---|---|
| ManicTime | Ar_Activity ReportId=2 | `power`（title=Active/Away/Session lock） |
| ManicTime | ReportId=3 | `app`（app=Ar_CommonGroup.Name，title=窗口标题） |
| ManicTime | ReportId=4 | `web`（Name 以 http 开头）否则 `app`（文件路径） |
| ManicTime | Screenshots/*.webp | `screen` |
| ScreenMemo | shots_* | `screen`（payload 带 ocr_text 若有） |
| ScreenMemo | segments | `app` |
| IQQZ3 | Activity / Usage | `app` |
| IQQZ3 | Battery / Notification / Install | `battery` / `notify` / `install` |
| Tai | 时段 | `app` |
| Tai | 网页统计 | `web` |
| shion | history | `web` |

### 1.2 落盘分层（vault / `.data` 两分，2026-10-21 用户要求）

| 层 | 位置 | 内容 | 可否重建 |
|---|---|---|---|
| vault | `04-Areas/数字人生/` | `10-时段/<日期>.html`（一天一页）、`10-时段/片层/<日期>/*.md`、`20-日报/*.md`、`_index/manifest.json` | 可 |
| 中间物 | `知识库/.data/digital-life/` | `digital-life.sqlite`、`exports/*.ndjson`、`work/` | 可 |

理由：库 + NDJSON + 解包件合计约 1.2 GB，压在 vault 的 `_index/` 里会让 Obsidian 索引整个
`04-Areas`（我的数据区）时扫进可重建的中间物，也让「我的数据」与「派生物」混在一棵树上。
判定代码：`paths.INDEX_DIR`（vault 内）与 `paths.DATA_DIR`（`.data/`）。
`.data/` 由根 `.gitignore` 的**显式 `.data/` 规则**排除（2026-10-22 补写，已 `git check-ignore -v` 反证）。
用 `git check-ignore -v -- .data/digital-life` 复核——**不要以为 `*.data` 那条二进制 glob 在管它**：
`*.data` 的语义是「二进制模型文件」，它此前只是靠 `*` 能匹配空串侥幸命中（S54）。

## 2. 幂等的主键设计

`event_id = sha1(device|source|kind|start_utc_ms|end_utc_ms|dedupe_key)`，其中 `dedupe_key`：
- `screen` → path（回退 title）
- `web` → url（回退 title）
- `battery` → app|title|**`payload.battery_pct`**（2026-10-23 补：漏电量会把「同秒同标题不同电量」的记录合并掉，实测丢 11,075 行）
- `notify` → app|title|**`payload.count`**（同上：同一 App 同一天可能有多条不同计数，实测 244 组）
- `app`/`power` → app|title（**标题参与**：同一应用的不同窗口是不同行为）

> 实现真源：`digitallife/ingest/model.py` 的 `dedupe_key()`（分支数以此为准）。

因此重复导入天然幂等（`ON CONFLICT(event_id) DO UPDATE`），不需要"先查再插"的竞态逻辑。
副作用：`payload` 变化不会产生新行（payload 里只放可追溯信息，不放判定依据）。

## 3. 切片：为什么是 15 分钟 + 递归二分

实测 15 分钟窗口 2,558 个，每窗截图数：中位 18、p90 41、max 102。
ScreenMemo 自身单次请求实测带 13 张图（`segment_results.raw_request` 里 `images_attached=13`）。

- 取 15 分钟为基本单元：与人的"一段连续行为"大致同阶，且使单窗图片数落在可用区间。
- `MAX_IMAGES = 24`：留出余量给 3MB/张与 40MB/包的双重闸门。
- 超限递归二分（不是截断）：宁可多切一片，不丢任何一张截图（用户明确要求不做关键帧去重）。
- 父片也落库（`status="split"`），子片 `split_of` 指回父片 —— 保证"截图总数守恒"可断言。

## 4. 清洗规则 C1–C13（每条带实测依据）

| # | 规则 | 依据 |
|---|---|---|
| C1 | IQQZ3 只读解压目录，zip 仅用于 sha256 校验 | 108 天全量核验 972/972 文件逐字节相同，0 不同 |
| C2 | shion 只导新版（2025-03-11），旧版仅登记指纹 | 旧库 12,380 个 URL 全部被新库 27,424 个包含，旧库独有 0 |
| C3 | ManicTime 只读 Reports.db 的 Ar_Activity；Core.db 只取 Document 元数据 | Core 的 Cli_EcId_* 经 PRAGMA 实测只有 EntityId/CurrentChangeSequence/CurrentChangeType 三列，Cli_Ch_* 为空 —— 是变更日志不是正文；且 Core 561,519/219,173 行 vs Reports 544,950/206,146 行属同源冗余 |
| C4 | ManicTime 同名且间隔 ≤120s 合并为一段 | 33,374 原始段 → ≥30s 的 9,374 段（日均 134），不合并会把每秒焦点变化当独立行为 |
| C5 | 截图不做关键帧去重，全量 55,527 进切片 | 1 分钟/张已足够粗；15 分钟窗中位 18 张 |
| C6 | ScreenMemo 的 segment_samples.file_path 是目录不是文件 | 实测样本 `/data/.../2026-05/28`（无文件名），真实文件名在 `shots_*` 的 file_path 里 |
| C7 | ScreenMemo 只取 shots_* / segments / segment_samples / segment_results / daily_summaries / morning_insights；跳过 day_stats/totals/app_health_*(337,836 行)/ai_*/search_docs | 后者是运行统计与对话记录，不是使用记录 |
| C8 | Tai 只导「时段」表，「每日」表作交叉校验 | 时段 2,281 行（更细）vs 每日 755 行（按天聚合）；分类字段实测全为「未知」，不引入 |
| C9 | Tai 与 shion 的网页访问按 (url, 日期) 归并：时长取 Tai，标题/域名取 shion | URL 交集 5,977 / Tai 侧 11,017；同一天两侧都有数据 38 天 —— 这是唯一真实的跨源重合 |
| C10 | 时区统一存 UTC epoch ms，原始本地串留 payload | ManicTime 有 Local+Utc 双列（UTC+8）；ScreenMemo/shion 是 epoch ms；IQQZ3/Tai 无时区 |
| C11 | 列名归一 App name/应用/Name→app；Duration/时长/TotalSeconds→duration_s；网址/url/Documents.Name→url | 五源字段名互不相同 |
| C12 | 空事件不入库：duration ≤0 且无 title/url 的丢弃并计数 | 必须能解释「源 N 万行 → 入库 M 行」的差额 |
| C13 | power 事件必须入库并用于剔除离开时段 | 1,796 行；不剔除会把离开 8 小时算成使用时长 |

## 5. DB 骨架与视觉肌肉的分工

`dbview.build_day_structure` 只用 `event` 表产出：应用时长表、标题时间线、切换次数、专注连续段（同 app ≥15 分钟）、
离开/锁屏区间、当日截图清单、totals（active_s / away_s / locked_s 三者分开）。

`vision.enhance_slice` 只填 `overall_summary` / `key_actions[].detail` / `image_descriptions`，
并把 DB 侧时间线写进 `slice_result.app_timeline_json` 作为判定基线；
模型返回的 `timeline[]` 与基线不一致的条数计入 `conflict_count`（可观测指标，不是失败判据）。

**日层不再是拼装**（2026-10-22 用户裁决）：`daily._llm_narrative` 把当天**所有片层总结按时间序整篇**
喂给模型，由模型写成自然段叙事（`DAILY_PROMPT`：不用列表、不用时间轴、不省略、不编造）。
`render_narrative` 存在时优先于模板渲染。旧版「Python 拼时间轴罗列」已废。

片层结构不合格**不阻断入库**：`schema_check` 仍打回重做（`REWORK_ROUNDS=5`），仍不合格则照常
写库并记 `_meta.schema_ok=False` + `_meta.schema_check`；空内容必须重试出内容（上限 40 次，
逐步放大 `max_tokens`）。取舍理由：宁可留一份格式不完美的读图结果（可事后 prune），
不要为格式丢掉唯一一次送图机会。

### 5.1 网页渲染层：为什么网页不是「第三个数据层」

单日页（`10-时段/YYYY-MM-DD.html`，一天一个文件）**不持有任何数据**——它每次生成都从
`event / slice / slice_result` 现算。这一条决定了两件事：

- 删掉任意一天的 html，重跑 `build_slice_html.py` 即完全重建（与 `_index/` 同性质：派生物）。
- 页面口径变更（例如取消「代表页面」列、或把 9 页并成 1 页）不需要迁移任何数据，改渲染代码即可。

**分工**：日报（`daily_summary` + `20-日报/*.md`）是「读」的，只留突出部分；
网页是「查」的，全量。同一天两处数字必须能对上——它们来自同一个 `dbview` 结构与同一份
`slice_result`，差别只在聚合粒度。

#### 时间线为什么需要四层归并

同一段行为会被多条时间线各记一次，用户 2026-10-22 明确要求「一个时间戳就够」。
`digitallife/domain/timeline_merge.py` 按四层处理，**缺任一层都会漏重复**：

| 层 | 判据 | 落点 |
|---|---|---|
| 网页事件 | `url` 为 http/https | **仅当与窗口行起止时间完全相同**才挂：第一条当整行链接，同窗其余页进子行；对不上独立成行 |
| 路径孪生 | 标题为盘符路径 / `/home/` / `/Users/` / 非 http 协议引用 | 记 `path_twin`，不单独成行 |
| 同区间同义 | 同 `start/end` + 均无链接 + 一条标题含另一条 | 收进 `alt_of`，只留信息最全的一条 |
| 同页回声 | 页面身份同 + 区间重叠 ≥ 较短者 90% + 一侧无链接（或跨类型同标题） | 折成一行，保留有链接的一方 |

**父行判据只有「时间窗完全相同」一条**（2026-10-22 修正）。五天 2,560 个网页事件实测：
起止完全相同 1,282 ／ start 差 ≤2s 363 ／ 差 ≤60s 788 ／ 差 >60s 127——从「差 60 秒」起
就是不同行为。旧的「重叠最长」判据会把整点聚合的网页事件（tai 源 `raw_time` 全是
`HH:00:00`）统统挂到同一窗口行下，把不同事件伪装成一件事。

**同页回声用倒排索引实现**（2026-10-22 剪枝）：原先两两比对占单日 `build_daily` 的 97%
（14.57s/14.99s），索引化后 0.17s。语义契约由冒烟 T19a–T19h 常驻把守。

实测（2026-08-13 / pc-main）：原始 910 条 → **534 行，重复区间 0**，300 行带链接，26 行有子行。
归并结果同时被日报与四个页面消费——**它是单一实现，不允许任何消费方自己再写一份**。

## 6. 与日记系统的边界

| 层 | 产者 | 落点 |
|---|---|---|
| 日 | **本模块** | `04-Areas/数字人生/20-日报/` + 调 `PATCH /api/diaries/{date}` 写入 `DIGITAL-LIFE-BEGIN/END` 锚点段 |
| 周/月/季/年 | **日记系统**（second-brain `POST /api/periods/{layer}/generate`） | `04-Areas/GTD/50-回顾/*` |

机器总结在日记里**另立锚点**，不复用 `AI-SUMMARY-BEGIN/END`：那份模板注释明写「日层不生成总结，本段是 AI 聚合的输入锚点」，
复用它会把两种语义混成一份（S47）。

## 7. 失败模式与处置

| 情形 | 处置 |
|---|---|
| 源文件不存在 | 抛 `FileNotFoundError`，文案含期望路径与 `DL_*` 覆盖方式 |
| 缺 OCR 依赖 | 抛 `OcrUnavailable`，文案含 `pip install -e .[ocr]` |
| 模型网关未配置 | `is_configured()` 为 False → 抛 `LLMError`，文案指路「订阅管理 → 策略与设置 → 网关服务端」 |
| 单图超 3MB | 跳过该图并计数，不中断整片 |
| 整包超 40MB | 从尾部丢图 + 文本占位说明，不中断 |
| 视觉结果与 DB 冲突 | 以 DB 为准，`conflict_count` 记录，不报错 |
| 某日无截图但有大段活动 | 正常出片（`image_count=0`），日报只写 "仅活动" |

## 8. 验收分层

- 结构层（S45）：行数与源真值对拍；增长型数据用下限或独立扫描对拍，不写死精确值。
- 幂等层：同源两跑行数不变；publish 两跑 sha256 一致。
- 规则层（S20）：关掉 C1 或 C2 → 对应断言必须变红；还原复绿。
- 端到端层（S21）：日报里的截图引用直接消费接口返回值，不在测试里重建路径。
- 边界层（CP-5）：空日、单 app 连续 2 小时、离开 8 小时、pc+phone 同日并存。