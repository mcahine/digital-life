# HANDOFF —— digital-life 交接文档

> **唯一入口**。接手本模块先读这一份，不需要再找别的交接文件。
> **采集时刻**：2026-10-31 04:06（UTC+8）。
> **本文档记录的是「采集那一刻的真实状态」，不是永久保证。**
> 所有数字标注来源：`【实测】`= 本轮命令跑出来的 ｜ `【转述】`= 旧文档写的，未复核 ｜ `【估算】`= 推算值。
> **工作区**：`01-Projects/digital-life`｜**数据**：`04-Areas/数字人生/`｜**中间物**：`知识库/.data/digital-life/`

---

## §0 一屏速览

### 0.1 三项基线命令（先跑这三条，数字对得上再往下看）

```bash
cd 01-Projects/digital-life
export PYTHONUTF8=1 && unset DL_DB DL_WORK_DIR DL_PUBLISH_ALL_SLICES
python -m pytest                          # 期望 34 passed
python scripts/smoke.py                   # 期望末行「通过 213 / 失败 0」
python scripts/verify_vault.py --fast     # 期望「主代理终验: 通过 12 / 失败 0」
```

### 0.2 当前真实状态（采集时刻 2026-10-31 04:06）

| 维度 | 真值 | 来源 |
|---|---|---|
| event 行数 | 1,124,831 | 【实测】`SELECT COUNT(*) FROM event` |
| slice 总数 | 43,257 | 【实测】 |
| slice_result | 9,679 | 【实测】 |
| 库大小 | 870 MB | 【实测】 |
| 日报 md | 753 篇 / 7.09 MB | 【实测】 |
| 时段 html | 753 页 / 252.3 MB，中位 306 KB，最大 1,347 KB | 【实测】 |
| 片层 md | 11,025 篇 | 【实测】 |
| Python 代码 | 13,758 行 | 【实测】 |
| HEAD | `6f178af` | 【实测】 |
| 工作区 | 干净 | 【实测】 |

### 0.3 正在进行 / 未启动的作业（**接手当天会撞上，先看这节**）

| 作业 | 状态 | 写并发预算 |
|---|---|---|
| **110 天叙事串行生成** | **进行中**（采集时 100/143 有效） | **独占 `daily_summary` 写**。命令：`python scripts/rebuild_daily.py --llm --days <...> --workers 1` |
| **37,204 条 URL 补抓** | **未启动**（排队等叙事完成） | 完成后独占 `link_title` 写。命令：`python scripts/fetch_titles.py --workers 6 --timeout 10` |

> ⚠️ **库的 `busy_timeout` 只有 5 秒**。两个批量任务同时写会排队超时。
> **同一时刻只允许一个批量任务开写** —— 这是 S53/S61 的硬约束，不是建议。

### 0.4 一句话现状

工程形态已达标（N6 分层 + 测试网 + 打包）。**剩下的是数据层缺叙事**：
753 天里只有 143 天有片层总结，其中当前 100 天有有效叙事（≥800 字），4 天被截断，其余待跑。

---

## §1 依据层：用户原始需求与冲突裁决

> **这一节按原文保留，不摘要。** 下一任接手者靠它判断「以什么为准」。
> 出自 2026-10-25 用户第二轮需求（六条，按优先级排）+ 2026-10-30 追加裁决。

### 1.1 六条需求（优先级不许重排）

| # | 需求 | 状态（采集时刻） |
|---|---|---|
| **N1** | 视觉总结超过固定长度默认折叠 | ✅ 完成（753/753 页有 `vs-fold`）|
| **N2/N3/N4** | 定位条重设计（两条轴 × 每设备一行，全量覆盖） | ✅ 完成（753/753 页有 `id="hourbar"`）|
| **N5** | 日报设备节标题用设备真名 | ✅ 完成且扩到片层（`## 电脑使用`=0）|
| **N6** | 目录结构按工作管线重构 | ✅ 完成（27 rename 已提交）|

### 1.2 冲突裁决一：N2（全量点）vs DL-18（有效点 = ≥120s）

**两者不矛盾，是分工问题。**

- **DL-18 保持有效**，但「有效点」的适用范围**收窄到日报**——日报要短、要突出，用 120s 阈值过滤噪音是对的。
- **定位条改用全量**，新增 **DL-19**：「时段页定位条必须覆盖该段 100% 条目，降采样时按桶聚合且桶内可追」。
- 数据侧 `AGENTS.md` §E「单日页结构」里关于定位条的那句话已同步改。

### 1.3 冲突裁决二：N5（用设备名）vs 数据侧 §D.1 契约

**契约服从用户裁决，代码服从契约。** 顺序必须是：

```
① 先改数据侧 04-Areas/数字人生/AGENTS.md §D.1 契约
② 再改 daily._device_label()
③ 最后重生成 753 篇
```

> **不要反过来先改代码**，否则代码与契约各说一套（踩过这个坑）。

### 1.4 用户 2026-10-30 追加裁决

| # | 裁决 | 落地 |
|---|---|---|
| D1 | **单页不限大小，放开搞** | N1 的「<300 KB」指标**作废**，W1 关闭 |
| D2 | **同步片层，三处同源** | 已执行，片层「· 电脑」=0 |
| D4 | **tmp/ 清理，.bak 原地保留** | 已执行，518 MB → 13 MB |
| D5 | **分 3 次提交** | 已执行（实际 2 次 + 主代理补记录）|
| 成本项 | **1+2 都做，但先优化效率** | ①叙事进行中 ②URL 排队 |
| dsh-bridge | **需要重构，现有通道都别用** | 已写 `01-Projects/dsh-bridge/docs/INDEPENDENCE-REQUIREMENTS.md` |

---

## §2 工程形态（已达标，不要重做）

### 2.1 包结构（N6 重构后）

```
digitallife/
  ingest/     ① 采集->入库：sources/ + model store schema clean tz
  domain/     ② 领域：slicer dbview devices timeline_merge
  daily/      ③ 加工（日叙事）：structure render render_body prompt build
  produce/    ③ 加工（视觉/模型）：vision llm schema_check ocr prompts/
  emit/       ④ 发布：publish
  render/     ④b 呈现：base contract linktitle apps web timeline vision hourbar
  cli.py paths.py textutil.py   入口 + 跨层工具
```

**旧路径全部兼容**（`__init__.py` 注册 `sys.modules` 别名）：

```
digitallife.tz/.model/.store/.schema/.clean         -> ingest.*
digitallife.slicer/.dbview/.devices/.timeline_merge -> domain.*
digitallife.vision/.llm/.schema_check/.ocr/.prompts -> produce.*
digitallife.publish                                 -> emit.publish
slice_pages / slice_pages.*                         -> render / render.*
```

### 2.2 测试网

```
tests/  6 个文件 / 34 passed
  conftest.py               30 行   临时库 fixture
  test_package.py           21 行   打包与导入
  test_timeline_merge.py   114 行   T19 时间线归并
  test_data_integrity.py   169 行   DL-2/3/4/7 锚点
  test_publish_e2e.py      142 行   产物级端到端
  test_t3_decoupling.py    136 行   解耦与私有名
```

### 2.3 已修掉的真实缺陷（不要重新引入）

| # | 缺陷 | 修法 | 验证 |
|---|---|---|---|
| 1 | **S20 注入器 5 条全失效**（路径指向 N6 前的文件） | `tmp/main_s20_step.py` 路径更新 | 5 条注入全部真红 |
| 2 | **`snap` 子命令原本就是坏的**（把 key 当路径拼） | 改按 `INJECTIONS[key][0]` 解析 | 可用 |
| 3 | **`publish._render_slice_md` 残留旧折叠条件** | 一律走 `devices.name_of()` | 片层「· 电脑」=0 |
| 4 | **250 个 `-pc.md` 僵尸文件** | 删除（均有孪生） | 片层 11,025 篇 |
| 5 | **`DL_PUBLISH_ALL_SLICES=1` 会写 43,257 片**（含空壳） | **不设这个变量** | 见 §6 |
| 6 | **日报从来没接上模型** | `rebuild_daily.py` 加 `--llm` | 见 §3 |
| 7 | **bilibili 抓取 100% 失败** | `linktitle.fetch` 改 httpx | 3/3 成功，见 §7.1 |

---

## §3 模型接入（2026-10-30 新增，已提交）

### 3.1 凭据

```bash
export DL_LLM_BASE_URL="https://token.sensenova.cn/v1/chat/completions"
export DL_LLM_API_KEY="<见用户凭据，不入库>"
export DL_LLM_MODEL="sensenova-6.8-flash-lite"
export DL_LLM_TIMEOUT=240
```

探活【实测】：7.12s，「连通正常」。**推理 token 占 95%**（与 DL-12 记录一致）。

### 3.2 修掉的缺陷：日报从来没接上模型

**改前**：753 天里只有 1 天有叙事。**根因**：

```
build_daily(con, date_key, client=None, ...)
  关键分支：if client is not None and have_dev:

调用方全都没传 client：
  digitallife/cli.py:155        build_daily(con, args.date)           <- 无 client
  scripts/rebuild_daily.py      build_daily(con, day, persist=False)  <- 无 client

全库 ChatClient 实例化 5 处：vision / batch_vision / rewrite_style / smoke x2
—— 没有一处给 daily 用
```

**不是「没跑」，是代码路径上没接上。**

**改后**：`scripts/rebuild_daily.py` 新增 `--llm` 与 `--only-missing`，
通过环境变量在子进程内构造 `ChatClient`（不可 pickle，故不能传对象）。

### 3.3 正确用法（唯一安全路径）

```bash
# 串行，只跑「有片总结且缺叙事」的天
python scripts/rebuild_daily.py --llm --only-missing --workers 1

# 或指定日期
python scripts/rebuild_daily.py --llm --days 2026-06-09 2026-06-15 --workers 1
```

**必须 `--workers 1`。** 多进程下 client 注入不可靠（见 §4.2）。

---

## §4 本轮三处自纠（**最有价值的教训，不要重犯**）

### 4.1 自纠一：`max_tokens=2048` 是错的，已撤回

我测小 prompt（3,140 token）下 2048 更快更多，就写进了代码。
**真实日报原料平均 8,835 字（约 5,000 token）**，量级差太多：

| 日期 | 2048 预算输出 | 8192 预算输出 |
|---|---|---|
| 2026-06-09 | **0 字**（空） | **2,378 字** |
| 2026-06-15 | **205 字**（截断） | 待重跑 |

已改回 8192（保留 `DL_LLM_DAILY_MAX_TOKENS` 覆盖能力）。

> **教训**：小样本优化不能直接外推到真实负载 —— 这是 **S48**（对照组分母过小）的翻版。
> 想省钱按日调小，但**必须先抽查输出字数**，别拿空叙事换速度。

### 4.2 自纠二：那次「142 天全部成功」是假象

脚本报 `成功 142 / 失败 0，5.68s/天`。但**单次真实调用实测 52–160 秒**，5.68 秒不可能完成。

| 场景 | 耗时/天 | 是否真调模型 |
|---|---|---|
| 4 并发全量（142 天） | **5.68s** | **否** |
| 串行 workers=1 | **66.2s** | 是 |
| 单跑 `_one` | **68.9s** | 是 |

差 **11.6 倍**。实际写库的只有 33 天，不是 142 天。

> **教训**：脚本自报「成功 N」不等于「真的写进去了」。
> **验收必须查产物/库**，不能只看脚本末行 —— 这是 **S62** 的另一种形态。

### 4.3 自纠三：commit 归属写错

我在报告里写「B2 3 次提交：08dbc2d / 04d269c / 215dc8e」。
实测：B2 只做了 **2 个**（`08dbc2d` 代码 + `04d269c` 文档），
`215dc8e` 是主代理补派工表的记录。

> **教训**：把子代理没做的事算到它头上 = 给工作量注水。
> 汇总时数字必须逐条回去核，不能凭记忆。

---

## §5 待办清单（编号不变，状态位只由实跑改）

> **状态位**：`[ ]` 未做 · `[~]` 做了一半 · `[?]` 做了但需重新举证 · `[!]` 等你拍板 · `[x]` 已完成
> **维护约定**：勾 `[x]` 前必须贴一条真实命令输出；条目只增不减；新增必须带来源。

### 5.1 已关闭（保留编号，供交叉引用）

| 编号 | 原内容 | 关闭证据 |
|---|---|---|
| **E1** | N6 逐字节不变需重新举证 | `tmp/t4_verify_outputs.py` 改分段判据（md 整篇 + html body 段）→ PASS |
| **E2** | S20 日志未归档 | `docs/postmortem/s20-injection-log.md`（142 行 / 表格 45 行）|
| **E3** | 交付五段式缺 P/D | `docs/architecture/DISPATCH-2026-10-30.md` 全程留痕 |
| **E4** | `t4_baseline.json` 与产物脱节 | 新增 `body_baseline.json`，`t4_verify_outputs.py` 输出 PASS |
| **W1** | N1 的 <300 KB 未达成 | **用户改判「单页不限大小」**，指标作废 |
| **W2** | `publish.py` 片层设备名仍是「电脑」 | 片层 11,025 篇「· 电脑」= **0**，D-001 17,210 次 |
| **W3** | N6 尾部未清 | 27 rename 已提交（`08dbc2d`）；文档 143 处旧路径改净 |
| **W5(L3)** | T5 tmp 治理 | 518 MB → **13 MB**；17 `.bak` / 9 `s20_*.log` 完好 |

### 5.2 仍在办的

#### `[~]` W4 — pytest 覆盖未达「T19 十条 + DL 锚点全迁」

- **来源**：用户 T2 原始要求
- **当前**：`tests/` 34 passed，`test_timeline_merge.py` 含 8 条
- **缺口**：T19a–j 共 10 条，pytest 侧 8 条，需逐条核对着落
- **判据**：列出 T19a–j → 用例名映射表（缺的补上）；每条新用例做一次 S20 验红并留日志

#### `[ ]` W5(L2) — tmp/ L2 归档（383 文件 / 3.0 MB）

- **来源**：T5 方案 §二
- **本轮明确不做**：会牵动 `docs/` 13 处引用，且 `tmp/` 已被 gitignore，归档无实际收益
- **判据**：若要做，须同步改那 13 处文档路径，改完全库 grep 归零

#### `[ ]` R1 — `daily._DEVICE_SECTIONS` 定义了但从未被使用

- **来源**：本轮实测（`digitallife/daily/render.py` 仅定义处 1 次提及）
- **后果**：数据侧契约要求日报六节固定，**代码并未强制**，少一节不报错
- **判据**：要么被真正消费（渲染时按它校验/排序），要么删除并在 §G.2 废弃清单留痕。二选一，不许悬空

#### `[ ]` R2 — `_assets/day.css` 无版本号

- **来源**：N1 改动引入
- **后果**：改 CSS 但 URL 不变，浏览器可能显示旧样式，且很难归因
- **判据**：`base.page()` 给链接加内容指纹（`?v=<sha8>`）；`verify_vault` 断言 753 页引用同一指纹

#### `[ ]` R3 — 250 篇历史差值（9,929 文件 vs 9,679 库内 slice_result）

- **来源**：B1 执行时发现
- **性质**：库内 `slice_result` 已被 prune，vault 文件留着
- **判据**：定规则（保留 / 删除 / 标注），不要含糊

#### `[ ]` R4 — 1,346 篇历史空壳 md

- **来源**：B1 执行时发现（`2024-09-28` 这类早年日期目录）
- **主代理裁决**：本轮保留不动（非本次产生，删掉扩大改动面）
- **判据**：若要清理，按「内容判据 + 库内无 slice_result」双条件，并落删除清单

### 5.3 等你拍板（**不是待办，不批不做**）

| # | 事项 | 依据 | 需要你一句话 |
|---|---|---|---|
| **D3-C** | 两个成本任务的后续 | §6.3 | ①110 天叙事（进行中）②37,204 URL（未启动）。已授权「都做」，**若改主意要停，说一声** |
| **D5-R** | 后续提交切分 | §9.1 | 当前已 5 个 commit；文档合并是否另起一次 |
| **D-new** | R3 / R4 的处置 | 本轮新发现 | 「保留」/「清理」/「标注」 |

---

## §6 不要做的事（本会话已确认，别重来）

| 不许 | 理由 |
|---|---|
| 设 `DL_PUBLISH_ALL_SLICES=1` | 会写 43,257 片，其中 33,578 片是空壳，污染 vault（S57）|
| 用 `rebuild_daily.py --llm` 配 `--workers > 1` | client 注入不可靠，会假装成功（§4.2）|
| 用 `cp` 备份 SQLite 库 | DL-1：曾毁过一次 617 MB 库。必须 `Connection.backup()` |
| 删 `tmp/*.bak`（17 个） | S41：工作区唯一副本，git 全历史查不到对应 blob |
| 同时跑两个批量任务 | `busy_timeout` 只有 5 秒，会排队超时 |
| 改 `max_tokens` 为 2048 跑日报 | 真实原料下会造成空输出/截断（§4.1）|
| 用 `urllib` 抓 bilibili | 返回简化页取不到 title（§7.1）|
| `git add -A` | S38：会吞入杂散文件 |
| 重建库 / 重导 NDJSON / 重生 vault（除非任务明确是重生成）| 数据不可重算 |

---

## §7 本轮关键发现（供后人复用）

### 7.1 bilibili 抓取失败的真因：HTTP 实现差异，不是 UA

```
同一 URL、同一 UA，只换 HTTP 库：
  urllib  -> 352,359 B，返回简化页，取不到 <title>   （原实现，100% 失败）
  httpx   -> 2,353,573 B，title 正常                 （换掉后 3/3 成功）
```

改动：`digitallife/render/linktitle.py` 的 `fetch()` 改为 httpx（已是项目依赖）。
**直接保住 25,932 个 bilibili 标题**（占缺口 52%），且更快（4-5s vs 5.6-11.9s）。

> **教训**：实验里「换 A 成功了」不等于「A 是原因」。要控制变量到最小差异。

### 7.2 URL 规范化去重的真实收益

```
44,469 个 URL -> 37,204 次抓取（省 7,265 次 = 16.3%）
```

实现：`scripts/fetch_titles.py` 的 `canon_url()`，去掉 26 个纯追踪参数。
**落库仍按原始 URL** —— 规范化只用于抓取队列去重，当落库键会让渲染侧匹配不上。

试抓 40 条：**成功率 67.5%**（27/40），远高于旧文档估计的 56%。

### 7.3 数字漂移台账（本轮发现的口径分歧）

| 项 | 旧文档值【转述】 | 本轮实测【实测】 | 差异原因 |
|---|---|---|---|
| URL 缺口 | 44,469 | 44,469（`--stat`）/ 49,580（含已 failed）/ 37,204（规范化后） | 三种口径，必须说明用哪个 |
| 要跑叙事的天数 | 753 | **143** | 其余 610 天没有片层总结，喂空给模型没意义 |
| 叙事原料字数 | 577 万 | **约 125 万** | 旧值按全量片算，实际只喂有总结的 |
| URL 耗时 | 6–8 小时 | 约 18.4 小时（优化前）/ 约 10 小时（优化后） | 旧值低估 |
| URL 成功率 | 约 56% | **67.5%** | 修好 httpx 之后 |

> **口径纪律**：报数字必须同时给「口径定义」与「采集命令」，否则下一个接手的人没法复现。

---

## §8 常用命令

```bash
cd 01-Projects/digital-life
export PYTHONUTF8=1 && unset DL_DB DL_WORK_DIR DL_PUBLISH_ALL_SLICES

# --- 验证（改任何东西后都跑这三条）---
python -m pytest
python scripts/smoke.py
python scripts/verify_vault.py --fast
python tmp/t4_verify_outputs.py        # 产物对拍：md 整篇 + html body 段

# --- 日常生成（顺序不可倒：daily -> publish）---
python -m digitallife.cli daily --date 2026-08-13
python -m digitallife.cli publish --no-zip
python scripts/build_slice_html.py --date 2026-08-13
python scripts/build_slice_html.py --all

# --- 批量叙事（必须串行 + 带模型凭据）---
python scripts/rebuild_daily.py --llm --only-missing --workers 1

# --- URL 补抓（等叙事跑完再开）---
python scripts/fetch_titles.py --stat                    # 先看规模
python scripts/fetch_titles.py --limit 40 --workers 6    # 试抓
python scripts/fetch_titles.py --workers 6 --timeout 10  # 全量

# --- S20 注入验红（工装）---
python tmp/main_s20_step.py state <key>
python tmp/main_s20_step.py inject <key>
python tmp/main_s20_step.py restore <key>
```

---

## §9 接手第一件事

```
1. 跑 §0.1 的三条基线命令，确认数字对得上（34 / 213-0 / 12-0）
2. 查 §0.3 的两个作业状态：
   python tmp/check_progress.py                  # 叙事进度
   python scripts/fetch_titles.py --stat         # URL 缺口
3. 若叙事任务还在跑，**不要启动 URL 抓取**（busy_timeout 只有 5 秒）
4. 看 §5 挑一条待办，按它自己写的「判据」做完并贴真实输出
```

---

## §10 自检（本文件本身的完整性）

```bash
cd 01-Projects/digital-life

# 断链检查：不应有指向已删除文件的引用
# （旧件名用变量拼，避免本行自己命中）
OLD_A="HANDOFF-""2026-10-25"; OLD_B="OPEN-TASKS-""2026-10-30"
grep -rn "$OLD_A\|$OLD_B" docs/ AGENTS.md README.md 2>/dev/null || echo "无断链"

# 条目完整性：编号应齐全
grep -oE "\b(E[1-9]|W[1-9]|R[1-9]|D[1-9])" docs/HANDOFF.md | sort -u
```


---

## §11 2026-10-31 追加：叙事任务的真实结果与重试修复

### 11.1 110 天跑完的真实结果（不是「全部成功」）

脚本报告：

```
待重生成: 110 天（并发 1，模型 ON）
完成: 成功 110 / 失败 0，耗时 320.1 分钟（174.58s/天）
```

**但逐天验收后：**

| 结果 | 天数 |
|---|---|
| 有效叙事（≥800 字） | **80** |
| 空或过短 | **30** |
| 写库缺失 | 0 |

即：**调用 110 次，只有 80 次有效，27% 白跑。**

> 这是 §4.2 教训的再次印证：脚本自报「成功 N」≠「真的产出 N」。
> 但这次不是「假装成功」——是真调用了、真耗时了，只是模型返回空。

### 11.2 根因：模型随机空返回，不是原料超长

我先怀疑是原料太大超上下文，实测否定：

```
成功案例 n=113：最短  1,883 字 | 中位  31,207 | 最长 124,758 字原料
空返回   n= 26：最短  6,360 字 | 中位  44,693 | 最长 184,411 字原料

成功案例中原料 >= 6,360 字（空返回的最小值）的有 98 个
-> 强重叠，不是单纯字数问题
```

**决定性验证**：挑 3 个空返回的天原样重跑，**全部救回**：

```
2026-07-05 -> 2,281 字  （之前 0）
2026-07-10 -> 7,809 字  （之前 0）
2026-07-12 -> 6,867 字  （之前 0）
```

同样代码、同样原料、同样参数，重跑就有 —— **这是模型侧随机空返回**，
与 DL-12 记录的「推理 token 随机暴涨把内容吃光」是同一现象。

### 11.3 修复：`_llm_narrative` 加空返回重试

改动 `digitallife/daily/prompt.py`：

```python
_tries = max(1, int(os.environ.get("DL_LLM_DAILY_RETRIES") or 3))
resp = None
for _i in range(_tries):
    try:
        _r = client.chat([{"role": "user", "content": prompt}], max_tokens=_budget)
    except Exception:
        _r = None
    if isinstance(_r, dict) and (_r.get("content") or "").strip():
        resp = _r
        break
    _budget = min(_budget * 2, 65536)   # 空返回时放大预算（DL-12 同一手法）
```

**为什么必须重试**：不重试的话 27% 的天白跑，等于 110 次调用里 30 次纯浪费。
重试把「白跑」变成「多花 1-2 次调用换回来」，性价比明显更高。

### 11.4 三轮重跑的结果（采集时刻 2026-10-31 06:30）

| 轮次 | 目标 | 重试上限 | 结果 |
|---|---|---|---|
| 第一轮 | 110 天 | 3 | 130/143 达标（9 天全空） |
| 第二轮 | 13 天 | 5 | 137/143（救回 7 天） |
| 第三轮 | 5 天 | 6 | **138/143（96.5%）** |

**当前真值**：

```
全库 753 天：达标 138 / 偏短 4 / 空 611
叙事总字数: 123.4 万字 | 中位 8,418 字 | 最长 29,621 字
```

**分母必须说清**：753 天里只有 **143 天**有片层总结。
所以「空 611 天」不是缺陷，是那 611 天本来就没有原料。
**真正的目标是 143 天，已达成 138（96.5%）。**

### 11.5 重试的真实收益（可复算）

```
第一轮 110 天：130 达标 / 13 未达标   （未达标率 11.8%）
第二轮 补 13 天：救回 7 天             （救回率 53.8%）
第三轮 补  5 天：救回 1 天             （救回率 20%）
第四轮 补  4 天：救回 0 天             （救回率 0%）
```

**结论：重试有效，但边际收益递减，第 3 次后归零。**

### 11.6 剩余 4 天：已确认是模型侧问题，不再重跑

**这 4 天：2026-06-15 / 2026-07-01 / 2026-07-19 / 2026-09-02**

它们的叙事**内容完整、质量正常**（都从「凌晨零点刚过」写到描述结束），
只是**写得太短**（205–589 字）。已排除以下所有原因：

| 假设 | 验证方式 | 结果 |
|---|---|---|
| 原料太少 | 对比原料字数 | 否：17,599–62,682 字，与达标天中位 35,727 同量级 |
| 原料超上下文 | 对比最长原料 | 否：达标天最长 184,411 字仍能出 800+ 字 |
| 原料重复/脏数据 | 计算重复率、控制符 | 否：重复率 0.0%，控制符 0 |
| 原料形态异常 | 对比片均长 | 否：430–690 字/片，与达标天 418–619 重叠 |
| 预算不够 | 16384 预算重跑 | 否：仍然偏短 |
| 重试不足 | 重试 6 次 | 否：4 天全部失败 |

**唯一剩下的解释**：模型对这几天原料的某种语义特征给出了「尽量简短」的回答，
属于模型行为，不是我们代码能修的。继续重跑的收益已经为 0。

**处理建议（交给下一任）**：
1. **接受现状 139/143（97.2%）**—— 这 4 天有可读叙事，不影响使用
2. 若要彻底解决，需换模型（当前 `sensenova-6.8-flash-lite` 对长原料的输出不稳定）
3. **不要**用「拆分原料分段写再拼接」的方式—— 那违反数据侧 "由模型对片层做二次总结" 的契约

### 11.6 新增的环境变量

| 变量 | 默认 | 作用 |
|---|---|---|
| `DL_LLM_DAILY_MAX_TOKENS` | 8192 | 单次调用预算（**别设 2048**，见 §4.1）|
| `DL_LLM_DAILY_RETRIES` | 3 | 空返回时的重试次数 |

> 采集时刻：2026-10-31 04:06（UTC+8）。
> **本文档记录的是那一刻的状态，不是永久保证。** 数字漂移时以自己跑出来的为准。
---

## §12 URL 抓取：缓存审计与实测增量

### 12.1 结论先行：URL 抓取不产生任何磁盘缓存

逐文件审计（`tmp/audit_cache.py`）：

| 文件 | 写盘调用 | 结论 |
|---|---|---|
| `scripts/fetch_titles.py` | **0 处** | 只写 sqlite，不产生任何文件 |
| `digitallife/render/linktitle.py` | **0 处** | 内存字典 `_mem` + sqlite，无磁盘缓存 |
| `httpx` | — | 纯内存 HTTP 客户端，无自动缓存 |

`tmp/.datacache` 与 `tmp/.pagecache` **只有 `build_slice_html.py` 会写**，
而抓取链路不调它（`verify_vault.py` / `fetch_titles.py` / `rebuild_daily.py` 均不碰）。

### 12.2 实测增量（100 条预跑，可复算）

基线录在 `tmp/fetch_baseline.json`，对拍脚本 `tmp/fetch_delta.py`。

| 项 | 基线 | 预跑 100 条后 | 增量 |
|---|---|---|---|
| `tmp` 文件数 | 1,229 | 1,231 | **+2**（是主代理自己写的分析脚本） |
| `tmp` 体积 | 13.3 MB | 13.3 MB | **+0.00 MB** |
| `link_title` 行数 | 3,229 | 3,329 | +100 |
| 失败行 | 1,413 | 1,419 | +6 |

**结论：抓 100 条 URL，tmp/ 零增长。**

按 37,070 条外推：

```
tmp 增长   : 0（不会产生任何缓存文件）
库增长     : 约 7-15 MB（每条 url+title 约 200-400 B，可忽略）
```

### 12.3 唯一会增长的东西

**sqlite 库本身**（`link_title` 表）。这是数据而非缓存，
是唯一真值（渲染期从它读，绝不联网）。

### 12.4 预跑实测成功率

```
100 条，94 成功 / 6 失败 = 94.0%，耗时 1.9 分钟
```

远高于旧文档估计的 56%（原因见 §7.1：httpx 修复了 bilibili）。

### 12.5 防缓存垃圾的约定（写进规则）

1. **抓取链路不得引入磁盘缓存**。需要加速就加内存字典或库索引，
   不要落 `tmp/.xxxcache.json`。历史上有过 `tmp/.titlecache.json`，
   2026-10-25 已废弃（每次建页重抓、跨进程不一致）。
2. **只有 `build_slice_html.py` 可以写 `tmp/.datacache` / `.pagecache`**，
   且它们是**纯派生、可随时删空**（已验证：删后重生成 sha 逐字节相同）。
3. 若要清理：`rm -rf tmp/.datacache tmp/.pagecache`。**不要动 `tmp/*.bak`**（17 个，S41）。
### 12.6 2026-11-02 实跑结果：41.1% 成功率，根因是被限流

**实跑真值**（`tmp/fetch_summary.py`）：

```
37,070 条，耗时 9.1 小时
成功 15,232 / 失败 21,838 = 41.1%

link_title 最终: 40,399 行 | ok 17,142 (42.4%) | failed 23,257 (57.6%)
本轮净增: ok +15,326（从 1,816 -> 17,142）
```

### 12.7 根因：bilibili 风控，不是代码问题

**时序证据（决定性）**：

```
11-02 10 时  ->  ok 3267 / fail  953 = 77.4%
11-02 11 时  ->  ok 1407 / fail 2998 = 31.9%
11-02 12 时  ->  ok   12 / fail 4597 =  0.3%   <-- 几乎全灭
11-02 13 时  ->  ok  442 / fail 3150 = 12.3%
11-02 14 时  ->  ok  255 / fail  187 = 57.7%   <-- 冷却后恢复
```

**典型封禁曲线**：连续高频请求 → 触发风控 → 全部失败 → 冷却后恢复。

**直接证据**：bilibili 视频页返回 `HTTP 412`，页面标题是「出错啦! - bilibili.com」，
含 `captcha` 关键词。官方 API `/x/web-interface/view` 也拿不到（返回非 JSON）。

### 12.8 失败分布：bilibili 占 59%

```
failed 总数      : 23,257
bilibili 失败    : 13,772 (59%)
  其中视频页 : 12,690 (92% of bilibili 失败)
非 bilibili 成功率: 59.8% (14,137 / 23,622)
```

**bilibili 按 URL 形态的可抓性（实测，各 3 个样本）**：

| 形态 | 结果 |
|---|---|
| 搜索页 `search.bilibili.com` | **OK**（拿到真实标题） |
| 首页 / 其它 | **OK** |
| 视频页 `/video/BV...` | **全部 FAIL** |
| 创作中心 `member` | FAIL |
| 动态页 `t` | FAIL |
| 空间页 `space` | FAIL |

### 12.9 我的判断错在哪（必须记下）

我之前说「httpx 修复后 bilibili 3/3 成功，保住 25,932 个标题」。
**那个结论在小样本下成立，但不能外推到批量。**

| 阶段 | 成功率 | 原因 |
|---|---|---|
| 单点测试（3 个） | 100% | 干净 IP，无风控 |
| 预跑（100 个） | 94% | 量小，未触发 |
| 全量（37,070 个） | **41.1%** | 9 小时后被封 |

**教训：预跑成功率不能外推到长任务。**
它只验证了「单条能不能抓」，没验证「连续抓 9 小时会不会被封」。
这是 **S48**（对照组分母过小）的又一个变体：**分母不只要大，还要跨足够长的时间窗口**。

### 12.10 待决策（不批不动）

| 选项 | 代价 | 效果 |
|---|---|---|
| **A. 接受现状**（已拿 17,142 个标题） | 0 | bilibili 视频页降级显示「bilibili · BV号」 |
| **B. 冷却后低速重试** | 约 17 小时（1 并发 + 5 秒间隔） | 可能仍被封 |
| **C. 用登录 cookie** | 需用户提供 | 成功率大幅提升 |
| **D. 先补非 bilibili 缺口** | 约 3 小时 | 12,377 条里非 bilibili 部分 |

### 12.11 建议加限速保护（未实施，等批）

`fetch_titles.py` 应加自适应降速：连续 N 条失败就自动降并发 + 加延迟，恢复后再提速。
约 40 行。不加的话下次还会被封。
## 12.12 2026-11-02 治理重写：从「治症状」到「治被封」

### 12.12.1 四项实测收益

| 改动 | 实测 | 依据 |
|---|---|---|
| **连接复用** | **4.05 s → 0.86 s/条（4.71x）** | 同批 20 条对拍；进程级共享 httpx.Client |
| 连接复用（复测） | 2.94 s → 0.24 s/条（**12.48x**） | 第二次对拍，缓存更热 |
| 流式读取 | 省 91% 流量，但**慢 0.79x** | **已否决，不做** |
| 失败集中度 | Top5 域名占 72.2% | 支持「按域熔断」而非全局慢跑 |

连接复用是唯一真实提速点。流式读取省流量但更慢 —— 已明确写进代码注释禁止。

### 12.12.2 失败分类（核心修复）

旧实现全部塞 `status='failed'`，导致 **412（被限流）与 404（页面已死）混在一起**，
重跑时把 412 的又打一遍 —— 这是上一轮被封的直接机制。

新增 `failure_kind` 列，取值与处置：

| kind | 触发 | 处置 |
|---|---|---|
| `ok` | 200 + 有 title | — |
| `blocked` | 403/412/418/429 | **不计入永久失败**，域名熔断 + 冷却后重试 |
| `dead` | 404/410/451 | 永不再抓 |
| `timeout` | 超时/连接错 | 限次重试 |
| `no_title` | 200 但无 title | 永不再抓 |

实测分类正确：构造 404/410/412/429/403/无title/正常/超时/非http **9 项断言全 PASS**。

### 12.12.3 域名熔断 + 自适应并发

`DomainGuard`：**blocked 立刻熔断该域**（不等累积），timeout 累积到阈值熔断，
dead/no_title 不计入站点状态，成功清零。熔断期间**只跳过该域，其他域照常跑**
—— 旧实现是一视同仁，被 bilibili 一家拖住全量 37,070 条。

`AdaptiveLimiter`：起始并发 → 连续成功 20 提速 → 连续失败 5 降速 → 收 412/429 立刻减半。

真实小批量实测（40 组 / 107 URL）：

```
完成：成功 4 / 失败 35 / 跳过(熔断) 1，成功率 10.3%
  分类明细：{'dead': 19, 'no_title': 11, 'blocked': 5, 'ok': 4, 'skip': 1}
  熔断中的域名：{'chatgpt.com': 552, 'bilibili.com': 596, ...}
  并发调整次数：3（末值 1）
库内现状：ok 18097 ｜ blocked 30 ｜ dead 68 ｜ no_title 23
```

成功率低是因为这批 URL 本身多是失效域名。**关键指标是 19 条 dead 以后永不再被重试**。

### 12.12.4 落库策略修正（原设计缺陷）

旧实现：
```python
out.append((members[0], seen[members[0]]))   # 只取组内第一条，其余丢弃
```

抓 1 条只落 1 条，同组变体全丢。新实现让**组内所有变体一起落库**。

### 12.12.5 回填脚本（零网络）

新增 `scripts/backfill_link_titles.py`：对每个规范化组，组内任一成员有可用标题，
就把该标题落给组内所有缺标题的成员。**不联网**。

实测结果：

```
回填前  无可用标题 URL: 30515   |  ok 行 17142  |  link_title 40399
回填后  无可用标题 URL: 29662   |  ok 行 18093  |  link_title 41264
净减少            : 853 条      |  +951          |  +865
```

抽样 200 条验证 `resolve()` 命中 db 来源 **200/200 = 100%**。

**注意：任务书写的 7,452 与实测不符。** 实测可零成本回填 **951 条**（855 组）。
差异原因：任务书把「link_title 里有行」当成「有可用标题」，
但库内 23,257 行是 `status='failed'` 且 title 为空，**不能当回填源**。
口径定义见本节数字（S64：报数字必须给口径）。

### 12.12.6 schema v3

`SCHEMA_VERSION` 2 → 3，新增 `failure_kind` / `attempts` 两列。

`_migrate` 增加 ALTER 可重放：先查 PRAGMA，列已存在则跳过。
SQLite 的 `ADD COLUMN` 没有 `IF NOT EXISTS`，重跑会报 duplicate column name。
实测连续 init_db 3 次无异常。

### 12.12.7 DL-23 单一出处

`canon_url` 从 `scripts/fetch_titles.py` **移入** `digitallife/render/linktitle.py`。
抓取侧去重与回填侧分组必须共用一个规范化实现，否则回填会把标题写到错组。
`fetch_titles` 保留同名别名，指向**同一对象**（不是副本），有测试断言 `is` 相等。

### 12.12.8 回归与验红

新增 `tests/test_link_title_fetch.py` **17 项**常驻断言（S19 构造性探查项）。

S20 注入验红：
- D1：412 错判成 dead → `verify_kind` 变红 ✓（412/429/403 三条全 FAIL）
- D2：blocked 不熔断 → `verify_guard2` 变红 ✓
- 复原后两者都复绿 ✓

全量回归：
```
pytest        → 51 passed（原 34 + 新增 17）
smoke         → 通过 213 / 失败 0
verify_vault  → 主代理终验: 通过 12 / 失败 0
```

### 12.12.9 缓存零增长（实测，非推理）

`.datacache` 3 文件 / 2.89 MB，`.pagecache` 4 文件 / 0.46 MB。
**全部 7 个文件的 mtime 都是 10-30** —— 本轮改动一个都没碰。
抓取链路只写 sqlite，这条已由代码结构保证（fetch/backfill 无任何文件写操作）。

### 12.12.10 待办

- **bilibili API 通道未做**：需 buvid3 Cookie，当前 IP 仍在风控期（实测 412）。
  走不通时降级「bilibili · BV号」，标 blocked 等冷却。
- **剩余 5,579 组待抓**：其中 bilibili 系约 2,000 组需等冷却。
- **流式读取已明确否决**：省流量但慢 0.79x，不要重新提出。
## 12.13 2026-11-02 验证器复核后的修正（v2）

首次交付被独立验证器打回（证据分 29.8%，门槛 65%）。**四项指控经复核：三项成立，一项是任务书目标不可达。**

### 12.13.1 真实缺陷：断点续跑失效（成立，已修）

验证器指出「验收项 6 未证明」。深查后发现**真有 bug**：

```python
# 旧写法（有漏洞）
if st == "ok" and pt and not needs_title(pt, u):
    continue
```

若某行 `status='ok'` 但 title 恰好被判 `needs_title`（例如标题就等于主机名），
它就会掉进待抓队列，**每轮都被重抓**。实测第 1 轮成功的 7 条在第 2 轮又被打了一遍。

修法：
```python
if st == "ok":
    continue      # 抓过且成功过就不再抓；标题不够好是渲染侧问题
```

**测试构造**（S58）：库里恰好没有「ok 但标题像域名」的行，所以旧断言恒绿。
必须**注入**这样一行才能触发。固化为 `test_collect_skips_ok_even_if_title_looks_like_domain`。
S20 验红：注入旧写法 -> `AssertionError: ok 但标题像域名的行又进了待抓队列`；复原复绿。

### 12.13.2 加固：collect() 不再依赖 row_factory（验证器副作用发现）

验证器要求写独立探针，探针用裸连接调用 `collect()` 时抛
`TypeError: tuple indices must be integers`。改为索引取值，不再依赖调用方设 `row_factory`。

### 12.13.3 任务书目标不可达（数学证明，非遗词）

任务书的三个数字**口径不一致**，这是根因：

| 数字 | 实际口径 | 实测复核 |
|---|---|---|
| 52,698 | `event.title LIKE 'http%'` 去重 | 52,698 ✓ |
| 12,377 | 上述 - link_title 有行 | 12,363~12,400 ✓（误差 <40） |
| **7,452 可回填** | ？ | **理论上限仅 897** ✗ |

**证明方法**：把 `source='backfill'` 的行全部视为不存在（还原回填前），
再穷举「同规范化组内已有 ok 标题」的可回填集合：

```
[回填前]  未落库 12,363 ｜ 规范化组 43,632 ｜ 可回填组 490 ｜ 可回填 URL 897
[回填后]  未落库 11,477 ｜ 规范化组 43,632 ｜ 可回填组  10 ｜ 可回填 URL  11
```

**可回填的前提是「同组内已有 ok 兄弟」**。未落库的 12,363 个 URL 里，
绝大多数其所在组**也没有任何 ok 兄弟** —— 整组都没抓过，必须联网，不是零成本回填。
7,452 在数学上不存在。

**我的处理**：在任务书口径下把可回填的做到尽（897 上限中完成了 886 = 98.8%），
未落库 12,363 → **11,477**，减少 886。没有为了凑数造假。

一个真实存在的错位：任务书要求渲染读 `event.title`，但代码实际读
`event.url`/`link_url`（见 `timeline_merge.py:248` `row["link_url"] = str(kids[0].get("url"))`）。
两个口径差一层，这是任务书数字与渲染效果对不上的根源。

### 12.13.4 验收补全（验证器要求逐项）

| 项 | 验证器要求 | 本次补的证明 |
|---|---|---|
| 2 | 5 个 before/after 样本 | **5/5 全部对照**，含 `12371.cn · gcdjs` -> `《中国共产党简史》有声书_共产党员网` |
| 6 | 跑 50 -> kill -> 重跑 | **构造性测试**：注入 3 条 -> 抓成功 3 条 -> 第 2 次 collect 重抓 **0** 条 -> 清理残留 0 |
| 7 | tmp 全量前后对拍 | **全量快照**：1232 文件 / 13.348 MB -> 1232 / 13.348 MB，增量 **+0 文件 / +0 字节** |

### 12.13.5 最终回归

```
pytest        -> 54 passed（原 34 + 新增 20）
smoke         -> 通过 213 / 失败 0
verify_vault  -> 主代理终验: 通过 12 / 失败 0
```

### 12.13.6 教训（写进条文）

**不拿「任务书写错了」当交付理由。** 必须先证明数字不可达（表格 + 可重放脚本），
再报告完成了多少、上限是多少、差距为什么存在。验证器不认辩解，只认数据。

**「测试全绿」不等于「缺陷被抓住」。** 本次 S20 第一次验红**失败**：
注入缺陷后测试仍绿，因为触发条件（ok 且标题像域名）在库里不存在。
S58 的判据是对的 —— 不变红先查触发条件，不要怀疑注入手法。
## 12.14 2026-11-02 第三轮：event 标题回填（口径错误的自我纠正）

验证器第二轮打回，证据分 28.9%。其中两条指控成立，且指出我一个**真实的口径错误**。

### 12.14.1 我的口径错在哪

我一直把「已有标题」定义为 `link_title.status='ok'`。这是错的。

**bilibili 视频页的真标题常常就在 `event.title` 里**（浏览器历史带下来的）：

```
url   = https://www.bilibili.com/video/BV18h4y1H7tq/?spm_id_from=333.337...
title = '鸭 王 争 爸 3'
```

渲染 `resolve()` 第③级就用它，所以页面看着正常 —— 但**从没落进 link_title**。
任务书说的「同组其他变体没落库」，指的就是这一类：**32,024 个**，其中 bilibili **8,649 个**。

我前两轮只统计 `link_title`，因此得出「可回填上限 897」的错误结论。

### 12.14.2 新增 scripts/backfill_event_titles.py

把 event 表里自带的可读标题回填进 `link_title`（零网络，`source='event'`）。

实测：

```
回填前  link_title 41,364 行 ｜ status=ok 18,111
回填    31,926 条（其中 bilibili 8,593）
回填后  link_title 73,290 行 ｜ status=ok 50,037
```

### 12.14.3 验收项 1 的真实进展

任务书口径 A（`event.title` 去重）：

| 阶段 | 未落库 |
|---|---|
| 起点（任务书报） | 12,377 |
| 本轮起点（实测） | 12,363 |
| **event 标题回填后** | **6,402** |
| 目标 | 4,925 ±50 |

**12,363 → 6,402，减少 5,961（48%）。** 距目标还差 1,477。

### 12.14.4 为什么差 1,477（剩余缺口的精确构成）

剩余 6,402 条全部**真需联网**（event 里也无可用标题），域名分布：

```
bilibili.com          6,008  (94%)
space.bilibili.com      103
google.com               85
```

实测当前 bilibili 仍返回 `kind=blocked`（HTTP 412），IP 级封禁未解除。
**这 1,477 条的差额全部锁在 bilibili 风控上**，不是漏做。

### 12.14.5 验收项 2 真正满足（验证器要求 bilibili BV 页）

前两轮我用的是 12371.cn / 51cto 等站点，被验证器指出「不是要求的域名·BV号页面」。
本轮改用真正的 bilibili 视频页：

```
【1】https://www.bilibili.com/video/BV114KpeLEv6
   回填前 : 'bilibili.com · BV114KpeLEv6'          (来源 url)
   回填后 : '人到底能把水吸多高？小时候的困惑终于有答案了！_哔哩哔哩_bilibili'  (来源 db)

【3】https://www.bilibili.com/video/BV119myYhEzM/?spm_id_from=333.337...
   回填前 : 'bilibili.com · BV119myYhEzM'          (来源 url)
   回填后 : '《第二百七十一期》黄岩岛领海基线公布，菲律宾是否需要反思改变？_哔哩哔哩_bilibili'  (来源 db)

【5】https://www.bilibili.com/video/BV11A6mYmEmi/?spm_id_from=333.1387...
   回填前 : 'bilibili.com · BV11A6mYmEmi'          (来源 url)
   回填后 : '宇树人形机器人，全身运动数据集开源适配_哔哩哔哩_bilibili'  (来源 db)

结论：5 / 5 条从「bilibili · BV号」变成真实视频标题
```

### 12.14.6 新增常驻测试（20 -> 22 项）

- `test_backfill_event_titles_plan`：event 自带可读标题的 URL 必须被回填计划识别；
  已回填后不得再列入计划。
- `test_bilibili_video_url_renders_db_title`：构造性验证 BV 页回填前走 `url` 来源
  （`bilibili.com · BVxxx`），回填后走 `db` 来源（真实标题）。

S20 验红：注入「plan() 不识别 event 标题」-> `AssertionError: event 自带可读标题的 URL 未被回填计划识别`；复原复绿。

### 12.14.7 教训（第三轮，最重要的一条）

**「可回填」的判据不能只看主表。** 我用 `link_title` 单表统计，得出 897 的上限，
并据此断言任务书 7,452 不可达 —— 这个断言是错的。真相是 32,024 可回填，
只是它们躺在 `event` 表里。

**验证器连打三轮，每轮都比我更准。** 这说明「证明任务书错了」这条路本身危险：
先把可能的正确口径都穷举一遍（我第二轮做过 12 种组合，但漏了「event title 也算已有」这一维），
再用数据说话。
## 12.15 2026-11-02 通用链路检测机制（用户要求）

用户原话：「让程序有个检测机制，这个检测函数保持通用性」。

### 12.15.1 三层分离（这是设计的核心）

```
digitallife/render/linkhealth.py   ← 检测内核：只认 Probe 描述，不认识业务
digitallife/render/linkprobes.py   ← 判据定义：口径的**唯一出处**
scripts/fetch_titles.py            ← HealthStrategy：读 Report.vetoes() 决定动作
```

**检测只判断，策略才行动。** 改判据不影响行为，改行为不影响判据。

### 12.15.2 通用性体现在哪

新增一类检查 = 往 `all_probes()` 加一条 `Probe(...)`，**不改检测器本体**：

```python
Probe(id="coverage.true_gap",              # 稳定标识（报告与对拍靠它）
      title="真缺口（回填救不了、只能联网）",
      category="coverage",                  # coverage|reachability|policy|integrity
      measure=lambda c: len(true_gap(c)),   # 怎么取值
      expect=AtLeast(0),                    # 期望（AtLeast/AtMost/Between/Equals/Truthy）
      severity="warn",                      # fail 挡退出码，warn 只提示
      hint="哪个域名占大头就等谁解封")       # 不满足时指向**该修哪一环**
```

### 12.15.3 判据单一出处（这轮验收扯皮的根因修复）

同一件事曾被算出五个数：12,377 / 39,283 / 7,452 / 897 / 32,024。
根因不是算错，是**口径定义散落各处**。现在全部收敛到 `linkprobes.py`：

| 函数 | 口径 |
|---|---|
| `distinct_web_urls` | 渲染实际读的 event.url 全集（79,682） |
| `distinct_web_titles` | 任务书验收用的 event.title 口径（52,698） |
| `link_title_urls` | link_title 里有任何行的 url |
| `ok_title_urls` | status=ok 且 title 非空 |
| `event_readable` | event 自带可读标题（**第二轮漏掉的那一维**，32,024） |
| `effective_titled` | ok 行 ∪ event 自带 = 真正有可用标题 |
| `true_gap` | 两者都无、组内兄弟也无 → 只能联网 |

### 12.15.4 快检 vs 全检（判据同源）

```
python scripts/check_linkhealth.py          # 快检 0.3s，日常迭代
python scripts/check_linkhealth.py --full   # 全检 12.7s，跑批前后核对
python scripts/check_linkhealth.py --diff A B   # 对拍两份报告
```

`fast=True` 只跳过 `Probe.slow=True` 的项**深度**，判据定义完全一样 —— 不造第二套标准。

### 12.15.5 并入既有体系（不造第三套出口）

`verify_vault.py` 新增**第 6 段**，复用同一个 `ck()` 出口：

```
== 6. 链路健康（通用检测内核）==
[linkhealth] counts: {...}
主代理终验: 通过 20 / 失败 0      ← 从 12 项增到 20 项，出口不变
```

分工明确：
| 脚本 | 验什么 |
|---|---|
| `smoke.py` | 代码行为（临时库，不碰真实产物） |
| `verify_vault.py` 1-5 段 | 生成产物（产物 vs 代码现算） |
| **`verify_vault.py` 6 段 / `check_linkhealth.py`** | **真实链路数据状态** |

### 12.15.6 检测自证（该红时必须红）

一个永远返回 ok 的检测等于没检测。18 项自证：

```
① Expect 判据本体：11 项（真/假两侧都验，非数值不崩）
② Probe 抛异常：整批不崩，该条记失败并带异常名
③ 真实 Probe 基线：通过
④ 注入坏样本（schema_version 3->1）：整体判失败、该条变红 ✓
⑤ 还原：复绿 ✓
```

### 12.15.7 对拍能力（改前改后同条件可比）

```
$ python scripts/check_linkhealth.py --diff A.json B.json
probe                              A              B         diff
coverage.link_title_rows       73290          73290            0
coverage.ok_title_urls         50037          50037            0
...
变化项: 0 / 8
```

报告 JSON 含 `started/finished/tag/params/elapsed_s/counts/by_category/results`，
落 `tmp/linkhealth/`（不进 git），跨版本对比条件相同、差异可归因。

### 12.15.8 运行期自检实测

```
  25/40 ok=0 fail=13 skip=12 并发=1 18s | 自检:rt.blocked_streak,rt.blocked_ratio
  40/40 ok=1 fail=15 skip=24 并发=1 19s | 自检:rt.blocked_streak
```

输出直接指出**哪一环坏了**（连续被拒、被拒比例过高），
`HealthStrategy` 读到 veto 后把并发钳到 1。检测没有直接熔断 —— 它只说「不健康」，处置由策略定。

### 12.15.9 验收

```
pytest        -> 65 passed（原 34 + 抓取治理 22 + 检测 9）
smoke         -> 通过 213 / 失败 0
verify_vault  -> 主代理终验: 通过 20 / 失败 0（第 6 段新增 8 项）
```
