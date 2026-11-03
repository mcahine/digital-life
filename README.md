# digital-life

把散在不同设备、不同软件里的个人使用记录，清洗成**一张统一事件表**，再分两层加工成总结：
**模型写的片层总结** → **模型写的日层叙事日报**，供第二大脑的日记系统消费。

> **改代码前先读 [`AGENTS.md`](./AGENTS.md)** —— 那里写了"怎么改才不闯祸"（含 WAL 复制毁库、
> 幂等键丢行、视觉参数定论、设备别名查询等实测教训）。本文件只做速览。

> 四条主轴：
> 1. **时间是线性的，设备只是维度** —— event 表以 UTC 毫秒为唯一主轴，`device` 是字段不是分区。
> 2. **数据库是骨架，截图是肌肉** —— ManicTime 的窗口标题/URL/活跃状态构成结构化骨架；
>    视觉模型只补画面内容，冲突以 DB 为准（`conflict_count` 可观测）。
> 3. **只产每日总结** —— 周/月/季/年由日记系统自己滚，本模块不碰 `50-回顾/`。
> 4. **Python 只搬运原料** —— 片层与日层总结都由模型写；禁止用模板把时间轴罗列成日报。

## 两层加工

```
原始痕迹（ManicTime DB + 截图 / ScreenMemo / IQQZ3 / Tai / shion）
        ↓ ingest → 统一 event 表（UTC 毫秒单一主轴）
        ↓ slice  → 15 分钟一片，超 24 图递归二分
        ↓ vision → 片层：模型读截图 + DB 结构，写完整总结（不截断、不省略）→ slice_result
        ↓ daily  → 日层：当天所有片总结整篇喂模型，二次总结成自然段叙事 → daily_summary
        ↓ publish→ 10-时段/*.md、20-日报/*.md、NDJSON、manifest
```

> ⚠️ **本仓库只含代码，不含数据。**
> 克隆下来跑 `pytest` 能全绿（测试用临时库），但 `scripts/smoke.py` / `verify_vault.py`
> 需要**本机生产库**才会真正检查到数据。没有库时它们会跳过或报空 —— **这不是代码坏了**。
> 详见下方「## 从零 clone 到测试全绿」。

## 当前实况（2026-11-03 实测）

| 指标 | 值 |
|---|---|
| 统一事件表 | **1,124,831** 条 |
| 时间片 | **43,257** 片（15 分钟 UTC 网格，超限递归二分）|
| 视觉结果 | `slice_result` **9,679** 片 |
| 日总结 | **753** 天，其中 **143 天**有片层原料、**142 天**达标（≥800 字）|
| 链接标题 | `link_title` **73,291** 行（ok 50,038）|
| 生产库 | 870.2 MB（**不入库**，在 `知识库/.data/digital-life/`）|
| 主回归 | `scripts/smoke.py` **213 项** ｜ `pytest` **65 passed** |

## 从零 clone 到测试全绿

```bash
git clone https://github.com/mcahine/digital-life.git
cd digital-life

# 1) 装依赖（Python 3.11+）
pip install -e .
pip install pytest httpx            # 测试与抓取需要

# 2) 立即能绿的（不依赖生产库）
export PYTHONUTF8=1
python -m pytest                    # 期望 65 passed
```

**到这里就结束了 —— 其余全部依赖不入库的生产库。**

| 命令 | 需要什么 | 没有时的表现 |
|---|---|---|
| `pytest` | 无（用临时库 fixture）| ✅ 直接绿 |
| `scripts/smoke.py` | 无（强制临时库）| ✅ 直接绿 |
| `scripts/verify_vault.py --fast` | **生产库 + vault 产物** | ⚠️ 会报空/跳过 |
| `scripts/check_linkhealth.py` | **生产库** | ⚠️ 打不开库则退出码 2 |
| `scripts/check_migration.py` | **生产库**（会复制 870 MB 到系统临时目录）| ⚠️ 报「真库不存在」 |
| `cli daily/publish` | **生产库 + 模型凭据** | ❌ 无法产出日报 |

**要跑全量，你需要自己重建数据链**：

```bash
# 原始件放 00-Inbox/，然后按顺序：
python -m digitallife.cli ingest --source manictime --path <zip>
python -m digitallife.cli ingest --source shion
python -m digitallife.cli slice  --device pc-main
python -m digitallife.cli vision --device pc-main     # 需 DL_LLM_* 凭据
python -m digitallife.cli daily  --date 2026-08-13
python -m digitallife.cli publish --no-zip
```

> **数据不可重算**：中间物可删了重跑 `ingest`，但**原始件（00-Inbox）与生成的叙事正文不可逆**。
> 换机器时请整体备份 `.data/digital-life/`，不要只 clone 代码。

### 环境变量（都不入库）

```bash
export DL_LLM_BASE_URL="https://token.sensenova.cn/v1/chat/completions"
export DL_LLM_API_KEY="<你自己的>"
export DL_LLM_MODEL="sensenova-6.8-flash-lite"
export DL_LLM_TIMEOUT=240
```

> 凭据只放环境变量或 `config/llm.json`（已被 .gitignore 排除），**不要写进任何被跟踪的文件**。

## 五个数据源

| 源 | 设备 | 形态 | 覆盖 |
|---|---|---|---|
| manictime | pc-main | 截图 + Reports.db 四条时间线 | 2026-08…10 |
| screenmemo | phone-1 | 分片 SQLite（42 分片，16 个含 `shots`）| 2026-05…08 |
| iqqz3 | phone-1 | 108 顶层目录 + `daily/`（287 条）| 2025-01…04 |
| tai | pc-main | 应用时段 + 网页统计 | 2024-12…2025-03 |
| shion | pc-main | 浏览器历史 | 2024-09…2025-03 |

> tai 与 shion 有 6,214 条交集，**跨源归并后恰好 33,967**。
> 设备 id 用注册表（`digitallife/domain/devices.py`），旧值 `pc`/`phone` 经 `LEGACY_ALIASES` 只读兼容。

## 常用命令

```bash
cd 01-Projects/digital-life
export PYTHONUTF8=1          # Windows GBK 控制台必设（S33）
unset DL_DB DL_WORK_DIR      # 这两个覆盖变量会传染，收尾必须清

python -m digitallife.cli ingest  --source manictime --path <zip>
python -m digitallife.cli dbview  --date 2026-08-13
python -m digitallife.cli slice   --device pc-main
python -m digitallife.cli vision  --device pc-main --limit 10
python -m digitallife.cli daily   --date 2026-08-13
python -m digitallife.cli publish --no-zip
python -m digitallife.cli verify
```

批量与清理：

```bash
python scripts/smoke.py                                      # 213 项主回归
python -m pytest                                              # 65 项单元/回归测试
python scripts/verify_vault.py --fast                         # 产物终验（需生产库+产物）
python scripts/batch_vision.py --all --workers 24 --rounds 8  # 批量视觉（须配 DL_LLM_TIMEOUT=240）
python scripts/batch_vision.py --all --blind --workers 24     # 重做"没送图就写了总结"的历史片
python scripts/rebuild_daily.py --workers 4                   # 重生成日报（753 天约 9 分钟）
python scripts/build_slice_html.py --all --workers 6          # 重生成单日页（753 页约 6 分钟）
python scripts/build_slice_html.py --all --prune            # 顺带删旧的日期目录与 _html/
python scripts/prune_results.py                             # 只报告；--apply 真删不合规结果
```

## 路径

| 内容 | 位置 |
|---|---|
| 代码 | `01-Projects/digital-life/`（**数据不落此处**）|
| 给人看的产物 | `04-Areas/数字人生/`：`10-时段/YYYY-MM-DD.html`（**一天一页全量**）+ `10-时段/片层/`（片层 md）、`20-日报/`、`_index/manifest.json` |
| 大体量中间物 | `知识库/.data/digital-life/`：`digital-life.sqlite`（678 MB）、`exports/*.ndjson`（530 MB）、`work/`（解包原始 DB）|
| 原始件 | `00-Inbox/` 原地读取，不复制、不留副本 |

> `.data/` 由根 `.gitignore` 的 `*.data` 规则排除；`_index/` 只剩 `manifest.json` 入库。
> 全部中间物**可重建**：删掉重跑 `ingest` 即可。

## 日报 vs 时段网页（分工，2026-10-22 用户裁决）

| | 日报 `20-日报/` | 时段网页 `10-时段/YYYY-MM-DD.html` |
|---|---|---|
| 内容 | **只留突出部分**：前 6 应用 + 前 5 域名各一句 + 压缩时间线（≥2 分钟） | **全量**：所有应用、所有域名与其全部页面、全部时间线、全部片 |
| 形态 | 模型写的自然段叙事 | 按页分类的可查表 + 完整记录 |
| 谁写 | `daily` 调模型 → `publish` 落盘 | `scripts/build_slice_html.py` 渲染数据库 |

**一天一个 HTML**（2026-10-22 用户裁决；视觉照搬 `00-Inbox/index.html`）：

```text
2026-08-13.html
├── header    日期 · 数字人生 + 星期 + 规模统计 + 深浅色按钮
├── 时间线    device tab；浏览器窗口与网页已合并为一行 + 整行链接
├── 应用      device tab；全量，前 12 个之后「展开全部」
├── 网站      device tab；按域名 details，展开是该域名的每一次访问
├── 视觉      逐片卡片，全量不折叠
└── 右下 fab  目录浮钮 → 四个锚点
```

片层 md 不在这页里，落 `10-时段/片层/YYYY-MM-DD/HHMM-HHMM.md`（只有出了视觉总结的片才写）。

> 三个口径：① 一个时间戳就够（同区间的窗口行与网页行合并，第一条当整行链接）；
> ② **没有「代表页面」这一列**；③ 不写中间 JSON，删掉 html 重跑即重建。

## 文档地图

> 编排原则：**按「你此刻要做什么」找文档**，不是按文件名找。
> 一个目录一件事，同一件事只写一处，其他地方只引用。

```
docs/
├── spec/           设计真源（长期稳定，改动要慎重）
├── postmortem/     复盘归档（按日期，只增不改，供追溯）
├── WORKFLOW.md     管线怎么走
├── HANDOFF.md      接手后先干什么
└── phone-vision-*  phone-2 专项（runbook + 报告模板）
```

### 1. 我要动代码 → 先读这些

| 文件 | 管什么 |
|---|---|
| `[AGENTS.md](./AGENTS.md)` | **动手前必读**。§C 十三条 DL 防错、§D 视觉参数定论与两层写法、§D.4 时间线四层归并、§G 十步自验 |
| `docs/spec/interfaces.md` | **唯一接口真源**：函数签名、§5.8 页面模块契约、§3 写者归属 |
| `../../AGENTS.md` | 工作区通用卡点 CP-1…CP-6 与 S1–S59（**只引用不复制**）|

### 2. 我要理解设计 → spec/

| 文件 | 管什么 |
|---|---|
| `docs/spec/requirements.md` | R1–R12 需求口径。**R11** 两层加工、**R12** 全量归网页 |
| `docs/spec/design.md` | 为什么一张表、幂等主键、切片算法、清洗 C1–C13、§5.1 渲染层为何不是第三数据层 |
| `docs/spec/data-sources.md` | 五源格式约定、设备注册规则、`device = ?` 禁令 |
| `docs/spec/tasks.md` | B0–B14 批次状态、已修缺陷、环境变量、待决项 |

### 3. 我要跑起来 / 接手 → 这两份

| 文件 | 管什么 |
|---|---|
| `docs/WORKFLOW.md` | 五阶段数据流：采集 → 切片 → 片层 → 日层 → 发布。每段讲输入输出、判定标准、四道前处理闸、三层兜底 |
| `docs/HANDOFF.md` | 接手第一件事：当前状态、日常重跑命令、五个别踩的坑、复盘索引 |

### 4. 我要查「某个结论是怎么来的」→ postmortem/

按时间归档，**只增不改**。每条结论（DL-1…DL-13、T27–T35）都能在这里找到实测过程。

| 文件 | 结论 |
|---|---|
| `postmortem/2026-10-23-delivery.md` | 2026-10-23 交付总结（9,679 片、各项提速）|
| `postmortem/perf-regression-2026-10-23.md` | DL-9：循环里放 SQL 导致 60 倍退化 |
| `postmortem/vision-perf-and-blind-fix-2026-10-23.md` | DL-10：盲总结 + max_tokens 步长（DL-12）|
| `postmortem/413-degenerate-image-2026-10-23.md` | DL-11：一张 1280×20 畸形图毒掉整片 |
| `postmortem/quota-and-ratelimit-2026-10-23.md` | DL-13：额度读不到，只能探；限流≠耗尽 |
| `postmortem/diary-style-2026-10-24.md` | 文体改造走过的弯路（含 631 片原文丢失）|
| `postmortem/incremental-and-no-store-2026-10-24.md` | 日报不入库、两层缓存、`--days` 增量 |

### 5. 我在做 phone-2 → 专项

| 文件 | 管什么 |
|---|---|
| `docs/phone-vision-runbook.md` | phone-2 视觉运行手册（reader="text"、并发、验收）|
| `docs/phone-vision-report-template.md` | 该专项的交付报告模板 |

### 6. 我在读写 vault 数据 → 数据视角

| 文件 | 管什么 |
|---|---|
| `../../04-Areas/数字人生/AGENTS.md` | 数据视角：路径分层、三层数据、§C-bis 网页包口径、D1–D11 防错 |

**分工原则**：工程视角（本项目）讲「怎么改不闯祸」，数据视角（vault）讲「东西该放哪、是什么」；
设计细节只在 `docs/spec/design.md`，任何一处改动都要同步检查这两份 AGENTS 的相关断言是否失效。
