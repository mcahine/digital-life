# S20 注入验红台账

> **这份文件是什么**：把 `tmp/s20_*.log` 里已有的 8 次注入验红结果整理成可检索的表。
> **不是什么**：不是新的测试报告，不是断言清单（那在 `scripts/smoke.py`），也不是通过证明。
> 每条断言的**红绿两侧都必须是真值**，不允许写「变红了」这种描述。

## 证据来源

| 项 | 值 |
|---|---|
| 原始证据 | `tmp/s20_*.log` 共 **8 个文件** |
| 采集时间 | 2026-10-29 22:39:47 – 23:13:22（文件 mtime，逐个实测） |
| 注入工具 | `tmp/main_s20_step.py`（四步法：snap / inject / restore / state，每次独立进程） |
| 被测套件 | `scripts/smoke.py`（末行「通过 N / 失败 0」为唯一真值口径） |
| 台账写入时间 | 2026-10-30 |

### 为什么用四步法而不是一体脚本

`tmp/main_s20_step.py` 的 docstring 写明了原因（逐字引用）：

> 一体脚本的还原只在 `finally` 里，进程被 timeout/SIGKILL 杀掉时 `finally` 不执行，
> 工程会留在注入态（本轮真实踩过：`daily.py` 被留成「电脑」分支，差点交出假绿）。
> 所以拆成三条**幂等**子命令，每次都是一个独立进程，跑完即退。

## 如何复现

```bash
cd 01-Projects/digital-life
export PYTHONUTF8=1
unset DL_DB DL_WORK_DIR

# 1. 确认当前是基线态（未注入）
python scripts/smoke.py 2>&1 | tail -1

# 2. 记基线 sha → 注入 → 跑 → 还原 → 复跑（以 drop 为例）
python tmp/main_s20_step.py snap  digitallife/render/hourbar.py
python tmp/main_s20_step.py inject digitallife/render/hourbar.py drop
python scripts/smoke.py > tmp/s20_drop_red.log 2>&1; tail -3 tmp/s20_drop_red.log
python tmp/main_s20_step.py restore digitallife/render/hourbar.py drop
python scripts/smoke.py > tmp/s20_drop_green.log 2>&1; tail -1 tmp/s20_drop_green.log

# 3. 确认已回基线
python tmp/main_s20_step.py state digitallife/render/hourbar.py drop   # 应输出「基线态」
```

> ⚠️ **复现前必读**：原始日志产生于 N6 **之前**，注入器脚本内部写的还是旧路径。
> 直接用当前代码跑会报「目标不存在」。详见文末「局限」第 3 条。

---

## 主表：注入 → 变红 → 还原 → 复绿

| 断言ID | 断言名称 | 注入的缺陷 | 红时输出（真值） | 绿时输出（真值） | 日志文件 |
|---|---|---|---|---|---|
| **T9** | 日报跨设备并列两节（各用真实设备名） | `devname`：`daily._device_label` 里加 `if dev.startswith("pc"): return "电脑"`，把设备名折叠回「电脑」 | `通过 206 / 失败 4` ｜ `[FAIL] T9 日报跨设备并列两节（各用真实设备名） ---` | **缺绿侧证据** | `s20_devname_red.log` |
| **T9a2** | 不再出现折叠式设备标题 | 同上（`devname`） | `通过 206 / 失败 4` ｜ `[FAIL] T9a2 不再出现折叠式设备标题 ---` | **缺绿侧证据** | `s20_devname_red.log` |
| **T17d** | 单机时标题用设备真名「## D-001 使用」 | 同上（`devname`） | `通过 206 / 失败 4` ｜ `[FAIL] T17d ... 「## D-001 使用」 ## 电脑使用` | **缺绿侧证据** | `s20_devname_red.log` |
| **T17d2** | 不再出现折叠式的「## 电脑使用」 | 同上（`devname`） | `通过 206 / 失败 4` ｜ `[FAIL] T17d2 ... 「## 电脑使用」 ## 电脑使用` | **缺绿侧证据** | `s20_devname_red.log` |
| **T9a3** | 元信息行中/西文相邻处有空格（设备是 D-001，不是设备是D-001） | `cjkgap`：`_cjk_gap_before` 改成 `if False: return " " + s`，不补空格 | `通过 210 / 失败 1` ｜ `[FAIL] T9a3 ... 设备是 D-001，不是设备是D-001 ---` | `通过 211 / 失败 0` | `s20_cjkgap_red.log` / `s20_cjkgap_green.log` |
| **T66a** | ①行数守恒 Σ(data-c) == 全量条数（650 条） | `drop`：`items = sorted(...)[:POINT_CAP]` 直接截断前 200 条 | `通过 207 / 失败 3` ｜ `[FAIL] T66a ... Σ=200 真实=650` | `通过 210 / 失败 0` | `s20_drop_red.log` / `s20_drop_green.log` |
| **T66b** | ②桶数受限 <= POINT_CAP(200) | `truncate`：`if len(items) <= POINT_CAP` 改成 `if True`，全部当逐点画、不做聚合 | `通过 207 / 失败 3` ｜ `[FAIL] T66b ... 标记=650 cap=200` | **缺绿侧证据** | `s20_truncate_red.log` |
| **T66c** | ③桶 href == 桶内第一条事件的锚点（独立分桶复算） | `drop` 与 `truncate` 两条注入都能触发 | `通过 207 / 失败 3` ｜ `[FAIL] T66c ... got=['timeline-pc-main-r1', 'timeline-pc-main-r2', 'timeline-pc-main-r3'] want=['timeline-pc-main-r1', 'timeline-pc-main-r3', 'timeline-pc-main-r5']` | `通过 210 / 失败 0`（drop 侧） | `s20_drop_red.log` / `s20_drop_green.log` / `s20_truncate_red.log` |
| **T66d** | 桶宽 == max(60, min(900, ceil(span/200)))（黄金基线公式） | `drop` 与 `truncate` 两条注入都能触发 | `通过 207 / 失败 3` ｜ `[FAIL] T66d ... 实得 60 期望 98` | `通过 210 / 失败 0`（drop 侧） | `s20_drop_red.log` / `s20_drop_green.log` / `s20_truncate_red.log` |
| **T66g** | 全是短事件（有效点 0）也必须渲染定位条（全量口径，DL-19） | `earlyexit`：`has_items = any(rows_of_segment(...))` 改成 `bool(collect(ctx))`，退回旧口径早退 | `通过 212 / 失败 1` ｜ `[FAIL] T66g ... collect=0 render_len=0` | `通过 213 / 失败 0` | `s20_early_red.log` / `s20_early_green.log` |

### 注入点与原文片段的完整定义

| key | 被测文件（**当时路径**） | 原文片段 → 注入片段 |
|---|---|---|
| `devname` | `digitallife/daily.py` | `return _dev_mod.name_of(dev) or dev` → `if dev.startswith("pc"): return "电脑"` + 原句 |
| `cjkgap` | `digitallife/daily.py` | `if s[0].isascii() and s[0].isalnum(): return " " + s` → `if False: return " " + s` |
| `truncate` | `scripts/slice_pages/hourbar.py` | `if len(items) <= POINT_CAP:` → `if True:  # 注入：把所有行都当「逐点」` |
| `earlyexit` | `scripts/slice_pages/hourbar.py` | `has_items = any(rows_of_segment(ctx, seg) for seg, _ in SEGMENTS)` → `has_items = bool(collect(ctx))` |
| `drop` | `scripts/slice_pages/hourbar.py` | `items = sorted(items, key=lambda x: x["sec"])` → 同句 + `[:POINT_CAP]` |

---

## 汇总（一屏核对）

| 注入 key | 红时 | 绿时 | 双向闭环 | 守住断言数 |
|---|---|---|---|---|
| `devname` | `通过 206 / 失败 4` | **缺绿** | ❌ | 4 |
| `cjkgap` | `通过 210 / 失败 1` | `通过 211 / 失败 0` | ✅ | 1 |
| `drop` | `通过 207 / 失败 3` | `通过 210 / 失败 0` | ✅ | 3（T66a/c/d） |
| `truncate` | `通过 207 / 失败 3` | **缺绿** | ❌ | 3（T66b/c/d） |
| `earlyexit` | `通过 212 / 失败 1` | `通过 213 / 失败 0` | ✅ | 1 |

**统计**：8 个日志 / 5 个注入点 / **3 个有完整红绿闭环**，2 个只有红侧。

> ⚠️ **绿侧通过数 210/211/213 不一致是正常的**：注入发生在不同时间点，期间断言总数在增长。
> **判断是否复绿只看「失败 0」**，不比通过数。

---

## 本台账的局限

### 1. 2 个注入点没有绿侧证据

`devname` 与 `truncate` 只有 `_red.log`。这两条的「还原后复绿」没有直接证据，
只能靠同期其它 green 日志间接推断。**按 S20 原教旨，这两条不算闭环。**

### 2. 95.8% 的断言从未做过 S20

```
冒烟断言总数（当前实测）：213
本台账覆盖：9 条（T9, T9a2, T9a3, T17d, T17d2, T66a, T66b, T66c, T66d, T66g → 去重 10 条）
覆盖率：10 / 213 ≈ 4.7%
```

**没做 S20 的断言占约 95%**，它们目前只有「跑绿」这一项证据——按 S20 定义「只跑绿不算通过」。
这不是本台账能补的，需要逐个设计可观测的缺陷注入。

### 3. 注入器脚本已与代码结构脱节（**最重要**）

`tmp/main_s20_step.py` 的 `INJECTIONS` 表里写的全是 **N6 重构前**的路径：

| key | 脚本里写的 | N6 后的真实路径 | 现状 |
|---|---|---|---|
| `devname` | `digitallife/daily.py` | `digitallife/daily/`（包，6 文件） | ❌ 目标不存在 |
| `cjkgap` | `digitallife/daily.py` | 同上 | ❌ 目标不存在 |
| `truncate` | `scripts/slice_pages/hourbar.py` | `digitallife/render/hourbar.py` | ❌ 目标不存在 |
| `earlyexit` | `scripts/slice_pages/hourbar.py` | `digitallife/render/hourbar.py` | ❌ 目标不存在 |
| `drop` | `scripts/slice_pages/hourbar.py` | `digitallife/render/hourbar.py` | ❌ 目标不存在 |

**结论：这 5 条注入现在一条都跑不起来。**
要复现必须先更新 5 个路径，并重新确认 5 个原文片段在新文件里仍然存在（`daily.py` 已拆成 6 个文件，片段可能跨文件）。

### 4. 日志可能已过期

| 事实 | 值 |
|---|---|
| 日志采集于 | 2026-10-29 22:39 – 23:13 |
| 台账写入于 | 2026-10-30 |
| 期间代码变动 | N6 目录重构（31 个 rename）、N1 CSS 外链化、`daily.py` 拆成 6 文件 |

当前 smoke 实测 `通过 213 / 失败 0`，与 `s20_early_green.log` 数字一致；
这只说明**断言总数没变**，**不能证明**五条注入仍会产生同样的红值（文件与结构已变，见第 3 条）。

### 5. 本台账不含任何新的验证

**所有数字都是抄自磁盘上已有的日志。本轮没有重跑任何注入。**
要确认这些结论今天仍成立，必须按「如何复现」重跑，并先修好第 3 条的路径问题。
