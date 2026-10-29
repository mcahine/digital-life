# AGENTS.md —— digital-life 工程说明书

> **读者**：任何进入本目录改代码的 AI 代理。
> **边界**：本文件只写「工程怎么做才不闯祸」。数据现状、口径、契约归 `../../04-Areas/数字人生/AGENTS.md`；
> 设计意图归 `docs/spec/design.md`；接口签名归 `docs/spec/interfaces.md`；任务状态归 `docs/spec/tasks.md`；
> 文档索引见 `docs/README.md`。
> **接手本模块**：先读 `docs/HANDOFF-2026-10-25.md`（交接文档，唯一入口：现状真值 + SDCA 七步 + T1–T5 任务切分）。
> **通用卡点（CP-1…CP-6）与防错标准（S1–S59）**：见工作区根 `AGENTS.md` —— **只引用，不复制**。

---

## 本文件的维护约定（改之前先读这一节）

1. **单一真相**：本文件只定义「工程规则」（DL-N 铁律、目录职责、验证清单）。
   下列事实**不在本文件重复定义**，只给指路：
   | 事实 | 唯一出处 |
   |---|---|
   | 冒烟项数 | §E 命令的真跑输出（`python scripts/smoke.py` 末行）；正文不写死数字以外的副本 |
   | 日报结构契约 | `04-Areas/数字人生/AGENTS.md` §「日报结构契约」 |
   | 设备清单（三台设备的 name/label） | `digitallife/domain/devices.py` 的 `DEVICES` |
   | 数据规模（事件数/片数/篇数） | `04-Areas/数字人生/AGENTS.md` §「现状快照」 |
2. **改代码时必须同步改本文哪一节**：

   | 你改了什么 | 必须同步改 |
   |---|---|
   | 新增/删除 `digitallife/` 模块 | §B 目录表 |
   | 新增/改 `scripts/` 脚本 | §B 目录表 + §E 命令 |
   | 改了日报渲染（`daily.py`） | `04-Areas/数字人生/AGENTS.md` §「日报结构契约」+ §G 已知边界 |
   | 改了 `devices.py` 的 `DEVICES` | §D.1 设备层 + `scripts/build_slice_html.py` 的设备遍历 |
   | 改了提示词/schema 字段 | §D.4 加工层 + `docs/spec/interfaces.md` |
   | 修完一个「会静默出错」的坑 | §C 新增一条 DL-N（Problem→Standard→Checkpoint 三段） |
   | 新增冒烟断言 | §E 的冒烟项数（以真跑输出为准，不手填） |

3. **删除即声明**：任何被废弃的规则/模块，不得静默删除，必须在本节留一行
   「已于 YYYY-MM 废弃，原因 X，替代物 Y」。当前已废弃清单见 §G。
4. **可验证性**：本文件里每个数字、路径、行为断言都必须能被执行验证；
   不能验证的（设计取舍、用户裁决意图）必须显式标「设计意图，非实测」。

---

## §A 这个模块是什么

把**电脑/手机的原始使用痕迹**（ManicTime 双 SQLite + 截图、ScreenMemo 分片 SQLite、
IQQZ3 日目录 CSV、Tai CSV、shion 浏览器历史）统一成**单一 UTC 时间轴上的结构化事件**，
再分两层加工成总结：

```
源文件 → [事实层] event 表 → [片层] 15min 窗 + 视觉模型写全量总结 → slice_result
                                    ↓ 当天所有片总结整篇喂给模型
                             [日层] 模型二次总结，写成自然段叙事 → daily_summary
                                    ↓ 只搬运不重算
                              [呈现] 20-日报/*.md（读）+ 10-时段/*.html（查）
```

| 项 | 值 |
|---|---|
| 代码 | `01-Projects/digital-life/`（**数据一律不落此处**）|
| 给人看的数据 | `04-Areas/数字人生/`（`10-时段/`、`20-日报/`、`_index/manifest.json`）|
| 大体量中间物 | `知识库/.data/digital-life/`（库 / NDJSON / 解包件；由 `paths.DATA_DIR` 判定）|
| 入口 | `python -m digitallife.cli <子命令>` |
| 依赖 | Python 3.11+；`httpx`；本地 OCR 为可选 extra `[ocr]` |

### 三条不可动摇的主轴（改代码前先确认没有违背）

1. **时间是线性主轴**。`device` 只是事件的一个字段，**不是分区**。跨设备必须能按同一 UTC 时间轴对齐。
2. **DB 是骨架，截图只补画面内容**。结构字段（应用名、起止时间）一律以数据库为准；
   视觉模型与 DB 冲突时以 DB 为准，冲突数记进 `conflict_count`（可观测，不是错误）。
3. **本模块只产日总结**。周/月/季/年由日记系统自己滚，**这里不许产出**。

### 第四条（用户裁决，最容易违背）

**Python 只搬运原料，不做后处理拼接**。片层总结由模型写全量（不截断、不加省略号）；
日层由模型对片层做二次总结。**禁止用模板把时间轴罗列成日报**——那是被用户明确否掉的旧做法。

> 设计意图，非实测：这条是 2026-10-22 用户裁决，落地证据见 `daily.render_daily` 已降级为
> `render_narrative` 的别名（§G 废弃清单第 3 条）。

---

## §B 目录与职责

```
digitallife/
  # ---- N6 重构（2026-10-25）：按**工作管线**分组，不再平铺 ----
  ingest/         ① 采集 -> 入库：sources/ + model store schema clean tz
  domain/         ② 领域：slicer dbview devices timeline_merge
  daily/          ③ 加工（日叙事）：structure render render_body prompt build
  produce/        ③ 加工（视觉/模型）：vision llm schema_check ocr prompts/
  emit/           ④ 发布：publish（md / NDJSON / manifest / zip）
  render/         ④b 呈现：base contract linktitle apps web timeline vision hourbar
  cli.py paths.py textutil.py   入口 + 跨层工具
  _assets/day.css  753 页共用的样式表（N1：原 14 KB <style> 内联了 753 遍）

  # 旧路径全部兼容（__init__.py 里注册 sys.modules 别名）：
  #   digitallife.tz / .model / .store / .schema / .clean            -> ingest.*
  #   digitallife.slicer / .dbview / .devices / .timeline_merge     -> domain.*
  #   digitallife.vision / .llm / .schema_check / .ocr / .prompts   -> produce.*
  #   digitallife.publish                                            -> emit.publish
  #   slice_pages / slice_pages.*                                    -> render / render.*
  # 搬迁历史见 git（16 + 6 + 9 + ... 个 rename 记录），不再保留旧文本作对照。

  【N6 前布局，保留作历史对照】
  paths.py        INDEX_DIR(vault) / DATA_DIR(.data) / VAULT / DAILY_DIR / SLICE_DIR / LAYERED_DIR；
                  work_dir() db_path() manifest_path() clean_report_path() exports_dir()；DL_DB / DL_WORK_DIR 可覆盖
  devices.py      ★ 设备注册表（唯一真源）：DEVICES / LEGACY_ALIASES / Device(device_id,kind,label,note,name)
                  all_devices get name_of kind_of label_of by_kind resolve_for_source query_aliases sql_device_filter
  model.py        Event 数据类 + dedupe_key()（幂等主键来源）+ event_id；KINDS / SOURCES / EVENT_COLUMNS
  tz.py           LOCAL_TZ=UTC+8；local_to_utc_ms / utc_ms_to_local / local_day / parse_flexible / parse_hms_to_seconds
  schema.py       建表 DDL + init_db（先 _migrate 补列再 DDL，见 DL-16 意图）
  store.py        connect / open_db / upsert_events / record_source_file / count_events / count_kind /
                  time_span / events_between / upsert_capture_text / search_events / rebuild_fts
  clean.py        is_empty_event / normalize_event / merge_adjacent / build_report；MIN_DURATION_S_DEFAULT=0
  sources/        五个源适配器（manictime / screenmemo / iqqz3 / tai / shion）+ ADAPTERS 注册表
  dbview.py       build_day_structure：DB 事实 → 当日结构（应用时长、离开、电源）；POINT_EVENT_MS=60_000
  slicer.py       build_slices：WINDOW_MIN=15 / MAX_IMAGES=24，超限递归二分
  llm.py          ChatClient（直连上游 / dsh-bridge 网关两种模式）；MAX_TOKENS_CAP=65536
  prompts/        segment.py：screenmemo-v1 提示词（字段与硬约束的真源）
  schema_check.py 结构化校验器 R1–R9：Verdict(ok, issues)
  vision.py       enhance_slice；MAX_TOKENS=8192 起 / MAX_TOKENS_CEILING=65536 / MAX_SEND_IMAGES=8 /
                  REWORK_ROUNDS=5 / _is_degenerate；slice_has_description() 是**公开 API**（T3，见 DL-24）
tests/                pytest 测试网（T1/T2 新建）：conftest.py（临时库 fixture）+ 5 个测试模块
  ocr.py          本地 OCR（可选 extra）
  textutil.py     ★ 跨层共享小工具：parse_json（T3 收敛点，见 DL-23）。**只收真正重复的**
  timeline_merge.py  四层 timeline 归并 → 一个时间戳一行（单一实现，见 DL-15）；
                  clean_title() 是**公开 API**（T3 升格，见 DL-22），_clean_title 仅兼容别名
  daily.py        build_daily + render_narrative（日层叙事，唯一渲染路径）
  publish.py      publish_all：md / NDJSON / manifest / zip
  cli.py          子命令：ingest / dbview / slice / vision / ocr / daily / publish / verify
scripts/
  smoke.py            主回归套件（项数以真跑输出为准，见 §E）
  batch_vision.py     并发批量视觉识别；写后复核**只上报不删行**；--blind 重做盲总结（DL-10）
  prune_results.py    按 screenmemo-v1 约定清理不合规 slice_result（默认只报告；唯一清理入口）
  rebuild_daily.py    批量重生成日报
  build_slice_html.py 生成 10-时段/YYYY-MM-DD.html 单日页（一天一页）
  fetch_titles.py     网页标题抓取（**唯一的联网入口**；渲染期绝不联网）
  finish_phone2.py    phone-2 一键收尾（五步，自带闸门）
  rewrite_style.py    [反面教材] 曾用于把片层改写成叙述体，保留作警示，勿用
  slice_pages/
    base.py           设计 token + page/esc/link/seg/more_button/card/fmt_dur
    contract.py       Ctx / load_slices / day_range_ms / device_label
    linktitle.py      ★ 链接标题单一真相函数 resolve()（时间线与网站两处同序）
    apps.py           应用 section（全量，前 12 个之后折叠）
    web.py            网站 section（按域名 + <details> 逐次访问）
    timeline.py       时间线 section（全量，merge 后整行链接）
    vision.py         视觉 section（逐片卡片，全量不折叠）
tmp/                  运行产物（.gitignore 已排除）——中间文件放这里，不要放系统临时目录；**不得删除他人文件**（S41）
```

> `docs/architecture/` 归另一条产出线（架构文档），**本文件不重复其内容**；
> 需要分层视图时见 `docs/architecture/`（子代理3 产出中）。

---

## §C 动手前必读的铁律（DL-N）

> 每条三段式：**Problem**（触发条件）→ **Standard**（必须怎么做）→ **Checkpoint**（在哪一步检查）。
> 与根 `AGENTS.md` 的 S 表同格式；DL 编号**在本模块内唯一且不复用**。
> 排序按 MECE 分组：存储幂等（DL-1…4）｜查询（DL-5…7、DL-9）｜事件语义（DL-8）｜
> 跨层（DL-10）｜上游通道（DL-11…13）｜产出与呈现（DL-14、DL-15）。

### 存储与幂等

| # | Problem（触发条件） | Standard（必须怎么做） | Checkpoint（在哪检查） |
|---|---|---|---|
| **DL-1** | 用 `cp` 复制正在用的 SQLite 库 | 库开 WAL，**禁止 `cp`**；要复制必须用 `sqlite3.Connection.backup()` | 任何备份/迁移库的操作后跑 `PRAGMA integrity_check`，必须 `ok`。实测误用 `cp` 毁过一次 617MB 库 |
| **DL-2** | 改了幂等主键的构成 | 必须**同时**改 `model.dedupe_key` / `model.event_id` **和**冒烟里的幂等断言 | 改完跑冒烟幂等项 + 同一源连跑两次 `count_events` 不变。曾漏掉电量字段，静默丢 11,075 行 |
| **DL-3** | 对单一混合流做相邻合并 | `clean.merge_adjacent` 必须按 `(device, kind)` **分流**后再合并 | 合并次数对拍：混流时 687 vs 应有 46,665（差 67 倍） |
| **DL-4** | 想「过滤掉太短的事件」 | `MIN_DURATION_S_DEFAULT` 必须为 **0**；需要粗粒度时由**调用方显式传** `min_duration_s` | 任何改动 `clean.py` 默认值后，跑一次全量入库并核对总时长。曾默认 30 秒，丢 23,998 段 = 15.6% 真实时长 |

### 查询

| # | Problem（触发条件） | Standard（必须怎么做） | Checkpoint（在哪检查） |
|---|---|---|---|
| **DL-5** | 写区间查询谓词 | 必须写 `end > lo OR (end IS NULL AND start >= lo)`；点事件不得被时间窗漏掉 | 用单日查询计时对拍。写成单列 `end IS NULL` 会把全表捞进来（单天 218,356 行，2.69s vs 0.10s） |
| **DL-6** | 拼 `IN (?,…)` 参数列表 | 必须分批（**≤500**） | 片数上万时跑一次，不得出现 `too many SQL variables`（SQLite 上限 999） |
| **DL-7** | 按 `device` 过滤 | **禁止 `device = ?`**；必须走 `devices.sql_device_filter()`（生成 `IN (?,…)` 并含旧别名） | 任何改设备相关查询后，构造「只有旧别名值」的行验证能查到。设备 id 从 `pc`/`phone` 改成 `pc-main`/`phone-1` 时，9 处精确匹配（daily×4 / slicer×3 / vision×1 / publish×2）全部静默返回空 |
| **DL-9** | 需要「每片/每行一个派生值」 | **循环里不得出现 SQL**。先一次性批量查原料，再在内存里比较 | 改完 `load_slices` / 任何板块渲染后，用单日重建计时对拍：正常 ~1.6s；曾退化到 103s（753 页从 40 分钟变 5 小时）。详见 `docs/postmortem/perf-regression-2026-10-23.md` |

### 事件语义

| # | Problem（触发条件） | Standard（必须怎么做） | Checkpoint（在哪检查） |
|---|---|---|---|
| **DL-8** | 消费 `end_utc_ms IS NULL` 的 app/web 点事件 | 必须补时长 `dbview.POINT_EVENT_MS = 60_000` | 改 `dbview` 后核对：`shion` 28,805 条 100% 无 end，`ce > cs` 恒假会让整类被丢弃（表现为「明明有 web 记录却没展示」） |

### 跨层

| # | Problem（触发条件） | Standard（必须怎么做） | Checkpoint（在哪检查） |
|---|---|---|---|
| **DL-10** | 中间结果跨层传递（适配器→视觉→LLM） | **必须有端到端哨兵**，不能靠每层自报成功 | 新增跨层字段后，先构造一次真实调用并断言「下游真的收到了」。实测：vision 层报「收集到 23 张，missing=0」+ llm 层报「23 张缺失，已跳过」，两层日志各自正常，合起来是「一张图都没送」——PC 侧 1,874 条总结全是盲总结（`_meta.images_sent=0`）。哨兵见冒烟 T27 |

### 上游模型通道

| # | Problem（触发条件） | Standard（必须怎么做） | Checkpoint（在哪检查） |
|---|---|---|---|
| **DL-11** | 上游返回错误码 | 必须按**语义**分三类：可重试（`429`/`413`/`5xx`）、不可重试（`400 sensitive`/`401`）、需换抽样（`400` 其他文案）。退避**必须跨过 rpm 的 60 秒滚动窗** | 改 `llm._post` / `vision` 的错误分支后，跑一次注入式断言（冒烟 T30a–e 含「413 可重试」「400 不可重试」两条反向断言）。三条实测：①`429 rpm exhausted` 用 `tries=4/max=20s`（共 30s）**全部再撞**，改 `tries=5/max=65s` 才有效；②`413` 文案写 `payload too large` 但**与体积无关**（base64 仅 0.16–0.53MB），真因是**畸形截图**（1280×20，宽高比 64:1），一张退化图毒掉整片，修法是 `_is_degenerate` 前处理过滤；③`400 sensitive image` ≥16 张必触发，故 `MAX_SEND_IMAGES=8`。详见 `docs/postmortem/413-degenerate-image-2026-10-23.md` |
| **DL-12** | 想「把 `max_tokens` 给满保平安」 | `max_tokens` 是**步长不是安全带**：起始 `MAX_TOKENS=8192`，只在「空内容」时翻倍放大到 `MAX_TOKENS_CEILING=65536` | 改预算前后各跑 2 次同片计时对拍。实测同片 8 图：`65536` → 269.9s/217.0s、推理 23,439/20,568 token；`8192` → 189.9s/71.4s、推理 1,749/1,383 token，**输出字数反而更多**（10,182 vs 6,865） |
| **DL-13** | 想知道还剩多少额度 / 遇到 429 就整批停 | 额度**不可读只能探**；且「限流」与「额度耗尽」必须分开处置。**按 429 正文的 `type` 分类必错**——`type` 恒为 `quota_exceeded_error`，真语义在 `message`：含 `rpm` 是每分钟限流（等 90s 即恢复），无 rpm 才是额度耗尽 | 改 `batch_vision._classify` / `wait_for_quota` 后跑冒烟 T31a–h。依据（2026-10-23 实测）：响应头无 `x-ratelimit-*`；`/v1/usage`、`/v1/quota`、`/v1/rate_limit`、`/v1/me` 全 404（`/v1/models` 200 证明路径没错）；bridge 的 `quota.remaining` 四通道全 `null` |

### 产出与呈现

| # | Problem（触发条件） | Standard（必须怎么做） | Checkpoint（在哪检查） |
|---|---|---|---|
| **DL-14** | 想改日报结构 / 恢复模板渲染 | 日报**只有一条渲染路径**：`daily.render_narrative`。`render_daily` 已降级为其别名（仅兼容旧调用），**不得恢复**基于 `_render_key_actions` 的罗列式模板（会把当天每片每条 action 都列出：实测 2026-08-14 有 168 条、2026-09-15 有 248 条） | 改 `daily.py` 后：①冒烟 T32–T36 全绿；②抽查新日报确有自然段、省略号 0、无 `### 应用明细（全量`；③**重跑产出**（`rebuild_daily.py` → `cli publish`），只改代码不算完成 |
| **DL-15** | 同一个 URL/标题在时间线与网站两处显示不一致 | 链接标题**只有一个真相函数** `slice_pages/linktitle.resolve()`，时间线与网站**两处同序调用**，任何一侧都不得再写自己的降级链 | 生成任一双设备日页面，逐行核对：时间线里出现的标题，网站 section 里同一 URL 必须是同一文本。用户原话：「网站和时间线的链接要保持同步，不能时间线上已经有标题显示，但是你的网站还没有标题显示」 |
| **DL-16** | 加列/加索引时先建索引后补列 | `init_db` 必须**先 `_migrate` 补列、再 `executescript(DDL)`**（DDL 里 CREATE INDEX 会引用新列） | 任何 schema 变更后，对**旧库**跑一次 `init_db`，不得报 `no such column` |
| **DL-17** | 视觉 section 里父片（`status=split`）只列子片状态、不渲染子片正文 | 父片卡片必须**递归展开整棵 `split_of` 子树**，每个**叶子片**的 `overall_summary` 用 `render_summary_md` 完整渲染；中间层既不是叶子也不得重复渲染；**叶子片总数 == 渲染出的总结块数**，每个叶子只出现一次。片数统计口径一律按**叶子片**（父片是容器、没有 summary；按片数算会把已完成显示成未完成：2026-08-13 会显示 61/75） | 生成任一含子片的日期页（如 `2026-08-07`：19 父片 / 184 叶子），断言 `html.count(vnode leaf)` == 独立 DFS 叶子数；再抽查 3 个叶子 summary 正文首段字面命中 |
| **DL-18** | 页面上「有效点」的个数由两处各自计算，点数与图上点对不上 | 时间线「有效点」= `timeline.points()`（行时长 >= `MAJOR_SECONDS=120`），视觉「有效点」= `vision.points()`（有 summary 的**叶子**片），定位条只聚合这两处，**不得自己重算**。锚点 id 也必须在 `timeline.row_anchor()` / `vision.card_anchor()` 单点生成，定位条与表格/卡片共用。**2026-10-25 收窄适用范围**：本条只约束**日报**的「突出部分」口径；时段页定位条改用 DL-19 的全量口径，两者**不是同一个数**，不许互相替代 | 任一日期页断言 `data-n` == 独立遍历重算的**全量条数**（DL-19）；日报侧断言「有效点」= `>=120s` 的行数。两者分开测 |
| **DL-19** | 定位条只画「有效点」（>=120s 的行），用户看不到当天全部事件 | 时段页定位条必须覆盖该段 **100% 条目**：①单行 <= `POINT_CAP=200` 时**逐点画**（每条可独立点开）；②>200 时按 `bucket_w = max(60, min(900, ceil(span_s/200)))` 聚合，桶宽按**时间跨度**算（不是行数，量纲不同），每桶把条数写进 `data-c`，**Σdata-c 必须等于该设备该段全量条数**——聚合不是抽样，一条都不许丢；③桶的 `href` 指向桶内**第一条**事件的锚点。图例必须注明降采样（数据完整性可观测）。**循环里不得出现 SQL**（DL-9） | 逐设备逐段三条并列断言：①行数守恒 Σdata-c == 全量条数；②标记数 <= 200；③桶 href == 独立分桶复算的首条锚点。再断言全页 id 唯一。**S20：把降采样改成「截断前 200 条」→ 断言①必须变红**（实测 Σ=200 真实=650） |
| **DL-20** | 折叠/收起超长正文时顺手截断，把「默认收起」做成了「内容让位给长度」 | 折叠**只改默认显示状态，不改字节**：全文必须完整留在 `<details>` 内。片层总结「不截断」是数据侧铁律（§A 三条口径之三），折叠不得违背。用原生 `<details>`（`base.reveal()` 已支持跳转时自动展开），不写 JS | 任取一个超长片，断言「库里 summary 经 `render_summary_md()` 渲染后的 HTML **逐字出现在页面里**」。**注意 S63**：不能拿原始文本比对（`## x`→`<strong>`、`- x`→`<li>`），必须比渲染等价物。**S20：把折叠改成截断 → 该断言必须变红**（实测 61 片中 36 片全文消失） |
| **DL-22** | 呈现层/运维脚本 import 领域层的**下划线私有符号**（`from digitallife.timeline_merge import _clean_title`） | 跨包只用**公开名**。需要复用内部实现时，把被复用的那个函数**升格为公开 API**（去掉下划线），原私有名保留为兼容别名（`_clean_title = clean_title`，指向**同一对象**，不许是副本）。呈现层三处（`contract.py`/`timeline.py`/`web.py`）2026-10-25 已全部切换到 `clean_title` | 用 AST 扫三个呈现层文件的 `ImportFrom`，不得出现以 `_` 开头的名字；再断言 `_clean_title is clean_title`。**S20：把别名改成 `lambda` 副本 → 变红** |
| **DL-23** | 同一个小工具（`_parse_json`）在两个模块里各抄一份，改一处忘另一处 | 重复实现必须收敛到**单一出处**：`digitallife/textutil.py`。`daily.py`/`publish.py` 2026-10-25 已改为 `from .textutil import parse_json as _parse_json`（别名保留，避免改所有调用点）。**不要在"建了共享模块"之后顺手把只被一处使用的函数也搬进去** | 用 AST 统计全库 `def .*parse_json` 的定义数，必须恰好 1 个且位于 `textutil.py`。**S20：在 daily.py 里再抄一份 → 变红** |
| **DL-24** | 运维脚本（`finish_phone2.py` 这类「一键收尾」闸门）反向依赖加工层私有符号 | 闸门脚本只能调**公开 API**。需要「这一片有没有画面描述」这类语义时，由加工层提供语义明确的公开函数（`vision.slice_has_description(con, slice_id, meta)`），把内部两段拼装收进去。**升格时必须验等价性**：新旧两条路径对同一批片结果逐片相同 | AST 扫 `finish_phone2.py` 的 `ImportFrom digitallife.vision`，不得有下划线名；再跑等价性对拍（旧 `bool(_collect_descriptions(_slice_events(...)))` vs 新公开函数，逐片一致）。**S20：重新 import `_slice_events` → 变红** |
| **DL-21** | 同一个「设备显示名」在多处各写一份折叠逻辑，改一处另两处静默不变 | 设备显示名**只有一个真相函数**：`devices.name_of(device_id) or device_id`。`daily` / `publish` / `slice_pages` 三处都调它，任何一处不得再写 `if len(by_kind(...))>1` 之类的折叠分支。**中/西文相邻处补空格**也必须走同一处（`daily._cjk_gap_before`），不得各写一套 | 用同一 device_id 跑三个入口，断言输出字符串逐字相同。**实测反例（2026-10-25）**：改完 `daily` 后 `publish._render_slice_md('pc-main')` 仍输出「电脑」而 `daily` 输出「D-001」。再断言 `## D-001 使用` 与 `设备是 D-001` 都带空格（`设备是D-001` 必须 0 处） |

---

## §D 各层怎么做

> 六个层：采集 → 存储 → 领域 → 加工 → 发布 → 呈现。每层只写「怎么改才不闯祸」，
> 数据形状的权威定义在 `docs/spec/`，此处只引用。

### D.1 采集层（`sources/`）

- 新增源 = 加适配器 + 在 `ADAPTERS` 注册 + 在 `model.SOURCES` 加名 + 补冒烟断言
  （+ 若引入新设备则在 `devices.py` 加一行）。**不要改 `event` 表结构去容纳源特有字段**，
  特有信息进 `payload` JSON。格式约定见 `docs/spec/data-sources.md`。
- **设备归属由导入配置声明，不由原始文件推断**（原始文件里没有可靠设备标识）。
  `DL_DEVICE_<SOURCE>` 环境变量 > 适配器 `device` 默认值。

### D.2 存储层（`schema.py` / `store.py`）

- 表结构变更走 `schema.py` 的 `DDL` + `MIGRATIONS` 登记；`init_db` 顺序见 DL-16。
- 连接一律走 `store.connect()`（内部已设 `check_same_thread=False`）。

### D.3 领域层（`model.py` / `tz.py` / `dbview.py` / `slicer.py`）

- `device_id` 形如 `<类别>-<短名>`，**定下不要改**（参与 `event_id` 计算）。
  旧值 `pc`/`phone` 经 `LEGACY_ALIASES` 只读兼容，**新写入不得再用裸值**。
- 三个名字字段别混用：

  | 字段 | 例 | 用在哪 |
  |---|---|---|
  | `device_id` | `pc-main` | 库、`event_id`、SQL 过滤 |
  | `name` | `D-001` / `iQQQZ3` / `iQQQ Neo11` | **网页与界面上一律显示它** |
  | `label` | `主力电脑` / `手机 1` / `手机 2` | 需要成句的场合 |

- **日报里的设备节标题只有一条规则**（`daily._device_label`，2026-10-25 用户裁决后）：
  **一律**用 `devices.name_of()` 的真实设备名 —— `## D-001 使用` / `## iQQQZ3 使用` / `## iQQQ Neo11 使用`。
  旧规则「同类只有一台 → 沿用电脑/手机」**已于 2026-10-25 删除**（它让 `pc-main` 显示成「电脑」，
  与网页侧 `contract.device_label()` 口径不一致）。契约见数据侧 `AGENTS.md` §D.1 第 1 条（DL-21）。
  **当日没有该设备数据时整节不出现**——这条不变。
- **加设备时必须同时改三处**，漏一处就静默失效：`devices.py` 的 `DEVICES`、
  `sources/` 对应适配器的 `device`、`scripts/build_slice_html.py` 的设备遍历。
  该遍历**不得写死 `(PC_DEVICE, PHONE_DEVICE)`**；panel id **不得用 `dev.split("-")[0]`**
  （会把 phone-1/phone-2 截成同一个 id，两个 panel 撞号后页面上直接少一台设备）。

### D.4 加工层（`vision.py` / `prompts/` / `schema_check.py`）

**参数定论**（官方文档 + 多变量实测，改参数前先读 `docs/spec/interfaces.md` §2.11）：

| 参数 | 定论 |
|---|---|
| 端点 | `https://token.sensenova.cn/v1/chat/completions`（`base_url` **必须带全路径**，只写 `/v1` 会 404）|
| 模型 | `sensenova-6.8-flash-lite` |
| `thinking` | 官方文档：**必填 object，默认 `{enabled:true}`**。必须显式传 `{"enabled": false}` |
| 图片 | **只能** `{"type":"image_url","image_url":{"url":"data:image/webp;base64,…"}}`；传 `image_base64` 字段形式会被拒 400 |
| `max_tokens` | 起始 `MAX_TOKENS=8192`，空内容时放大到 `MAX_TOKENS_CEILING=65536`（见 DL-12）|
| 送图张数 | 每片最多 **8 张**（`MAX_SEND_IMAGES`，均匀抽样保留首尾）（见 DL-11）|
| 缩图 | 送模型前缩到**长边 1280**、WEBP q=72 |
| 退化图 | **必须过滤**（`_is_degenerate`：宽高比 >12 或短边 <64）（见 DL-11）|

**兜底三层，缺一不可**：

1. **空内容重试**：`vision.py` 的 `while` 上限 **40 次**，每轮逐步放大 `max_tokens`——必须有内容产出。
2. **结构打回重做**：`schema_check` 判不合格 → 把**具体违规理由**回灌模型重做，最多 `REWORK_ROUNDS=5` 轮；
   仍不合格**照常写库**，只记 `_meta.schema_ok=False`（有内容就入库，格式问题可事后 prune）。
   ⚠️ **`batch_vision` 不得「写后即删」**：它曾 `DELETE FROM slice_result`，实测一次批量丢了
   37 片已读到图的总结。现在只分类上报、行保留；**清理口径只有一个：`scripts/prune_results.py`**（哨兵 T31k）。
3. **并发**：模型侧可并发但有上限。当前采用 **32 并发 + `DL_LLM_TIMEOUT=240` + 60s 退避**（净 5.7 片/分）。

   **退避不是越长越好**——它是阻塞式 `time.sleep`，占着槽位不产出。95s 退避在 40 并发下把一半槽位
   变成「睡觉的」，净产出反而从 7.9 掉到 3.96；压到 60s（20s+40s，刚好跨一个 rpm 窗）并降到 32 并发后回到 5.7。
   **判断标准永远是净成功片数**，不是失败率、也不是退避有没有「绕过去」。
   详见 `docs/postmortem/vision-perf-and-blind-fix-2026-10-23.md`。

**关于 `thinking.enabled` 的真相**（两轮子代理都判错过，这里写死）：它不是硬开关，推理量仍会随机暴涨
（同一请求两次 565 / 1131 个推理 token）。但实测 `{enabled:false}` 下 20 次请求 0 次空内容；
真正让 `content` 为空的是 **`max_tokens` 太小**（`max_tokens=512` 时 5 次里空 4 次）。
**归因要归到预算，不要归到参数失灵。**

**格式约定**（`prompts/segment.py` 的 `PROMPT_VERSION` + `OUTPUT_FIELDS`，8 个字段缺一不可）：
`apps, categories, timeline, key_actions, described_images, image_tags, image_descriptions, overall_summary`。
`overall_summary` 必须是三节、顺序固定、**每节至少 3 条列表要点**（清单体——片层是证据；日记体只在日层）：
`## 关键操作` / `## 主要活动` / `## 重点内容`。
**改字段或改小节，必须同时改 `prompts/segment.py` + `schema_check.py` + 冒烟 T13。**

> ⚠️ 注意区分两层：**片层 `overall_summary` 的三节标题**是上面这三个；
> **日报正文**里没有 `## 关键操作`（日报走 `render_narrative`，结构见数据侧契约）。两者同名易混。

### D.5 发布层（`publish.py`）

- `publish_all` 产出：`20-日报/*.md`、`10-时段/片层/...</`、`.data/digital-life/exports/*.ndjson`、
  `_index/manifest.json`，可选 zip。
- **`publish` 只搬运库内已存的 `output_text`，不会重新生成**——顺序不可倒：`daily` → `publish`。
  （B15 之后新增：日报正文走现算 `build_daily`，见冒烟 T35a/T35b。）

### D.6 呈现层（`scripts/build_slice_html.py` + `slice_pages/`）

- 产出 **`10-时段/YYYY-MM-DD.html` 一个文件**：header（日期+星期+规模）+ 四个 section
  （时间线 / 应用 / 网站 / 视觉），每个 section 内按设备用 tab 切换，右下 fab 目录浮钮跳锚点。
- section 模块契约：暴露 `ANCHOR` / `LABEL` / `panel(pid, ctx, hidden=...) -> (html, 行数)`
  （`vision` 是跨设备 section，签名不同）。**渲染顺序与目录锚点由编排器统一控制**；
  页面外壳（header/footer/浮钮/主题）只在 `base.py`。新增 section 必须挂进 `build_slice_html.render_day`，
  否则**被静默吞掉**（不报错也不产出）。
- **设备 tab 的取舍**：某 section 里设备 0 行时**不生成这个 tab**；只有 1 个设备有内容时整条 tab 控件不出现。
- **链接标题统一走 `linktitle.resolve()`**（见 DL-15）。抓取落 `link_title` 表，`status='failed'` 也落行；
  **渲染期绝不联网**。

### D.7 时间线归并（`timeline_merge.py`）

同一次上网会被两条记录各记一遍，必须合并成**一行 + 整行做链接**。四层归并，缺一层就会漏重复：

| 层 | 判据 | 落点 |
|---|---|---|
| 网页事件 | `url` 以 http/https 开头 | **仅当与窗口行起止时间完全相同**才挂：第一条当 `link_url`（整行链接），同窗其余页进 `sub`；对不上一律独立成行 |
| 路径孪生 | 标题是盘符路径 / `/home/` / `/Users/` | 记 `path_twin`，**不单独成行** |
| 同区间同义 | 同 `start/end` + 都无链接 + 一条标题包含另一条 | 收进 `alt_of`，只留信息最全的一条 |
| 同页回声 | 页面身份同 + 区间重叠 ≥ 较短者 90% + 一侧无链接（或跨类型同标题） | 折成一行，保留有链接的一方 |

**父行判据只有一个：时间窗完全相同。** 五天 2,560 个网页事件实测：起止完全相同 1,282 ｜
start 差 ≤2s 363 ｜ 差 ≤60s 788 ｜ 差 >60s 127 —— 从「差 60 秒」起就是不同行为。
**宁可时间线长几行，也不许把不同事件说成一件事。**

实测（2026-08-13 / pc-main）：910 条原始 → **534 行，重复区间 0**，300 行带链接，26 行有子行。
**改这个文件等于同时改日报、时段网页、以及任何消费 timeline 的地方——先跑冒烟再跑两个样本日。**
语义契约由冒烟 **T19a–T19j** 常驻把守（含 90%/89%/60% 重叠边界、跨类型标题对齐、候选插入顺序、
槽位代次失效、3000 行不退化反向哨兵）——**改这个函数必须先让 T19 全绿，并按 S20 逐个注入缺陷确认它能变红**。

---

## §E 常用命令

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

python scripts/smoke.py                                     # 主回归；项数以末行「通过 N / 失败 0」为准
# 批量视觉：32 并发 + 超时 240 + 60s 退避是实测最优组合（净 5.7 片/分，见 D.4）
DL_LLM_TIMEOUT=240 python scripts/batch_vision.py --all --workers 32 --rounds 14
python scripts/rebuild_daily.py                             # 批量重生成日报
python scripts/build_slice_html.py --all                    # 重生成时段网页
python scripts/build_slice_html.py --date 2026-08-13        # 单日
python scripts/build_slice_html.py --all --prune            # 顺带删旧日期目录与 _html/
python scripts/fetch_titles.py                              # 抓网页标题入 link_title 表（唯一联网入口）
python scripts/prune_results.py                             # 只报告；加 --apply 真删
```

**冒烟口径（唯一真值）**：`python scripts/smoke.py` 末行输出 `通过 N / 失败 0`。
**本文与其他文档一律不再抄写具体数字**——数字会漂移，命令不会。
2026-10-25 实测为 **187 / 0**（命令：`python scripts/smoke.py 2>&1 | tail -1`）。

> ⚠️ **本数字是采集时刻的快照，不是常量**。同一天晚些时候代码仍在改造中，断言数即变为
> **191 条且 T36d2 失败**（`linktitle` 同源改造的进行中状态）。**判断回归是否通过，只看你自己跑的那一次末行**，
> 不要拿本文的 187 去对拍别人跑出来的结果。

### 视觉识别需要的环境变量

```bash
export DL_LLM_BASE_URL="https://token.sensenova.cn/v1/chat/completions"   # 必须带全路径
export DL_LLM_API_KEY="<key>"
export DL_LLM_MODEL="sensenova-6.8-flash-lite"
export DL_LLM_TIMEOUT=240            # 并发批量时必须 240：900 会让挂死片占住槽位 15 分钟
export DL_WORK_DIR="<ManicTime 解包目录>"
```

---

## §F 改完必须自己验的事

1. **`python scripts/smoke.py` 必须 0 失败**（不是「大概绿了」）。项数以末行为准。
2. **幂等**：同一批数据跑两遍，`count_events` 不变。
3. **截图守恒**：叶子片 `SUM(image_count)` == 源 `kind='screen'` 事件数（逐设备对齐）。
4. **结构合规**：`python scripts/prune_results.py` → 不合规必须 0（这只是报告口径，不代表「入库被阻断」）。
5. **注入验红（S20）**：新增断言后，**故意注入缺陷必须变红**，还原后复绿。只跑绿不算通过。
   注入不变红时先查**触发条件是否真被构造出来**（S58），不要先怀疑注入手法。
6. **发布后回显**：`20-日报/` 真有文件、`manifest.json` 的 counts 与库内一致。
7. **日报抽样**：新日报里省略号计数为 0、确有自然段；且**没有** `### 应用明细（全量` 与 `代表页面`。
8. **网页包**：`build_slice_html.py --date <某双设备日>` → `10-时段/<日期>.html` 存在且 > 50 KB、
   含四个 `<section>`、无「尚未实现」；双设备日必须真有 `role="tablist"` 与 hidden panel。
9. **时间线不重复**：`merge_browser_web` 输出里**同一 `(start, end)` 只能出现一次**；
   用两个样本日（一个纯 PC、一个 PC+Phone）各验一次。
10. **改了 `daily.py` 必须重跑产出**：`rebuild_daily.py` 再 `cli publish`。
    改代码 ≠ 改数据——库里 `output_text` 和 `20-日报/*.md` 都是早先跑出来的快照。
    **验收必须抽查 md 文件本身，不能只看代码或库表。**
11. **改了 `schema.py` 必须对旧库跑一次 `init_db`**（DL-16），并跑一次全量 `verify`。

---

## §G 已知边界与废弃清单

### G.1 已知边界（不要当成 bug 去「修」）

| 现象 | 真相 |
|---|---|
| `slice.status` 永远是 `pending`/`split` | 全库无人回写；幂等判据是 `NOT EXISTS(slice_result)`，别用 status 选片 |
| `10-时段/*.html` 数量 ≈ 有日报的天数，`片层/` 下却远少于片总数 | 分工如此：**一天一页 html**；片层 md 只有出了视觉总结的片才写（`DL_PUBLISH_ALL_SLICES=1` 可全量） |
| 单页里某设备整段不见 | 该设备当日**该 section 0 行**，tab 就不生成（不是渲染失败） |
| `slice_result` 里有 `schema_ok=False` | 设计如此：格式不阻断入库（见 D.4）。历史行 `_meta.schema_ok` 可能是 `null` |
| 手机截图本机没有文件 | ScreenMemo 的 `path` 是**安卓设备路径**，本机不存在。视觉走 `reader="text"` 用 ScreenMemo 自带的画面描述，**全程不碰图片** |
| ScreenMemo 的描述覆盖率不是 100% | 描述只覆盖 **21.3%** 的截图（26,742 / 103,424）；按片算 92% 的片至少有一条描述。**不要把「表内有效描述率 99%」误读成「全量截图覆盖 99%」**——两个分母不同 |
| `phone-2` 的 `kind='app'` 事件是合成的 | `segments.app_packages` 实测 666 条全空，一段都产不出。改用截图自带的 app 名合成（同一 app、相邻间隔 ≤120s 连成一段）。没有它 `dbview` 的 apps/timeline 永远是空数组 |
| 库/NDJSON 不在 vault | 已移 `.data/digital-life/`；`_index/` 只留 `manifest.json`。别再用 `_index/digital-life.sqlite` 这个旧路径 |
| 无模型时重跑日报，内容变模板 | **已有叙事会被复用**（`build_daily` 读 `structured_json.narrative`），不会因缺模型而丢掉已写好的正文 |
| 残留旧代码进程会写 vault | 曾把 vault 膨胀到 6,528 篇空壳。收尾必须按 PID/端口确认无残留（S49） |

### G.2 废弃清单（不得静默删除，此处留痕）

| 已废弃 | 时间 | 原因 | 替代物 |
|---|---|---|---|
| `daily.render_daily` 的模板渲染（`_render_key_actions` 罗列路径） | 2026-10-24 | 用户裁决「`render_daily` 这种输出长列表的无效」；当天 168–248 条 action 全列出，日报沦为流水账 | `daily.render_narrative`（`render_daily` 保留为别名，仅供旧调用方） |
| `10-时段/` 的「日期目录 + 9 页」结构 | 2026-10-22 | 用户裁决「直接一个 HTML 包含一天的信息」 | `10-时段/<日期>.html` 单文件 |
| `10-时段/_html/`（751 个单日页） | 2026-10-22 | 同上 | 同上；`--prune` 可重复执行 |
| `scripts/rewrite_style.py`（片层文体改写） | 2026-10-24 | 把文体要求错放到片层，导致 3,739 片清单体全判不合格、631 片原文永久丢失 | 保留作**反面教材**，勿用 |
| 时间线「代表页面」列 | 2026-10-22 | 用户裁决取消 | 网站段只到域名粒度 + 每域名全量清单 |
| 磁盘标题缓存 `tmp/.titlecache.json` | 2026-10-25 | 每次建页重抓、跨进程不一致 | `link_title` 表（见 DL-15） |

---

## §H 接口

- **注册中心**：`01-Projects/second-brain/config/pipelines.json` 有 `digital-life` 一行（唯一登记处）。
- **只读查询面**：second-brain 的 `apps/api/routers/digital_life.py` 暴露 `/api/dl/days|slices|stats`，
  只读 vault（`20-日报` / `10-时段` / `_index/manifest.json`），**不碰 `.data/`**。
- **日记系统消费**：`PATCH /api/diaries/{date}` 把日总结写进
  `04-Areas/GTD/40-执行/日记/*.md` 的 `<!-- DIGITAL-LIFE-BEGIN/END -->` 锚点区。
  **该锚点独立于 `AI-SUMMARY`，不要复用**（S47：同一字段不得两种口径）。
- **签名级契约**：`docs/spec/interfaces.md`（唯一真源）；一致性对账见 `docs/spec/CONSISTENCY.md`。
- **数据区说明书**：`04-Areas/数字人生/AGENTS.md`（数据视角，与本文件互补，不重复）。
- **架构分层视图**：`docs/architecture/`（子代理3 产出中）。
- **工作区通用规则**：根 `AGENTS.md` 的 CP-1…CP-6 与 S1–S59 —— **只引用，不复制**。
