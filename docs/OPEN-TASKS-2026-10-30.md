# 未完成任务清单（digital-life）

> **用途**：交接件。抽到任一条，都能回指来源、找到当前真值、知道「怎样才算做完」。
> **不写**：会话过程、已解决问题的来龙去脉（那些在 `HANDOFF-2026-10-25.md` 与 `docs/postmortem/`）。
> **状态位**：`[ ]` 未做 · `[~]` 做了一半 · `[?]` 做了但需重新举证 · `[!]` 等你拍板（**不是待办，是决策**）

生成时间：2026-10-30 ｜ 生成方式：`python tmp/todo_probe.py` 实跑取真，非回忆。

---

## 零、一屏速览

| 区 | 条目 | 状态 | 卡在哪 |
|---|---|---|---|
| 1 证据补链 | E1–E4 | `[?]` | 做过了，但可复现输出散在 tmp/，未归档 |
| 2 待施工 | W1–W5 | `[ ]` `[~]` | 依赖顺序：测试网 → 结构 → 目录 |
| 3 风险项 | R1–R2 | `[ ]` | 已知隐患，未触发事故 |
| 4 等你拍板 | D1–D5 | `[!]` | **不批不干** |

---

## 一、证据补链（做了，但要重新举证）

> 这一类**不是重做功能**。功能在，可复现的输出丢在 `tmp/` 或已被后一次重生成覆盖。
> 补的动作 = 跑一次并把输出存成固定路径，不是重写代码。

### `[?]` E1 — N6「产物逐字节不变」需按新基线重新举证

- **来源**：§11 N6 验收判据；验证器 finding「N6 byte-invariance is unproven」「only a rename count is shown」
- **事实**：N6 期间确实逐字节不变（当时 4 次 PASS）。但**之后为 N1 重新生成了 753 页 HTML**，整页 sha 必然变；`tmp/t4_verify_outputs.py` 现在输出 `html 变化 753/753 → FAIL`。这是我上一轮报告「PASS」的真实错误。
- **已做的隔离证明**：
  - `tmp/body_baseline.json` + `python tmp/body_baseline.py check` → `753 页 | 变化 0 | PASS`（body 逐字节未变）
  - `tmp/t4_verify_outputs.py` 的 md 段 → `基线 753 | 缺失 0 | 变化 0`
- **判据（怎样算做完）**：把「整页 sha」判据**正式改为分段判据**并写进 `docs/architecture/testing.md`：
  - md：整篇 sha 对 `tmp/t4_baseline.json` 逐字节相同
  - html：`<body>` 段 sha 对 `body_baseline.json` 相同 + `<head>` 差异仅限 `_assets/day.css` 一行
- **依赖**：无。可独立完成。

### `[?]` E2 — S20 注入验红日志未归档

- **来源**：用户硬指标「每个新增断言 S20 注入验红」；验证器 finding「No S20 injection-red evidence anywhere in the visible trace」
- **事实**：日志真实存在，共 8 个：`tmp/s20_*.log`。实测内容：
  - `s20_devname_red.log` → `通过 206 / 失败 4`（4 条设备名断言同时红）
  - `s20_cjkgap_red.log` → `通过 210 / 失败 1`（T9a3 空格断言红）
  - `s20_drop_green.log` → `通过 210 / 失败 0`（还原后复绿）
- **判据**：把这 8 个日志整理成 `docs/postmortem/s20-injection-log.md` 一张表（断言 → 注入点 → 红时数字 → 绿时数字 → 文件），并**重跑其中 2 条**验证日志与当前代码仍一致（防日志过期）。
- **依赖**：无。

### `[?]` E3 — 交付五段式缺 P/D 的历史轮次

- **来源**：用户「交付五段式：P/D/C/A/未做与风险，缺段视为未完成」；验证器 finding「omits the required P and D sections」
- **事实**：本轮及上一轮报告已补 P/D/C/A，但**早期 B1/B2/T1–T4 轮次**的交付按压缩摘要形式给出，原始 P/D 段未留存。
- **判据**：在 `docs/postmortem/2026-10-25-batch-delivery.md` 补齐 B1/B2/T1/T2/T3/T4 六项的 P（判据）与 D（改动文件 + 关键代码片段）；C 段引用 E2 的日志表与本次真跑输出。
- **依赖**：E2（C 段要引用）。

### `[?]` E4 — `t4_baseline.json` 已与当前产物脱节

- **来源**：本轮实测（`tmp/t4_verify_outputs.py` FAIL）
- **事实**：基线是 N1 之前的 html 快照，N1 之后再拿它比整页 sha 必然全红。**留着会持续误报**。
- **判据**：按 E1 的分段判据重建基线：`t4_baseline.json` 只存 md 整篇 sha；新增 `body_baseline.json` 存 html 的 body 段 sha（**已存在**）。给 `t4_verify_outputs.py` 加注释说明「html 不比对整页」，输出改为「md 整篇 / html body 段」两张表，末行判定合并。
- **依赖**：E1（判据先定，再改脚本）。

---

## 二、待施工（按依赖顺序，不许颠倒）

> 顺序依据：`docs/architecture/layering.md` §5 + `docs/architecture/testing.md` §4。
> 先建测试网 → 再动结构 → 最后搬目录。**当前测试网已建（6 文件 / 34 passed），N6 已完成，故本节只剩尾部与边角。**

### `[ ]` W1 — N1 的 `<300 KB` 未达成（386/753 超限）

- **来源**：§11 N1「单页体积显著下降（对比 494 KB，目标 < 300 KB）」；`docs/HANDOFF-2026-10-25.md:484`
- **当前真值**：`753 页 | 合计 252.3 MB | 中位 313,704 B (306 KB) | 超 300KB 386 页（51%）`。已做：`<style>` 外链化（753/753 生效，由 262.5 MB → 252.3 MB）。
- **实测上限**：可见文本占 31.6%，标签开销 68.4% → 标签压到 0 时中位页理论下限约 97 KB。**300 KB 可达成，但需改渲染结构，不是删内容。**
- **两条路（需 D1 裁决后才能动）**：
  - **A. URL 去追踪参数**：单页 -20,594 B（-4.1%），496 → 476 KB。**代价：修改用户真实浏览记录**。
  - **B. 重构标签结构**（紧凑分隔符 / JS 渲染）：中位可到约 150 KB。**代价：改动面远超 N1，且触项目禁令「不引框架」**。
- **判据**：`python scripts/verify_vault.py --fast` 新增一项「单页体积」检查，中位与 P95 都要达标；`tmp/n1_measure.py` 输出超限页数 < D1 裁定阈值。
- **依赖**：**D1**。

### `[ ]` W2 — `publish.py` 片层 md 设备名仍是「电脑」

- **来源**：§11 N5「日报设备名」；DL-21「三处同源」；本轮实测
- **当前真值**（全量 9,929 篇实测，非抽样）：「电脑」**8,314 次** / 「D-001」**7,185 次**。日报侧已 100% 新名（`## 电脑使用`=0）。**两处分叉，片层侧旧名仍占多数**。
- **判据**：`publish._render_slice_md("pc-main")` 输出含「D-001」不含「电脑」；重生成 9,929 篇后抽样 200 篇零「电脑」；DL-21 的「三处同源」对拍脚本全绿。
- **依赖**：**D2**（用户原话「只改日报」，扩大范围需批准）。

### `[~]` W3 — N6 目录重构：主体已完，尾部未清

- **已完成**：`ingest/ domain/ produce/ emit/ render/ daily/` 五层，**31 个 git rename**，四项出口判据全绿，旧路径 `sys.modules` 别名全部保活。
- **未清**：
  - `[ ]` `digitallife/_assets/` 目前落在数据区 `04-Areas/数字人生/10-时段/_assets/`，**不在代码仓库**——需在 `.gitignore` / `paths.py` 里显式声明它是产物而非源码（对齐 S54：迁移后逐条核 gitignore 带路径规则）。
  - `[ ]` `docs/` 里仍有多处旧路径未替换（`HANDOFF-2026-10-25.md` 按设计保留历史，其余应已改净 143 处，需再全扫一次确认）。
- **判据**：`git status --short | grep -c "^R"` 稳定为 31；`grep -rn "scripts/slice_pages\|digitallife/sources/" docs/ --include=*.md` 只命中 HANDOFF 与 n6-migration-plan（有意保留）；`git check-ignore -v` 对 `_assets/` 给出明确结论。
- **依赖**：E4（基线先定）。

### `[ ]` W4 — T2 的 pytest 覆盖未达「T19 十条 + DL 锚点全迁」原定规模

- **来源**：用户 T2「T19 时间线归并 10 条、DL-2/3/4/7 锚点全部搬迁、publish 至少 1 条产物级端到端断言」
- **当前真值**：`tests/` 6 个文件 / 34 passed；`test_timeline_merge.py` 114 行（含 8 条），`test_data_integrity.py` 169 行（12 条），`test_publish_e2e.py` 142 行（3 条）。
- **可能缺口**：T19 共 10 条（T19a–T19j），pytest 侧 8 条，**需逐条核对着落**；S20 覆盖以 smoke 侧日志为主，pytest 侧未逐条注入。
- **判据**：逐条列出 T19a–j → 对应 pytest 用例名的映射表（缺的补上）；每条新用例做一次 S20 注入验红并留日志（接 E2 的表）。
- **依赖**：E2（沿用同一套日志格式）。

### `[ ]` W5 — T5 tmp/ 治理：方案已有，未执行

- **来源**：用户「T5 只出方案，执行需批准」
- **当前真值**：`tmp/` **4,681 文件 / 506.0 MB / 16 个 .bak**（一个未删）。
- **方案**：`docs/architecture/t5-tmp-governance-plan.md`（含 16 个 `.bak` 清单，**S41：工作区唯一副本，不得删**）。
- **判据**：按方案执行后 `tmp/` 降到方案目标；16 个 `.bak` 逐个有去向（保留或归档路径可查）；`git status` 无新增未跟踪大文件。
- **依赖**：**D3**。

---

## 三、已知隐患（未触发事故，但会静默失效）

### `[ ]` R1 — `daily._DEVICE_SECTIONS` 定义了但从未被使用

- **来源**：本轮实测（`digitallife/daily/render.py` 仅 1 次提及 = 定义处）
- **后果**：数据侧契约要求日报六节固定，**代码并未强制**——少一节不会报错。
- **判据**：`render.py` 里该常量要么被真正消费（渲染时按它校验/排序），要么删除并在 §G.2 废弃清单留痕。二选一，不许继续悬空。

### `[ ]` R2 — 数据区 `_assets/day.css` 无版本号，浏览器可能吃旧缓存

- **来源**：本轮 N1 改动引入
- **后果**：改了 CSS 但 URL 不变，用户浏览器可能显示旧样式，且**很难归因**。
- **判据**：`base.page()` 里给链接加内容指纹（`?v=<sha8>`）；`verify_vault` 断言 753 页引用同一指纹。

---

## 四、等你拍板（**不是待办，不批不做**）

| # | 事项 | 原文依据 | 我需要你给的一句话 |
|---|---|---|---|
| **D1** | N1 体积指标改判 / 选路线 | §11 N1 `HANDOFF:484` | 「300 KB 改判为 X」或「走 A（URL 去参）」或「走 B（重构标签）」 |
| **D2** | `publish.py` 设备名是否同步 | 用户原话「只改日报」 | 「片层 md 也改」或「保持电脑」 |
| **D3** | T5 tmp 治理是否执行 | 用户「T5 只出方案」 | 「执行方案」或「只保留清单」 |
| **D4** | 两项成本 | §6.3 `HANDOFF:227-232` | ①753 天全量跑模型（143 次调用 / 约 2 小时）②剩余 44,469 URL 补抓（6–8 小时 / 成功率约 56%） |
| **D5** | 提交切分 | §9.1 `HANDOFF:412-423` | 当前 **65 条变更（R31 / M19 / ??14）全部未提交**。建议分 3 次：代码 / 文档 / 根 AGENTS.md |

---

## 五、维护约定

1. **状态位只由实跑改**：勾 `[x]` 前必须贴出一条真实命令输出，写在本条「判据」下面。
2. **条目只增不减**：做完的划 `[x]` 并注明日期与证据路径，不删行——下一轮才看得出「这条是刚做完」还是「从来没做」。
3. **本节与 `docs/spec/tasks.md` 的分工**：`tasks.md` 记**已交付**的任务状态；本文件记**未完成 / 待举证 / 待裁决**。同一件事不得两处各说一套（S47）。
4. **新增条目必须带来源**：指向 §11 某条、T1–T5 某项、DL-N 某条、或一条验证器 finding。指不出来的，先降级为「存疑」不进正文。

---

## 六、本清单的抽样回验记录

写完后按「随机挑几条、按它自己写的判据实跑」验证过一次（`python tmp/todo_verify.py`），
**发现并已修正 1 处标注偏差**：

| 条目 | 清单原标注 | 回验实测 | 处理 |
|---|---|---|---|
| E1 body 基线 | 应 PASS | `PASS —— body 逐字节未变` | 一致，保留 |
| E1 md 基线 | 应 0 变化 | `基线 753 / 缺失 0 / 变化 0` | 一致，保留 |
| E1 html 整页 | 应 FAIL（已知） | `变化 753 / 判定 FAIL` | 一致，正是本条要修的问题 |
| E2 S20 日志 | 8 个，含红/绿对照 | 8 个；实测红时：`devname 206/4`、`cjkgap 210/1`、`drop`/`early`/`truncate` 各 1 条红 | 一致，保留 |
| **W2 片层设备名** | 抽样 2000 篇「电脑」19 次 | **全量 9,929 篇：「电脑」8,314 / 「D-001」7,185** | **已修正**：19 是抽样内计数，改为全量真值 |
| R1 `_DEVICE_SECTIONS` | 仅定义未使用 | AST 上下文只有 `Store`（无 `Load`）→ 确未读取 | 一致，保留 |

> **回验的价值**：W2 的抽样口径差点把「九千篇里八千多次旧名」写成「19 次」，读的人会误判为小问题。
> 这正是 S64（缺口统计口径不完整）在文档侧的翻版——**报数字必须同时给口径与分母**。

### 回验命令（可复现）

```bash
cd 01-Projects/digital-life
export PYTHONUTF8=1
python tmp/todo_probe.py     # 全部条目的当前真值
python tmp/todo_verify.py    # 抽样回验红绿
```

### 条目 → 证据 索引

| 条目 | 证据在哪 |
|---|---|
| E1 / E4 | `tmp/body_baseline.json`、`tmp/t4_baseline.json`、`python tmp/body_baseline.py check` |
| E2 | `tmp/s20_*.log`（8 个） |
| E3 | 待建立 `docs/postmortem/2026-10-25-batch-delivery.md` |
| W1 | `python tmp/n1_measure.py`、`python scripts/verify_vault.py --fast` |
| W2 | `python tmp/todo_probe.py` B 段 |
| W3 | `git status --short \| grep -c "^R"` |
| W4 | `python -m pytest` + `tests/` 目录 |
| W5 | `docs/architecture/t5-tmp-governance-plan.md`、`python tmp/todo_probe.py` D 段 |
| R1 / R2 | `python tmp/todo_verify.py` R 段 |
| D1–D5 | `docs/HANDOFF-2026-10-25.md` §6.3 / §9.1 / §11 |