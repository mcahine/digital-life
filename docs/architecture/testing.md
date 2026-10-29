# digital-life 测试策略

> **产出**：子代理3｜**采集日期**：2026-10-25
> **采集环境**：`01-Projects/digital-life`，MSYS2 bash，`PYTHONUTF8=1`
> **分析脚本**：`tmp/a3_smoke_precise.py`、`tmp/a3_smoke_ast.py`、`tmp/a3_smoke_diff.py`、`tmp/a3_cover.py`

---

## 0. 一句话判决

**现有测试是「一个大脚本 187 条断言」，质量不低但形态不对：它把 43 个模块压成一条进程，
26 个模块零覆盖，且零 pytest 单测。补测试的第一步不是「多写断言」，是「让断言能独立失败」。**

---

## 1. 现有测试盘点（真实数字）

### 1.1 只有一个测试文件

```
$ find . -name "test_*.py" -o -name "*_test.py" -o -name "conftest.py" -o -name "tests" -type d 2>/dev/null | grep -v tmp
(空)
$ cat pyproject.toml
[project]
name = "digital-life"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = ["httpx>=0.27"]
[project.optional-dependencies]
ocr = ["pillow>=10.0", "rapidocr-onnxruntime>=1.4"]
[tool.uv]
package = false
```

**零 pytest、零 `tests/` 目录、pytest 不在任何依赖里。** 全部回归靠 `scripts/smoke.py` 一个 1366 行脚本。

### 1.2 断言数：三个口径，必须分清

这是本文最重要的一节——**「181 / 143 / 100」三个数字的来源必须厘清**。

```
$ python tmp/a3_smoke_precise.py
=== smoke.py 断言口径核算 ===
静态 check(...) 调用点总数           : 205
  其中 except 分支的失败哨兵        : 24  （正常运行不触发）
  其中 正向断言（正常路径）          : 181
实跑 PASS 条数                      : 187
核对：正向断言 181 条 + 循环内动态生成 = 实跑 187 条（差 6 条来自 for 循环）
```

| 口径 | 数值 | 含义 |
|---|---|---|
| `check(...)` 静态调用点 | **205** | AST 全量扫出的字面调用 + 1 个动态标签 |
| 其中 `except` 分支的失败哨兵 | **24** | 如 `check("T7 dbview 可用", False, ...)`，只在异常时触发 |
| **正向断言** | **181** | 205 − 24 = 181 |
| **实跑 PASS** | **187** | 181 + 6 条由 `for` 循环动态生成（T13 的 schema 字段族）|

**结论：AGENTS.md 写的 181 是准确的**（指正向断言数），实跑输出 187 是因为循环展开。

#### 三个数字的真伪判定

| 数字 | 出处 | 真伪 | 说明 |
|---|---|---|---|
| **181** | `01-Projects/digital-life/AGENTS.md:69,384,406`、`README.md:37,71` | ✅ **准确** | 与「正向断言 181」完全吻合 |
| **100** | 根 `AGENTS.md:237` | ❌ **过时** | 比实际少了 81 条，是较早版本的数字 |
| **143** | 任务书（子代理分工）提到 | ❌ **不存在** | 全库 grep 无此数字；真实历史是 **148 → 153**（见 `docs/postmortem/vision-perf-and-blind-fix-2026-10-23.md:96`）|

```bash
$ grep -rn "143 项\|143项" --include=*.md .    # → 无输出
$ grep -rn "smoke.py.*项\|主回归.*项" --include=*.md 01-Projects/digital-life AGENTS.md
01-Projects/digital-life/AGENTS.md:69:  smoke.py  主回归套件（**181 项**，...）
01-Projects/digital-life/AGENTS.md:384:python scripts/smoke.py    # 181 项主回归
01-Projects/digital-life/AGENTS.md:406:1. `python scripts/smoke.py` → **必须 181/0**
01-Projects/digital-life/README.md:37:| 主回归 | `scripts/smoke.py` **181 项** |
AGENTS.md:237:python scripts/smoke.py    # 主回归 100 项
```

> **建议**（对应 AGENTS.md 自己的维护约定）：数字会漂移，**只写命令不写数字**。
> 已经在 2026-10-25 的 AGENTS.md 修订里落地为「项数以末行『通过 N / 失败 0』为准」。
> 根 `AGENTS.md:237` 的 100 仍待同步（属主代理/子代理4 负责）。

### 1.3 覆盖了哪些模块（真实覆盖核算）

```
$ python tmp/a3_cover.py
=== 全项目模块数: 43 ===
=== smoke 直接 import 的模块数: 17 ===

=== smoke 直接 import 到的模块 ===
  ✓ digitallife.clean          ✓ digitallife.daily        ✓ digitallife.dbview
  ✓ digitallife.llm            ✓ digitallife.model        ✓ digitallife.schema_check
  ✓ digitallife.slicer         ✓ digitallife.sources.iqqz3 ✓ digitallife.store
  ✓ digitallife.timeline_merge ✓ digitallife.tz           ✓ digitallife.vision
  ✓ scripts.batch_vision       ✓ scripts.build_slice_html ✓ scripts.rebuild_daily
  ✓ scripts.slice_pages.timeline  ✓ scripts.slice_pages.vision

=== smoke 未直接 import 的模块 (26) ===
  ✗ digitallife.publish          495 行（快照，实时值请用 wc -l 复核）   ← 最严重
  ✗ digitallife.cli              246 行
  ✗ digitallife.schema           187 行
  ✗ digitallife.prompts.segment  147 行
  ✗ digitallife.devices          136 行   ← 注：T16 通过 model.DEVICES 间接测
  ✗ digitallife.sources.screenmemo 319 行
  ✗ digitallife.sources.manictime  244 行
  ✗ digitallife.sources.tai        210 行
  ✗ digitallife.sources.shion      166 行
  ✗ scripts.slice_pages.base       400 行
  ✗ scripts.slice_pages.linktitle  254 行
  ✗ scripts.slice_pages.contract   195 行
  ✗ scripts.slice_pages.web        145 行
  ✗ scripts.slice_pages.apps        82 行
  ✗ scripts.rewrite_style          199 行
  ✗ scripts.finish_phone2          164 行
  ✗ scripts.fetch_titles            99 行
  ✗ scripts.prune_results           74 行
  ✗ digitallife.ocr                104 行
  ✗ digitallife.paths               58 行
  ✗ digitallife.sources.base        80 行
  ✗ digitallife.sources.__init__    19 行
  ✗ digitallife.__init__             6 行
  ✗ digitallife.prompts.__init__     4 行
  ✗ scripts.slice_pages.__init__     1 行
  ✗ scripts.smoke                 1366 行  （自身）
```

**43 个模块，直接覆盖 17 个（39.5%），26 个零直接覆盖。**

#### 零覆盖里的真缺口排序（按风险 × 行数）

| 排 | 模块 | 行数 | 为什么危险 |
|---|---|---|---|
| 1 | `publish.py` | 495 | **发布是最终产出**，写错就是坏 md/坏 manifest，而它完全没有断言 |
| 2 | `slice_pages/web.py` | 145 | 网站 section 的域名聚合逻辑（含上面说的 `_domain_of` 重复实现）|
| 3 | `slice_pages/base.py` | 400 | `fmt_dur`/`esc`/`page` 全站的格式化底座，错一位全站错 |
| 4 | `slice_pages/linktitle.py` | 254 | DL-15 声明的「链接标题单一真相函数」，**零断言** |
| 5 | `sources/manictime.py` | 244 | 主数据源，解析错静默丢行 |
| 6 | `sources/screenmemo.py` | 319 | 手机侧唯一来源，含 666 条全空的坑 |
| 7 | `schema.py` | 187 | DDL + 迁移（DL-16 的顺序陷阱）|

> 注：部分模块（如 `devices`）通过 `model.DEVICES` 或 `daily` 间接被测到，
> 上表是「**没有直接 import**」的严格口径，不等于完全没测。但 `publish` 确实一条都没有。

### 1.4 187 条断言的分布（按标签族）

```
$ grep -oE '^\[PASS\] T[0-9]+[a-z]?' tmp/a3_smoke_run.log | sort | uniq -c | sort -rn | head
    187 [PASS]
```

按 T 编号分组（来自 `grep -n '    # ---------- ' scripts/smoke.py`）：

| 族 | 覆盖主题 | 条数（实跑） |
|---|---|---|
| T1 | 时间归一 | 4 |
| T2 | event_id 幂等 | 1（+ 循环） |
| T3 | 幂等写入 | ~4 |
| T4 | FTS | ~3 |
| T5 | 清洗规则 | ~6 |
| T6 | 构造性探查（S19） | ~5 |
| T7 | dbview 剔除离开时段 | 4 |
| T8 | slicer 截图守恒与二分 | ~6 |
| T9 | 日记边界（跨设备分账） | ~5 |
| T10 | 切片主轴（纯 power 窗不建片） | 4 |
| T11 | 冲突计数判别力 | 6 |
| T12 | 手机屏幕态 + 截图证伪 | ~4 |
| **T13** | **结构化校验器 R1–R9** | **11**（最多）|
| T14 | 日报细节完整性 | 8 |
| T15 | IQQZ3 多快照 | 2 |
| T16 | 设备注册表 | 8 |
| T17 | 日报 markdown 格式 | 4 |
| T18 | 设备别名贯穿查询 | 6 |
| T19 | 时间线归并剪枝 | 10 |
| T20 | 单日页 | 11 |
| T21 | 多手机 + 手机侧视觉 | 5 |
| T22 | max_tokens 上限 | 3 |
| T23 | 日报链接结构 | 3 |
| T24 | 裸连接兜底 | 3 |
| T25 | 无描述标注 | 4 |
| T26 | 日报无描述标注 | 4 |
| T27 | 视觉图片送达（DL-10 哨兵）| 5 |
| T28 | 送图上限 | 5 |
| T29 | 预算策略 | 3 |
| T30 | 429 退避 | 5 |
| T31 | 额度看门狗 | 8 |

**分布判断**：覆盖严重偏斜——**视觉通道（T22/T27/T28/T29/T30/T31）占了 29 条**，
因为那块出过最多事故；而**发布（publish）与呈现（slice_pages 大部分）几乎为 0**。
这符合「按事故驱动补测试」的直觉，但留下了一个巨大的盲区：**最终产出的正确性没人守**。

---

## 2. 假绿风险（现有断言里哪些实际上永远不会失败）

这一节是本文最有价值的部分——**绿不等于有效**。

### 2.1 风险 A：`except Exception` 兜底把异常变成一条「已知失败」

smoke 里有 **24 条**这种模式：

```python
# scripts/smoke.py:176-210（T7 示例）
    try:
        from digitallife.dbview import build_day_structure
        ...
        check("T7 C13 active_s 剔除与 Away 重叠的时段", ...)
        check("T7d 剔除生效的判别哨兵", ...)
        check("T7b 结构视图字段齐备", ...)
        check("T7c 应用时长来自 event", ...)
    except ImportError:
        print("[SKIP] T7 dbview 尚未就绪")
    except Exception as e:  # noqa: BLE001
        check("T7 dbview 可用", False, f"{type(e).__name__}: {e}")
```

**风险**：正常运行时不触发（所以实跑 187 < 205 正是这个原因）。这是**设计正确**的——
异常时它会变红。但要注意：它把「4 条细粒度断言」压缩成「1 条粗粒度失败」，
**定位信息丢失**。一旦 T7 崩了，你只知道「dbview 可用 = False」，不知道是 active_s 算错还是字段缺失。

**建议**：保留兜底，但把 `detail` 里带上已完成到哪一条（如 `已通过 T7a-T7c，T7d 抛错`）。

### 2.2 风险 B：`except ImportError: print("[SKIP]...")` —— 静默跳过只打印一行

```
$ grep -n 'SKIP' scripts/smoke.py
208:        print("[SKIP] T7 dbview 尚未就绪")
250:        print("[SKIP] T8 slicer 尚未就绪")
324:        print("[SKIP] T12 dbview 尚未就绪")
346:        print("[SKIP] T11 vision 尚未就绪")
385:        print("[SKIP] T10 slicer 尚未就绪")
422:        print("[SKIP] T9 daily 尚未就绪")
463:        print("[SKIP] T13 schema_check 尚不可用")
496:        print("[SKIP] T14 daily 工具函数不可用")
```

**这是真正的假绿风险**：如果哪天有人把 `digitallife.dbview` 改名或删掉，
T7 会**打印一行 SKIP 然后继续**，最终输出仍是「通过 183 / 失败 0」——**退出码 0**。

```python
# smoke.py 结尾（L1350-1355，逻辑示意）
print(f"\n通过 {len(passed)} / 失败 {len(failed)}")
if failed:
    print("失败项：" + ", ".join(failed))
    sys.exit(1)
```

**只检查 `failed`，不检查「应该跑却没跑」**。

**修法**（优先级最高，成本 20 行）：

```python
SKIPPED: list[str] = []
...
    except ImportError:
        SKIPPED.append("T7")
...
# 结尾
if SKIPPED:
    print(f"⚠️ 跳过 {len(SKIPPED)} 组：{SKIPPED}")
    # 关键：模块缺失是回归，不是「环境未就绪」
    sys.exit(1)
```

### 2.3 风险 C：弱断言（只判「不是 None」）

```python
# 典型形态（T16 附近）
check("T16a ...", _names is not None)
```

这类断言抓到的是「函数没崩」，抓不到「算错了」。
当前 187 条里大部分是**强断言**（含具体期望值 + detail 带实际值），这是优点——
smoke 的作者显然懂「断言要有判别力」。但仍有零星弱断言，建议逐条升级为具体值比对。

### 2.4 风险 D：循环内断言的索引依赖

```python
# scripts/smoke.py:458 附近（T13）
for _name in _must:
    check('T13 抓出「%s」' % _name, _v.ok is False, ...)
```

6 条断言由循环生成。**若 `_must` 列表被改短，断言数会静默减少**而总数只反映在末行数字上。
这正是「数字漂移」的根源——也是为什么 AGENTS.md 现在改成「以末行为准」而非写死 181。

### 2.5 风险 E：环境变量顺序依赖

```python
# scripts/smoke.py:22-23 —— 必须在任何 digitallife import 之前执行
os.environ["DL_DB"] = str(Path(_TMP) / "smoke.sqlite")
os.environ["DL_WORK_DIR"] = str(Path(_TMP) / "work")
```

因为 `vision.py:46/224/225/235/244/245` 的常量在 **import 期**求值。
一旦有人把 `from digitallife.vision import ...` 提到这两行之前，测试会用**生产库**跑——
后果不是失败，是**在真实数据上留下残留**（S16 违规）。

**当前靠注释和位置保证，没有任何机制拦住。**

---

## 3. 测试金字塔建议

### 3.1 第一层：pytest 单测（纯函数，零 IO）

**目标**：把 181 条大杂烩里**能脱离数据库独立验证**的部分抽出来，做到「改一行代码，3 秒内知道对错」。

#### 最该先测的 10 个纯函数（按投入产出比排序）

| # | 函数 | 位置 | 为什么最该先测 | 能抓住哪次真实事故 |
|---|---|---|---|---|
| 1 | `timeline_merge.merge_browser_web` | `timeline_merge.py` | 四层归并，**最复杂且零单测**；一个判据错就是全库时间线重复 | 用户原话「都不是一个事件的为什么要搞一个次级列表」——按「重叠最长」配对把 Obsidian 窗口塞进 12 个豆包会话 |
| 2 | `clean.merge_adjacent` | `clean.py` | 纯函数，输入输出都是 list | **DL-3**：混流不按 `(device,kind)` 分流 → 合并次数 687 vs 应有 46,665（差 67 倍）|
| 3 | `model.dedupe_key` / `event_id` | `model.py` | 幂等主键，纯哈希 | **DL-2**：漏掉电量字段 → 同秒同标题不同电量被合并，**静默丢 11,075 行** |
| 4 | `devices.query_aliases` / `sql_device_filter` | `devices.py` | 纯逻辑，返回列表/SQL 片段 | **DL-7**：设备 id 从 `pc`/`phone` 改成 `pc-main`/`phone-1` 后，9 处精确匹配**全部静默返回空** |
| 5 | `schema_check.validate_structured` | `schema_check.py` | R1–R9，输入 dict 输出 Verdict | 已有 T13（11 条，最多的族）——**说明作者也认为它最值得测**，但仍在 smoke 里 |
| 6 | `clean.is_empty_event` / `normalize_event` | `clean.py` | 纯函数 | **DL-4**：默认 `min_duration_s=30` 丢掉 23,998 段 = 15.6% 真实时长 |
| 7 | `tz.parse_flexible` / `parse_hms_to_seconds` | `tz.py` | 纯字符串解析，边界多 | 时间解析错了，全库数据整体偏移 |
| 8 | `timeline_merge.display_title` | `timeline_merge.py` | 纯字符串，有清晰契约 | **DL-15**：同一 URL/标题在时间线与网站两处显示不一致 |
| 9 | `dbview._domain_of` vs `web._domain_of` | 两处 | **两份实现必须逐字节等价** | 同一域名在两处聚合出不同结果 |
| 10 | `vision._is_degenerate` / `_sample_evenly` | `vision.py` | 纯字节/列表运算 | **DL-11**：一张 1280×20 的畸形 webp 单独发送就必触发 413，**毒掉整片** |

**具体测试文件建议**：

```
tests/
  conftest.py                # 提供临时库 fixture（见 3.2），不依赖 DL_DB 环境变量
  test_timeline_merge.py     # 四层归并：T19a-T19j 的 pytest 化 + 边界补全
  test_clean.py              # DL-3 / DL-4 的回归锚点
  test_model_idempotent.py   # DL-2 的回归锚点
  test_devices_aliases.py    # DL-7 的回归锚点
  test_schema_check.py       # R1-R9 逐条
  test_tz.py                 # 时间解析边界
  test_vision_images.py      # _is_degenerate / _sample_evenly（DL-11）
  test_textutil.py           # 抽出来的 _parse_json/_cell/_hms（防止再次分叉）
  test_urlutil.py            # 抽出来的 _domain_of（两份合一后）
```

**每个测试必须带「能抓住什么真实回归」的注释**，格式：

```python
def test_merge_adjacent_splits_by_device_kind():
    """DL-3 回归锚点。

    能抓住的真实事故：若把 (device, kind) 分流去掉，对混合流做相邻合并，
    合并次数会从 46,665 掉到 687（差 67 倍），表现为日报时间线大幅缩短。
    注入验证：把 merge_adjacent 的 groupby 键改成单一流 → 本用例必须变红（S20）。
    """
```

### 3.2 第二层：集成测试（必须用临时库，S16）

**铁律**：任何碰数据库的测试，**必须建临时库**，不得用 `.data/digital-life/digital-life.sqlite`（912 MB 真库）。

```python
# tests/conftest.py 建议形态
import os, tempfile, pytest
from pathlib import Path

@pytest.fixture
def tmpdb(tmp_path, monkeypatch):
    """每个用例一个全新空库。绝不用 DL_DB 指向真库（S16）。"""
    db = tmp_path / "test.sqlite"
    monkeypatch.setenv("DL_DB", str(db))
    monkeypatch.setenv("DL_WORK_DIR", str(tmp_path / "work"))
    from digitallife.schema import init_db
    from digitallife.store import open_db
    con = open_db(str(db))
    init_db(con)
    yield con
    con.close()
```

> ⚠️ **注意**：由于 `vision.py` 的常量在 import 期求值，`monkeypatch.setenv` 对它们**无效**。
> 必须先修这个设计（改成函数内读或提供 `reset_config()`），否则这些用例只能靠
> `importlib.reload` 或子进程隔离。**这是「先修架构再补测试」的具体案例。**

**必须用临时库的测试**：

| 测试 | 为什么不能 mock | 对应事故 |
|---|---|---|
| `store.events_between` 区间查询 | 要验真实 SQL 谓词的边界行为 | **DL-5**：写成 `end IS NULL` 单列 → 全表捞进来（单天 218,356 行，2.69s vs 0.10s）|
| `store.upsert_events` 幂等 | 要验 `INSERT OR IGNORE` + 唯一键的真实行为 | **DL-2** |
| `schema.init_db` 迁移顺序 | 要验真实 DDL 执行顺序 | **DL-16**：先建索引后补列 → `no such column` |
| `slicer.build_slices` 截图守恒 | 要验 `SUM(image_count)` == `kind='screen'` 计数 | 二分逻辑错了会静默丢图 |
| `dbview.build_day_structure` | 要验真实时间区间运算 | **DL-8**：点事件不补 `POINT_EVENT_MS` → shion 28,805 条全被丢 |
| `publish.publish_all` | 零覆盖，且产出是最终交付物 | 无历史事故，**但是最大的空白** |

**性能回归测试**（防 DL-9）：

```python
def test_load_slices_no_n_plus_one(tmpdb):
    """DL-9 回归锚点：循环里不得有 SQL。

    能抓住的真实事故：给「无画面描述」加标注时逐片查 event 表，
    单日 load_slices 从 1.6s 退化到 103s，753 页重建从 40 分钟变 5 小时。
    断言口径：用 sqlite 的 trace 回调统计 SQL 条数，必须与片数无关。
    """
    calls = []
    tmpdb.set_trace_callback(calls.append)
    load_slices(...)          # 单日 267 片
    assert len(calls) < 50, f"SQL 条数 {len(calls)} 与片数相关，疑似 N+1"
```

> 这条断言**比计时更可靠**——计时受机器影响，SQL 条数是结构性指标。

### 3.3 第三层：端到端（必须验真实产物）

**原则**（S21）：断言必须**直接消费真实产物文件**，不得在测试里重建路径或内容。

| 端到端测试 | 断言什么 | 能抓住什么 |
|---|---|---|
| 日报 md 产物 | `20-日报/<date>.md` 真实存在、省略号计数 0、无 `### 应用明细（全量`、确有自然段 | **DL-14**：恢复模板渲染会把当天每条 action 全列出（实测 2026-08-14 有 168 条、2026-09-15 有 248 条）|
| 时段页 html 产物 | `10-时段/<date>.html` > 50 KB、含 4 个 `<section>`、双设备日有 `role="tablist"` | 页面静默缺 section |
| 日报指路链接 | `10-时段/<date>.html#apps` 形式的锚点，且该 html 真存在该 id | **真实事故**：2026-10-22 把「日期目录+9页」并成单文件后，`daily.py` 链接模板仍写旧路径，**752 篇日报全部带坏链接** |
| manifest.json | `counts` 与库内 `COUNT(*)` 一致 | 发布与库不同步 |
| 视觉送达哨兵 | `_chat` 收到的是 data URL 而非 zip 相对路径 | **DL-10**：vision 报「missing=0」+ llm 报「23 张缺失」两层日志各自正常，合起来是「一张图都没送」，PC 侧 1,874 条全是盲总结 |

**端到端测试的运行成本**：建议**不进 CI 每次跑**，而是「发布前 + 每日一次」。

---

## 4. 测试先行落地顺序（不能颠倒）

| 阶段 | 动作 | 前置条件 | 预计 |
|---|---|---|---|
| 0 | **修 `pyproject.toml` 打包 + 删 `sys.path` hack** | 无 | 4h |
| 1 | 建 `tests/` + `conftest.py`（临时库 fixture） | 阶段 0 | 2h |
| 2 | 把 T19（时间线归并 10 条）搬进 pytest，**逐条做 S20 注入验红** | 阶段 1 | 1d |
| 3 | 补 `test_clean` / `test_model_idempotent` / `test_devices_aliases`（DL-2/3/4/7 锚点） | 阶段 1 | 1d |
| 4 | 补 `test_vision_images`（DL-11）+ SQL 条数断言（DL-9） | 阶段 1 | 1d |
| 5 | 给 `publish` 补端到端产物断言（最大空白） | 阶段 2-4 | 1d |
| 6 | **此时才允许拆 `daily.py`**（见 `layering.md` §5） | 阶段 2-5 | — |

> **这个顺序不能倒**。当前状态下拆 1196 行（快照，实时值请用 wc -l 复核）的 `daily.py`，等于在没有回归网的情况下做手术。

---

## 5. S20 注入验红的实操清单

AGENTS.md §F.5 要求「新增断言后故意注入缺陷必须变红」。**这条要求本身是对的，但执行难点在于构造触发条件**。

参考 S58 的教训（本轮真实踩坑）：

> 写的「None/str 混排排序」回归锚点，注入缺陷版后**仍然 49/0 全绿**。
> 真因：当时临时 vault 的未完成列表**全是 `date=None`**，`(None, mtime)` vs `(None, mtime)` 比较走到第二项，压根不报错。

**因此每条锚点的验收必须三步**：

1. 先注入缺陷版 → **必须变红**
2. 若不变红，**先查触发条件是否真被构造出来**（打印被测函数的实际入参/中间值），不要先怀疑注入手法
3. 补显式对照构造（如 S58 补 `N0 = "t8-有日期对照"` + `N1` 无 date，前置断言「两者同在返回列表里」），再复验变红

**本轮建议优先做注入验红的 5 个锚点**：

| 锚点 | 注入什么缺陷 | 期望 |
|---|---|---|
| `merge_adjacent` 分流 | 去掉 `(device,kind)` 分组 | 合并次数骤降，断言红 |
| `dedupe_key` | 从 key 里删掉 `title` 字段 | 同秒不同标题被合并，幂等断言红 |
| `events_between` 区间 | 改成单列 `end IS NULL` | 返回行数暴涨，断言红 |
| `_is_degenerate` | 恒返回 `False` | 退化图不再被过滤（需构造一张 1280×20 的测试图）|
| `load_slices` SQL 条数 | 在循环里加一次 `SELECT` | SQL 条数断言红 |

---

## 6. 与 AGENTS.md 数字漂移的治理（附建议条文）

**问题**：同一条命令的断言数在 4 处文档里出现过 100 / 143 / 148 / 153 / 181 / 187 六个数字。

**根因**：文档里抄了数字，而数字随代码变化。

**建议条文**（可直接并入根 AGENTS.md 的 S 表）：

> **S60 文档不得抄写会漂移的计数，只写获取它的命令**
> - Problem：文档写死「冒烟 181 项」，代码加断言后数字失效，读者按旧数字判断「少跑了」。
> - Standard：凡随代码变化而变化的量（断言数、文件数、行数、记录数），**文档只写命令 + 「以输出为准」**，
>   不写具体数值。确需写数值时，必须同时写「采集时刻 + 采集命令」。
> - Checkpoint：任何新写/修改的文档段落，若含裸数字，问一句「这个数字会随时间增长吗」；
>   会 → 改成命令。

**当前落实情况**：`01-Projects/digital-life/AGENTS.md` 已于 2026-10-25 改为
「项数以末行『通过 N / 失败 0』为准」——符合本条。
根 `AGENTS.md:237` 的「100 项」**仍是旧值**，需同步（属主代理/子代理4 范围）。

---

## 附录：可重放命令

```bash
cd 01-Projects/digital-life
export PYTHONUTF8=1

# 1. 断言口径核算
python tmp/a3_smoke_precise.py

# 2. 实跑一次（约 40s，出 187/0）
python scripts/smoke.py 2>&1 | tail -3

# 3. 模块覆盖核算
python tmp/a3_cover.py

# 4. 找未执行的断言（静态 vs 实跑差集）
python scripts/smoke.py > tmp/a3_smoke_run.log 2>&1
python tmp/a3_smoke_diff.py
```

---

## 7. 采集期间发现的真实缺陷（2026-10-25，可直接复现）

> 本节是 `scripts/smoke.py` 当前的**真实缺陷**，非分析推测。发现过程见
> `troubleshooting.md` 附录 D。

### 7.1 缺陷 1：兜底标签与守卫范围不符（会导致排障跑错方向）

```python
# scripts/smoke.py:1359-1380（T36 段）
        _vp_src = _insp2.getsource(_sd_vision.panel)
        _nbranch = sum(1 for ln in _vp_src.splitlines()
                       if ln.strip().startswith("meta = "))
        check("T36e 每张卡片都重算 meta（防止串用上一张卡片的元信息）",
              _nbranch >= 3, "meta 赋值处 %d 个" % _nbranch)
        ...
        check("T36f2 抓不到标题时保留原样（不抛异常、不阻塞建页）",
              "failed" in _lt_src and "status" in _lt_src, _lt_src[:80])
    except Exception as _e:  # noqa: BLE001
        check("T31 额度看门狗", False, f"{type(_e).__name__}: {_e}")   # ← 标签错了
```

**问题**：这个 `except` 守护的是 **T36 段**（L1330–1378），
但失败标签写的是 **「T31 额度看门狗」**（T31 在 L1177，是完全无关的另一组断言）。

**实测后果**（我今晚真跑遇到的）：

```
$ python scripts/smoke.py 2>&1 | tail -3
通过 185 / 失败 1
失败项：T31 额度看门狗
$ grep FAIL tmp/a3_smoke_final.log
[FAIL] T31 额度看门狗 NameError: name '_vp_src' is not defined
```

排障者看到「T31 额度看门狗失败」，会去查 `batch_vision._classify` / `wait_for_quota`
——**但根本问题在 T36 段**（`_vp_src` 是 `vision.panel` 的源码，属于视觉 section 的断言）。
**这条错误标签直接把人引到错误的模块。**

### 7.2 缺陷 2：兜底捕获了跨段的异常（作用域泄漏）

`_vp_src` 在 L1359 定义。既然 `NameError` 说它「未定义」，说明
**异常发生在 L1359 之前**（`_insp2.getsource(...)` 抛错），或者
**有一个更早的、不同缩进层级的 except 也在引用它**。

无论哪种，结论一样：**当前 T36 段是一个超长 try 块（约 50 行、6 条断言）**，
任何一条前置语句抛错，后面 6 条断言全部不执行，且**只报 1 条标签错误的失败**。
这与 `testing.md` §2.1 的风险 A 是同一个病，但更严重（标签还指错了段）。

### 7.3 缺陷 3：断言数与代码状态强耦合，无法作为验收基线

同一天内三次运行：

| 时刻 | 结果 | 失败项 |
|---|---|---|
| 12:0x | 187 / 0 | — |
| 12:3x | 191 / 1 | T36d2 |
| 12:4x | 185 / 1 | T31（实为 T36 段）|

**通过数从 185 到 191 波动**，因为并行子代理在改断言集。
这印证 `testing.md` §6 的 S60：**任何文档里的「187 项」都不该被当作验收基线**。

### 7.4 修复建议（三处，都是小改）

```python
# 修 1：标签必须与守卫范围一致
except Exception as _e:
    check("T36 标题真相函数与 meta 重算", False, f"{type(_e).__name__}: {_e}")

# 修 2：把 T36 的超长 try 拆成 3 个小 try（每段 2 条断言）
try:
    _vp_src = _insp2.getsource(_sd_vision.panel)
    ...
    check("T36e ...", _nbranch >= 3, ...)
except Exception as _e:
    check("T36e 每张卡片都重算 meta", False, f"{type(_e).__name__}: {_e}")

try:
    _lt_src = _insp2.getsource(_lt)
    ...
    check("T36f ...", ...)
    check("T36f2 ...", ...)
except Exception as _e:
    check("T36f 标题落库", False, f"{type(_e).__name__}: {_e}")

# 修 3：兜底的 detail 只允许引用异常对象本身，
#        不得引用 try 块内变量（块内变量正是「可能未赋值就抛错」的那批）
```

> **这条要转给负责 `smoke.py` 的子代理**（任务书说我不得改 `smoke.py`）。
> 修完后必须按 S20 做注入验红：故意让 `_sd_vision.panel` 抛异常，
> 确认失败项显示的是「T36 段」而不是「T31」。

---

## 8. 产物判据：为什么不能比整页 sha（2026-10-30 改造）

### 8.1 问题经过

N6 目录重构的验收判据是「753 篇 md + 753 个 html 逐字节不变」。
重构期间这条确实成立（`tmp/t4_verify_outputs.py` 跑了 4 次全 PASS）。

但紧接着的 **N1 改进**把每页内联的 14 KB `<style>` 换成了外链 `_assets/day.css`，
并**重新生成了全部 753 页**。于是：

```
python tmp/t4_verify_outputs.py   # 改判据之前
  === 753 篇 md 对拍 ===
  基线 753 | 缺失 0 | 变化 0
  === 753 个 html 对拍 ===
  基线 753 | 缺失 0 | 变化 753     <- 假失败
  判定: FAIL
```

**这不是产物被破坏，而是度量口径过期**（S46：修了指标却仍输出旧症状值）。
整页 sha 判据的隐含前提是「渲染代码不变」，N1 打破了这个前提。

### 8.2 新判据：md 看整篇，html 只看 body

| 产物 | 判据 | 基线文件 |
|---|---|---|
| `20-日报/*.md`（753 篇） | **整篇** sha256 | `tmp/t4_baseline.json` 的 `md` 键 |
| `10-时段/*.html`（753 个） | **body 段** sha256（`<body` … `</body>`，含两端标记） | `tmp/body_baseline.json` |

body 段提取必须**两处逐字一致**：
- 建基线：`tmp/body_baseline.py` 的 `body_sha()`
- 做验收：`tmp/t4_verify_outputs.py` 的 `body_sha()`

```python
def body_sha(p: Path) -> str:
    t = p.read_text(encoding="utf-8")
    i = t.find("<body")
    j = t.rfind("</body>")
    seg = t[i:j + 7] if i >= 0 and j > i else t
    return hashlib.sha256(seg.encode("utf-8")).hexdigest()
```

> **两处口径必须相同**：改任一处必须同步改另一处，否则基线侧与验收侧对不上，
> 属于 S47「同一真值字段两种口径」。

### 8.3 判据有效性证明（不能只跑绿）

新判据必须证明「能红能绿、且只在正文变化时红」。构造检验：

```bash
python tmp/t4_judge_selfcheck.py     # 只读，不修改任何真实产物
```

实测输出：

```
样本: 2026-08-13.html (508035 B)
基线 body sha: c7ff79d487d9e47c

G0 原文             body 判据: True   期望 True
G1 body 改1字符      body 判据: False  期望 False   <- 有辨别力
G2 head 改样式       body 判据: True   期望 True    <- 不误报 N1 类改动
G3 同上，整页 sha   判据: False  期望 False   <- 旧判据会误报

三组构造全部符合预期: True
结论: 判据有效（能红能绿，且只在正文变化时红）
```

**这段自检本身踩过一次 S58**，记录在此以免重犯：
最初 G2 用 `":root{"` / `".hb-p{"` 作替换锚点，但 N1 之后 head 里
**已没有任何内联 CSS**，锚点命中 0 次 → 替换静默不发生 → G2 与 G3 都是「原文」，
G3 因此得到 `True`（期望 `False`），自检报「判据无效」。
真因不是判据无效，而是**注入条件没被构造出来**。修正为：
锚点换成 `day.css`，并在替换前后各加一条 `assert` 锁死「条件成立」与「替换生效」。

> 通用教训：自检脚本里的每处注入，都要有 `assert` 证明它**真的改了东西**。
> 否则自检会以一种很难察觉的方式永远通过。

### 8.4 什么时候要重建基线

| 变更类型 | 是否重建 | 重建哪一份 |
|---|---|---|
| 纯重构（不改渲染输出） | 否 | —— 基线应保持不变，变了就是回归 |
| 改 `<head>` 里的样式/资源引用 | **否** | body 基线不受影响，这是分段判据的意义 |
| 改正文渲染逻辑（section 内容、排序、字段） | **是** | `body_baseline.json`，且必须**先说明为什么正文该变** |
| 改日报模板/叙事拼接 | **是** | `t4_baseline.json` 的 `md` |

> 重建基线是一个**声明性动作**，不是「让测试变绿」的手段。
> 重建前必须在 commit message 或文档里写清：正文为什么应该变、变了多少、
> 差异是否全部可归因。**不得为了消红而重建。**

### 8.5 可重放命令

```bash
cd 01-Projects/digital-life
export PYTHONUTF8=1

python tmp/t4_verify_outputs.py      # 主判据：md 整篇 + html body 段
python tmp/t4_judge_selfcheck.py     # 判据辨别力自检（只读）
python tmp/body_baseline.py check    # body 基线单独对拍
python tmp/body_baseline.py save     # 仅在正文确实该变时重建
```

期望：

```
=== 表一：753 篇日报 md（整篇 sha）===
基线 753 | 缺失 0 | 变化 0

=== 表二：753 个时段 html（body 段 sha，排除 head 内样式）===
基线 753 | 缺失 0 | 变化 0

=== 合并判定 ===
PASS —— md 整篇不变 + html 正文(body)不变
```
