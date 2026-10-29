# 契约 × 代码 一致性对账（CONSISTENCY）

> **采集日期：2026-10-25**（本地时区 UTC+8）。
> **对账对象**：`docs/spec/{`design,interfaces,requirements,data-sources,tasks`}.md` 里的每一条可判定的契约。
> **判据来源**：直接读代码，不采信任何文档自述（S36：交接情报视为待复核假设）。
> **代码基线**：本表所有 `文件:行号` 取自下列 sha256 前缀对应的快照
> （`python tmp/a4_sha.py` 重算，前 16 位）：
>
> ```text
> 94109fa2b76018d7  daily.py        548f75d859d3a43e  publish.py      1171197d67e072c4  vision.py
> d9b7651e1f5b0ce0  devices.py      a304e7e3068c8e3b  paths.py        1a1457e8b9ec1719  schema.py
> 6f33df3e9bf07f37  store.py        4d1a98b06834cb01  clean.py        867a0d962efefbc6  dbview.py
> 9d8619fe7e17bffc  slicer.py       48f5265ffd8a69a9  model.py        46e47aea99fc77a4  llm.py
> 00dcf7b6729d7b67  timeline_merge.py  b264fc40421a7d0d  schema_check.py  3ed00f460f0bb899  prompts/segment.py
> 8892450e14c36072  linktitle.py    aeba024f31f8dde6  build_slice_html.py
> ```
>
> ⚠️ **行号会漂移**（同轮有子代理在改 `daily.py`/`publish.py`/`slice_pages`）。
> 定位不到时按 sha256 判断「是不是同一版」，再用函数名重查——**不要相信行号本身**。

---

## 0. 判决规则（先约定，再逐条判）

对每条不一致，只有三种判决，理由必须写出来：

| 判决 | 什么时候用 |
|---|---|
| **改文档** | 代码是用户已拍板的方向 / 数据实际形状如此 / 文档写的是过时实现细节。文档服从代码与用户裁决。 |
| **改代码** | 文档表达的是**仍有效的契约**（签名、落盘归属、安全约束），代码偏离即缺陷。 |
| **已废弃** | 该契约描述的东西已不存在，且用户明确否掉。文档应删除并留痕，不得静默消失。 |

**本轮判决统计**：改文档 **15 条（全部已就地修订）** ｜ 改代码 **0 条** ｜ 已废弃 **1 条** ｜
无法判定（并发中）**2 条**（C-02 产出待重生成、C-20 子代理2 正在改）。
最终统计以 §2 总览表为准——**以那里为唯一口径，本节不重复计数**，避免两处数字漂移（S47）。

---

## 1. 逐条对账

### C-01 日报「恒出电脑/手机两节」→ 设备节是否恒定

| 项 | 内容 |
|---|---|
| 契约出处 | `interfaces.md` §5.3（原文 246–256 行）：「原契约要求 `## 电脑使用`/`## 手机使用` **恒出两节**。现行为：`render_narrative` 产出 … **没有固定的电脑/手机两节**」 |
| 代码实现 | `digitallife/daily/:855` `render_narrative`；设备循环在 `for dev in has_data` 一段 |
| 数据真值 | 753 篇日报中 `## 电脑使用` 出现 **752** 篇、`## iQQQZ3 使用` **392** 篇、`## iQQQ Neo11 使用` **72** 篇（`tmp/a4_modes2.py` 实测） |
| 判定 | **不一致 → 改文档** |
| 理由 | 文档说「没有固定设备节」是**错的**：设备节一直存在，只是「该设备当日无数据就跳过」。752 而非 753 是因为有一天 PC 无数据。文档描述的是某个中间版本，从未成立过。已在 `interfaces.md` §5.3 重写并标注原文错误。 |

### C-02 日报三级节「无数据就整节消失」→ 新契约要求固定六节

| 项 | 内容 |
|---|---|
| 契约出处 | 数据侧 `AGENTS.md` §D.1（2026-10-25 用户定稿）：每设备固定六个 `###`，无数据也保留 |
| 代码实现 | `digitallife/daily/:666` `_render_device_body`：六节**无条件** `out += ["", "### 应用", ""]`，空体写 `_EMPTY_SECTION`（`daily.py:605` = `- （本设备当日无此类记录）`） |
| 已生成产出 | 753 篇 md 仍是旧结构（`### 时间线（重要事件）` 1,171 次等） |
| 判定 | **代码已实现新契约，产出未重生成**（既不改文档也不改代码，登记为「待重跑」） |
| 理由 | 代码与契约一致；不一致的是**生成物**。重跑命令：`python scripts/rebuild_daily.py && python -m digitallife.cli publish --no-zip`。已在数据侧 §D.2 立表说明「代码落地 ≠ 产出落地」 |

### C-03 `interfaces.md` §0 设备清单

| 项 | 内容 |
|---|---|
| 契约出处 | `interfaces.md` §0 表格：`DEVICES`（`pc-main`/`phone-1`） |
| 代码实现 | `digitallife/domain/devices.py:50-53`：`DEVICES` 为 **三台** —— `pc-main`/`phone-1`/`phone-2`，且 `Device` 多一个 `name` 字段 |
| 判定 | **不一致 → 改文档**（已改） |
| 理由 | 少列一台设备 + 少一个字段。文档仍在描述改造前的两设备形态。已改为指向 `DEVICES` 本身，不复制清单（防漂移） |

### C-04 `interfaces.md` §5.3 降级路径「退回 `render_daily` 模板」

| 项 | 内容 |
|---|---|
| 契约出处 | `interfaces.md` §5.3 原文：「**模型不可用时**（降级路径）：退回 `render_daily` 模板，仍按设备分段」 |
| 代码实现 | `digitallife/daily/:920-928`：`render_daily` 已标注「[已停用] 模板渲染路径」，函数体 `return render_narrative(...)` |
| 判定 | **已废弃 → 改文档**（已改） |
| 理由 | 降级路径不存在了。2026-10-24 用户裁决「`render_daily` 这种输出长列表的无效」。已保留函数名兼容，但行为等同 `render_narrative` |

### C-05 `data-sources.md` 注册表硬编码清单

| 项 | 内容 |
|---|---|
| 契约出处 | `data-sources.md` §一「注册表（唯一真源）」里贴了一段 `DEVICES = (Device("pc-main"...), Device("phone-1"...))` |
| 代码实现 | `digitallife/domain/devices.py:50-53` 为三台 + `name=` 字段 |
| 判定 | **不一致 → 改文档**（已改） |
| 理由 | 贴代码片段做「真源」必然漂移（S47 同一真值多口径）。已改为「本文不复制清单 + 一条可执行命令」 |

### C-06 `data-sources.md` 旧别名条数

| 项 | 内容 |
|---|---|
| 契约出处 | `data-sources.md`：「`pc` -> pc-main **816,173** 条；`phone` -> phone-1 **306,885** 条（2026-10-22 实测）」 |
| 代码实现 | `digitallife/domain/devices.py:57` `LEGACY_ALIASES`（只做映射，不含条数） |
| 数据真值 | 2026-10-25 实测：`device` 字面值 `pc` 816,173 ｜ `phone` 203,461 ｜ `phone-2` 105,197 |
| 判定 | **不一致 → 改文档**（已改） |
| 理由 | `phone` 的 306,885 是**旧口径**（把 phone-2 并进 phone-1 算的），现在按字面值是 203,461。违反 S45（增长型指标写死精确值）。已改为不写死 + 给可执行查询 |

### C-07 `data-sources.md` `device_label()` 返回「电脑/手机」

| 项 | 内容 |
|---|---|
| 契约出处 | `data-sources.md` §一「设备呈现」：「设备标签走 `contract.device_label()`：同类只有一台时显示「电脑 / 手机」」 |
| 代码实现 | `digitallife/render/contract.py` 的 `device_label` 返回 **`devices.name_of()`**（真实设备名 `D-001`/`iQQQZ3`） |
| 判定 | **不一致 → 改文档**（已改） |
| 理由 | 与工程文档 §D.3、`requirements.md` R12「设备一律显示真实设备名」直接冲突。用户 2026-10-22 明确否掉「电脑/手机」的写法 |

### C-08 `data-sources.md` 日报设备名用 `label`

| 项 | 内容 |
|---|---|
| 契约出处 | `data-sources.md`「日报里的显示」：「同类有多台 → 自动带设备名（「主力电脑使用」「手机 2 使用」）」 |
| 代码实现 | `digitallife/daily/:612` `_device_label`：多台走 `devices.name_of()`（真实名），不是 `label` |
| 数据真值 | 实测节标题为 `## iQQQZ3 使用` / `## iQQQ Neo11 使用`，不是「手机 2 使用」 |
| 判定 | **不一致 → 改文档**（已改） |
| 理由 | 混淆了 `name` 与 `label` 两个字段。`label`（主力电脑/手机 1）只用于需要成句的场合 |

### C-09 `interfaces.md` §0 `devices` 缺 `name_of`

| 项 | 内容 |
|---|---|
| 契约出处 | `interfaces.md` §0：列出了 `all_devices get kind_of label_of by_kind resolve_for_source query_aliases sql_device_filter` |
| 代码实现 | `digitallife/domain/devices.py:65` `name_of()` —— 存在但未登记 |
| 判定 | **不一致 → 改文档**（已改） |
| 理由 | 函数真实存在且被 `daily.py`/`contract.py` 依赖；漏登记会让接手的代理以为自己该用 `label_of` |

### C-10 `interfaces.md` §2.12 `build_daily` 签名

| 项 | 内容 |
|---|---|
| 契约出处 | `interfaces.md` §2.12：`def build_daily(con, date_key: str, client=None) -> dict` |
| 代码实现 | `digitallife/daily/:1033`：`def build_daily(con, date_key: str, client=None, persist: bool = True) -> dict` |
| 判定 | **不一致 → 改文档**（已改） |
| 理由 | `persist` 是 2026-10-24「日报不入库」改造新增的参数（`publish` 走现算，`persist=False`）。属**向后兼容的增参**，不是破坏性变更，但契约必须登记，否则调用方不知道该参数存在 |

### C-11 表结构：`daily_summary` 主键列名

| 项 | 内容 |
|---|---|
| 契约出处 | `interfaces.md` §1 只列了表名，未列列名 |
| 代码实现 | `digitallife/ingest/schema.py:103-110`：主键是 `date_key`（不是 `date`） |
| 判定 | **契约不完备 → 改文档**（已改，§1 补了六张表的「易错列名」表） |
| 理由 | 我在写数据侧快照时真踩到了：查 `daily_summary` 用 `date` 列直接报 `no such column: date`（主键是 `date_key`）。这条不写进契约，下一个接手的人还会踩 |

### C-12 `requirements.md` R5「清洗可解释产出 `clean_report.json`」

| 项 | 内容 |
|---|---|
| 契约出处 | `requirements.md` R5：「每个源必须产出「输入行数 → 输出行数」的差额与原因分布（`clean_report.json`）」 |
| 代码实现 | `digitallife/paths.py:48` `clean_report_path()` → `INDEX_DIR/clean_report.json`；文件**实测存在**（701 字节） |
| 判定 | **一致** |
| 理由 | 文件在 `_index/` 下，路径与契约一致。注意它**只在跑 `cli ingest` 时更新**，不是常驻 |

### C-13 `requirements.md` R12 全量明细归时段网页（一天一个 HTML）

| 项 | 内容 |
|---|---|
| 契约出处 | `requirements.md` R12 |
| 代码实现 | `scripts/build_slice_html.py:238` `render_day`；`digitallife/paths.py:24` `SLICE_DIR = VAULT / "10-时段"`，产出 `<日期>.html` |
| 数据真值 | `10-时段/` 下 `*.html` **753** 个，无日期子目录（`--prune` 已清理旧结构） |
| 判定 | **一致** |
| 理由 | 文件形态、数量、落点都与契约吻合 |

### C-14 `requirements.md` R6 幂等

| 项 | 内容 |
|---|---|
| 契约出处 | `requirements.md` R6：「同一源重跑两次，`event` 行数不变」 |
| 代码实现 | `digitallife/ingest/model.py:76` `event_id`（sha1 主键）+ `store.upsert_events` 的 `ON CONFLICT DO UPDATE` |
| 验证 | 冒烟套件含幂等项，实测 `通过 187 / 失败 0` |
| 判定 | **一致** |
| 理由 | 主键由 `device|source|kind|start|end|dedupe_key` 决定，重跑天然幂等 |

### C-15 `design.md` §2 幂等主键公式

| 项 | 内容 |
|---|---|
| 契约出处 | `design.md` §2：`event_id = sha1(device|source|kind|start_utc_ms|end_utc_ms|dedupe_key)` |
| 代码实现 | `digitallife/ingest/model.py:76-83`：`raw = "|".join([device, source, kind, str(start), str(end or ""), dedupe_key()])` |
| 判定 | **一致** |
| 理由 | 字段顺序与公式完全对应；`end` 为空时用空串 |

### C-16 `design.md` §2 `dedupe_key` 规则

| 项 | 内容 |
|---|---|
| 契约出处 | `design.md` §2：screen→path；web→url（回退 title）；app/power→app|title |
| 代码实现 | `digitallife/ingest/model.py:53-74`：另有 **battery**（含 `battery_pct`）与 **notify**（含 `count`）两个分支 |
| 判定 | **契约不完备 → 改文档**（已改） |
| 理由 | `design.md` 漏了两个分支，而这两个分支正是「丢 11,075 行」缺陷的修复点。漏写会让下一个人「按设计文档简化」时把缺陷改回来 |

### C-17 `design.md` §1.2 落盘分层 + `.gitignore` 规则名

| 项 | 内容 |
|---|---|
| 契约出处 | `design.md` §1.2 末：「`.data/` 由根 `.gitignore` 的 `*.data` 规则排除」 |
| 实际 | 根 `.gitignore` 已补**显式 `.data/`** 规则（数据侧 `AGENTS.md` §C 记载 2026-10-22 补写）；`*.data` 只该管二进制模型文件 |
| 判定 | **不一致 → 改文档**（已改） |
| 理由 | 说错规则名会让维护者按错规则排查（S54：改规则后必须 `git check-ignore -v` 反证） |

### C-18 `design.md` §4 清洗规则 C1–C13

| 项 | 内容 |
|---|---|
| 契约出处 | `design.md` §4 十三条清洗规则 |
| 代码实现 | `clean.py`（`MIN_DURATION_S_DEFAULT=0`，`clean.py:86`）、`slicer.py:32-33`（`WINDOW_MIN=15`/`MAX_IMAGES=24`）、`dbview.py:173`（`POINT_EVENT_MS=60_000`） |
| 判定 | **一致** |
| 理由 | 抽查的三处常量与文档数字完全相同；C13（剔除离开时段）在 `dbview` 有对应实现 |

### C-19 `interfaces.md` §2.13 `publish.py` 落盘清单

| 项 | 内容 |
|---|---|
| 契约出处 | `interfaces.md` §2.13：`publish_all(con, day_keys=None, make_zip=True)`，写 `VAULT/10-时段/YYYY-MM-DD/HHMM-HHMM.md` |
| 代码实现 | `digitallife/emit/publish.py:426` 签名一致；但片层路径已改为 `10-时段/片层/<日期>/*.md`（`paths.LAYERED_DIR`，`paths.py:25`） |
| 判定 | **不一致 → 改文档**（已改） |
| 理由 | 契约里的 `10-时段/YYYY-MM-DD/HHMM-HHMM.md` 是 **2026-10-22 之前的旧布局**（那时时段是「日期目录 + 9 页」）。现布局是 `10-时段/片层/...`（`paths.LAYERED_DIR`），与 `build_slice_html` 的产出不冲突 |

### C-20 `interfaces.md` §5.8 时段的 CSS 类清单

| 项 | 内容 |
|---|---|
| 契约出处 | `interfaces.md` §5.8：「`base.CSS` 已定义 `.section/.section-head/.seg/.panel/.tw/table/.num/.ttl/ul.sub/.sites/.site/.visit/.more/.card/.empty/.dock/.jump/.fab/.bar`」 |
| 代码实现 | `digitallife/render/base.py` 仍在改（本轮 `git status` 显示 `M digitallife/render/base.py`），且新增了 `hourbar.py`（24h 时间条） |
| 判定 | **无法判定（并发进行中）** |
| 理由 | 子代理2 正在改 `slice_pages/*`，本轮不适合定格。**留给改造收尾时复核** |

### C-21 设备过滤必须走 `sql_device_filter`

| 项 | 内容 |
|---|---|
| 契约出处 | `data-sources.md` §一 + `interfaces.md` §5.6b（硬约束） |
| 代码实现 | `digitallife/domain/devices.py:105` `sql_device_filter(device_id, column="device")` 返回 `("device IN (?,?)", [新id, 旧别名])` |
| 判定 | **一致** |
| 理由 | 实现与契约逐字对应；函数注释里还写了受害点清单 |

### C-22 `schema_check` 的 R5b 是否还存在

| 项 | 内容 |
|---|---|
| 契约出处 | `interfaces.md` §2.10/§2.11 只写「R1–R9 校验」；`postmortem/diary-style-2026-10-24.md` 说 R5b 已回滚 |
| 代码实现 | `digitallife/produce/schema_check.py` 中搜索不到 `R5b` 的**生效代码**（只在注释里作为教训出现） |
| 判定 | **一致（已回滚）** |
| 理由 | 片层清单体校验仍在（每节 ≥3 条列表项），叙述体禁令已移除 |

### C-23 `interfaces.md` §2.10 提示词必须含的子串

| 项 | 内容 |
|---|---|
| 契约出处 | `interfaces.md` §2.10：`build_prompt` 必须包含 `"禁止使用OCR文本"` `"## 关键操作"` `"## 主要活动"` `"## 重点内容"` 等子串 |
| 代码实现 | `digitallife/produce/prompts/segment.py:11` `PROMPT_VERSION = "screenmemo-v1"`、`:13` `OUTPUT_FIELDS`、`:46` `build_prompt` |
| 验证 | 冒烟 T13 断言这些子串 |
| 判定 | **一致** |
| 理由 | 三节标题仍是片层格式的真源；注意它**只属于片层**，日报里已无这几个 `##` |

### C-24 `tasks.md` 交付实测数字

| 项 | 内容 |
|---|---|
| 契约出处 | `tasks.md`「交付实测（2026-10-22 现场查询）」：事件 1,123,058 / 片 86,193 / 视觉 124 / 冒烟 120 |
| 数据真值（2026-10-25） | 事件 1,124,831 / 片 43,257 / 视觉 9,679 / 冒烟 187 |
| 判定 | **不一致 → 改文档**（已改） |
| 理由 | 四个数字**全部漂移**。旧值里「片 86,193」是含已重切前旧片的数；「视觉 124」是长跑中途的快照。已整表重测替换，并加「数字会变，命令不会」 |

### C-25 冒烟项数在三处文档中不一致

| 项 | 内容 |
|---|---|
| 出处 | 原 `01-Projects/digital-life/AGENTS.md` 写 **181**（3 处）、`04-Areas/数字人生/AGENTS.md` 写 **143**、根 `AGENTS.md:237` 写 **100** |
| 代码真值 | `python scripts/smoke.py` → `通过 187 / 失败 0`（`scripts/smoke.py:1350` 打印该行） |
| 判定 | **不一致 → 改文档** |
| 理由 | 三个数字**没有一个对**。工程文档已收敛为「以命令末行为准 + 一处写明 187（2026-10-25）」；数据侧已删除数字、只留指路。根 `AGENTS.md` 的 100 属工作区级文档，**不在本子代理允许修改范围内**，登记见 §3 |

---

## 2. 一致性总览

> **本表是唯一计数口径**，其他地方（含 §0）不重复写数字。

| 判定 | 条数 | 编号 |
|---|---|---|
| ✅ 一致（无需改动） | **7** | C-12、C-13、C-14、C-15、C-18、C-21、C-22、C-23 —— 其中 C-04 的「已废弃」判定另计，见下行 |
| 📝 不一致 → **已改文档** | **15** | C-01、C-03、C-04、C-05、C-06、C-07、C-08、C-09、C-10、C-11、C-16、C-17、C-19、C-24、C-25 |
| 🛠 不一致 → **应改代码** | **0** | —— 本轮没有发现「代码偏离仍有效契约」的情形 |
| 🔵 无法判定（并发进行中） | **2** | C-20（子代理2 正在改 `slice_pages`）、C-02（代码已改，**产出未重生成**） |
| **合计** | **24** | C-01 … C-25（编号连续，C-02 归入「无法判定」，故计入 24 条判定 + C-04 的废弃属性） |

**术语澄清**：C-04（`render_daily` 降级路径）既被判为「已废弃」，又归入「已改文档」——
这里按**处置动作**计入「已改」，不重复计数。

---

## 3. 未做（本轮明确不做，交接给下一手）

> **C-10 / C-11 / C-16 / C-17 / C-19 已于本轮就地修订完毕**，移出待办。

| # | 待办 | 为什么不本轮做 | 建议做法 |
|---|---|---|---|
| 1 | C-20：`slice_pages` 的 CSS 类清单（`interfaces.md` §5.8） | 子代理2 正在改 `base.py`/`web.py`/`timeline.py`，本轮定格必然又过期 | 改造收尾时重新核对一遍类名 |
| 2 | C-02：753 篇日报未按新结构重生成 | **属于运行产出，不是文档工作**；且重跑会覆盖用户当前正在看的日报 | 由主线决定时机：`python scripts/rebuild_daily.py && python -m digitallife.cli publish --no-zip` |
| 3 | 根 `AGENTS.md:237` 的「冒烟 100 项」 | 根文档不在本子代理允许修改范围（禁止跨域写） | 由主代理改为「以 `python scripts/smoke.py` 末行为准」 |
| 4 | `docs/HANDOFF.md` 指向的 `FINAL-2026-10-23.md` 是死链 | 该文件从未产出；补写等于**发明历史**，改链接又越过了「交接记录应保持原样」的边界 | 由主代理决定：①把链接换成 `postmortem/2026-10-23-delivery.md`，或②在文首加一行「本文件的 `FINAL-*` 链接已失效，内容见 …」。已在 `docs/README.md` 标 🔴 |
| 5 | `docs/WORKFLOW.md:5` 写「§C 十三条 DL」（现为 16 条） | 属行文表述，改它不影响任何断言；但会误导查条数的人 | 改为「见 `AGENTS.md §C`」不写条数（已在 `docs/README.md` 标🟡） |
| 6 | `docs/architecture/*`（子代理3 产出）的死链 | 那不是我的写域；且 `layering.md` 里的 `digitallife/daily/*.py` 是**计划中的目标结构**，本就还不存在 | 已在我的死链检查里用白名单标注「② 目标结构」；子代理3 收尾时自检 |

---

## 4. 复现本对账的方法

```bash
cd 01-Projects/digital-life
export PYTHONUTF8=1 && unset DL_DB DL_WORK_DIR

python tmp/a4_sha.py          # 代码基线 sha256 + 行数（判断快照是否漂移）
python tmp/a4_measure.py      # 数据真值（事件/片/总结/日报/体积）
python tmp/a4_modes2.py       # 753 篇日报的二级/三级标题字面频次
python scripts/smoke.py       # 冒烟真值（末行「通过 N / 失败 0」）
```

**判定纪律**（照搬 S23）：写「契约说 X、代码做 Y」之前，**必须先在代码里看到 Y**，
并且把 `文件:行号` 与代码行原文一起贴出来。只凭记忆或只凭旧文档下的判决一律作废。

---

## 5. 行号抽查（9 条，含真实代码行原文）

```bash
cd 01-Projects/digital-life && python tmp/a4_spot5.py
```

```text
--- C-15 event_id 公式            ->  digitallife/ingest/model.py:76
        def event_id(self) -> str:
--- C-03 DEVICES 三台             ->  digitallife/domain/devices.py:50
    DEVICES: tuple[Device, ...] = (
--- C-02 _EMPTY_SECTION 空节占位    ->  digitallife/daily/:605
    _EMPTY_SECTION = "- （本设备当日无此类记录）"
--- C-01/C-04 render_narrative    ->  digitallife/daily/:855
    def render_narrative(date_key: str, breakdown: dict, structures: dict,
--- C-21 sql_device_filter         ->  digitallife/domain/devices.py:105
    def sql_device_filter(device_id: str, column: str = "device") -> tuple[str, list[str]]:
--- C-18 MIN_DURATION_S_DEFAULT    ->  digitallife/ingest/clean.py:86
    MIN_DURATION_S_DEFAULT = 0
--- C-18 WINDOW_MIN/MAX_IMAGES    ->  digitallife/domain/slicer.py:32
    WINDOW_MIN = 15
--- C-18 POINT_EVENT_MS           ->  digitallife/domain/dbview.py:173
    POINT_EVENT_MS = 60_000
--- C-19 DATA_DIR / 落盘           ->  digitallife/paths.py:21
    DATA_DIR = WORKSPACE / ".data" / "digital-life"   # 库、NDJSON、work 落这里
```

> 抽查口径：**函数定义行 = 该函数的第一行**（不含装饰器）。若代码被并行改动导致行号漂移，
> 以函数名重新 grep 定位——**sha256 相同才说明是同一版**。
