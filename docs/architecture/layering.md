# digital-life 分层组织与软件架构诊断

> **产出**：子代理3（代码架构诊断与分层设计）｜**采集日期**：2026-10-25
> **采集环境**：`01-Projects/digital-life`，MSYS2 bash，`PYTHONUTF8=1`，`DL_DB`/`DL_WORK_DIR` 已 unset
> **分析脚本**：`tmp/a3_deps.py`、`tmp/a3_dup.py`、`tmp/a3_fan.py`、`tmp/a3_cover.py`、`tmp/a3_smoke_precise.py`
> **全部结论来自真实命令输出，不采信文档描述**（S36）

---

## ⚠️ 关于行号：本文用「符号锚点」而非「行号锚点」

采集期间有并行子代理在改 `daily.py`，实测行数在 40 分钟内从 **1062 → 1159 → 1196** 连续漂移。
因此本文的每条证据采用**双锚点**：

1. **符号名**（主锚点，抗漂移）——用 `grep -n "符号名" 文件` 即可重定位
2. **行号**（辅锚点，标注采集时刻）——仅供本文写作时对照

复验命令模板：

```bash
cd 01-Projects/digital-life
grep -n "^_TIMELINE_MIN_S = 120.0" digitallife/daily/    # 应命中 2 行
grep -n "from .daily import" digitallife/emit/publish.py         # 应命中 2 行
grep -n "from digitallife.timeline_merge import" digitallife/render/web.py
```

---

## 0. 一句话判决

**分层骨架是有的，方向大体也是对的，但边界漏了：发布层反向驱动加工层，呈现层直接抓领域层的私有函数。
高内聚低耦合「部分做到」——采集层和存储层做得好，加工层和发布层没做到。**

七段打分的判据全部是代码证据，见 §4。

---

## 1. 现状问题清单（按严重度排序）

### P0-1 呈现层 import 领域层的**下划线私有**符号

```
$ grep -n "from digitallife.timeline_merge import" digitallife/render/*.py
digitallife/render/web.py:10:from digitallife.timeline_merge import _clean_title, span_seconds
digitallife/render/timeline.py:16:from digitallife.timeline_merge import _clean_title, display_title, span_seconds
```

私有符号（`_` 前缀）的语义就是「本模块内部实现，外部不得依赖」。跨包 import 它，
等于把 `timeline_merge` 的**重构自由冻结**了：改 `_clean_title` 的签名或语义，两个 section 静默变形。

**全项目跨包私有符号引用 = 15 处**（AST 全量扫描）：

| 引用方 | 被引用私有符号 | 行号 |
|---|---|---|
| `slice_pages/web.py` | `timeline_merge._clean_title` | L10 |
| `slice_pages/timeline.py` | `timeline_merge._clean_title` | L16 |
| `scripts/finish_phone2.py` | `vision._collect_descriptions` | L36 |
| `scripts/finish_phone2.py` | `vision._slice_events` | L36 |
| `scripts/smoke.py` | `daily._md_link` | L467 |
| `scripts/smoke.py` | `daily._no_visual_basis` | L814 |
| `scripts/smoke.py` | `daily._render_device` | L581 |
| `scripts/smoke.py` | `daily._sites_summary` | L467 |
| `scripts/smoke.py` | `daily._span_seconds` | L467 |
| `scripts/smoke.py` | `daily._top_apps_section` | L893 |
| `scripts/smoke.py` | `timeline_merge._clean_title` | L804 |
| `scripts/smoke.py` | `timeline_merge._drop_page_echoes` | L647 |
| `scripts/smoke.py` | `vision._collect_descriptions` | L971 |
| `scripts/smoke.py` | `vision._conflicts` | L330 |
| `scripts/smoke.py` | `vision._sample_evenly` | L1047 |

> smoke.py 的 11 处性质较轻（测试本就要抓细节），但**它同样冻结重构**：想拆 `daily.py` 就得先改 11 条断言。
> 真正危险的是 **`web.py:10` / `timeline.py:16` / `finish_phone2.py:36`** —— 生产代码依赖私有符号。

**注**：采集期间 `_readable_from_url` 已从 `web.py` 的 import 中移除（原本还有它），
说明并行子代理正在朝正确方向改；但 `_clean_title` 仍在，问题未解。

---

### P0-2 发布层反向依赖加工层（方向倒挂，但有用户裁决背书）

```
$ grep -n "from .daily import" digitallife/emit/publish.py
346:    from .daily import build_daily
356:        from .daily import normalize_date
```

`publish`（发布层）在发布时**重新跑一次 `build_daily`**：

```python
# digitallife/emit/publish.py:404-410  _daily_text()
def _daily_text(con, day: str) -> str:
    """当日日报正文 —— **现算，不读库**（2026-10-24 用户裁决）。

    用户原话：「日报不需要存储到数据库，包括时段也是，本身就是基于数据库内容
    进行的导出」。
    ...
    """
    from .daily import build_daily
    res = build_daily(con, day, persist=False)
    return str((res or {}).get("output_text") or "").strip()
```

#### ⚠️ 重要澄清：这个反向依赖是**刻意设计**，不是疏忽

我最初把它判为「架构债」，实测后必须修正。**证据链**：

1. `daily.py:1161-1168` 注释写死了设计决策：
   ```python
   # 只存"模型叙事原文"，不存渲染结果（2026-10-24 用户裁决：
   # 「日报不需要存储到数据库…本身就是基于数据库内容进行的导出」）。
   #   - narrative 是唯一不可重算的东西（要么调用模型，要么丢）
   #   - output_text / structured / breakdown 全部可由 slice+event 现算
   # persist=False（publish 走的路径）连 narrative 都不写，只把结果返回。
   ```

2. **库里实测印证这个设计已落地**：
   ```
   $ sqlite3 ... "SELECT COUNT(*) FROM daily_summary WHERE LENGTH(output_text)>0"
   非空行数: 0 / 753        ← output_text 全部为空，确实是"不存渲染结果"
   $ 抽 2026-08-13 看 structured_json
   {"_narrative_only": true, "narrative": "# 2026-08-13 个人数字人生日报\n\n## 凌晨启动…"}
   ```
   `structured_json` 里只有 `_narrative_only: true` + `narrative`（模型原文，9,331 字）。
   **渲染结果真的一行都没入库**。

3. **反推出这个设计要解决的真实问题**：`publish` 若读库里的 `output_text`，
   就是读「历史渲染快照」——改了渲染代码却忘了重跑，vault 里就是旧正文。
   这正是 `publish.py:414-404` 注释说的「长期隐患」。
   现算 = 保证 vault 永远等于「当前代码 + 当前数据」的输出。**这个选择是对的。**

#### 但这仍然是分层违规——问题不在「现算」，在「daily 是个黑盒」

现算的代价是 `publish` 必须整块调用 `build_daily`，而 `build_daily` 里
**取数、结构计算、渲染、落库全混在一起**（见 P0-3）。于是产生三个实际后果：

| 后果 | 证据 |
|---|---|
| `publish` 耗时被 `daily` 绑死 | 753 天发布 = 753 次 `build_daily`，而它每次都重算 `device_structure`（每设备一次 DB 查询）|
| 想单测 `publish` 必须先跑通 `daily` 整条链 | `tests/` 零覆盖 `publish`（见 `testing.md` §1.3）|
| `normalize_date` 这种纯日期工具被迫跟着 `daily` 下沉 | `publish.py:425` —— 一个 3 行函数造成整层依赖 |

#### 正确的修法（不是「改读库」，那会退回旧隐患）

**把渲染拆出来，让 `daily` 和 `publish` 都依赖它，而不是互相依赖**：

```
现在（倒挂）：  publish ──▶ daily（黑盒，取数+渲染+落库全在里面）

目标（单向）：  publish ──┐
                          ├──▶ daily_render.render(...)   ← 纯函数：输入结构，输出 markdown
                daily  ───┘
                          └──▶ daily_collect(...)          ← 取数（被两者共用）
```

这样：
- `publish` **仍然现算**（保留 2026-10-24 用户裁决的正确性）；
- 但依赖的是 `daily_render` 的**公开函数**，不是 `daily` 整个模块；
- `normalize_date` 下沉到 `tz.py`，`publish` 不再需要任何 `daily` 的符号；
- `daily_render` 是**纯函数**，可直接单测（输入 dict，输出 str，零 IO）——这正是
  `testing.md` 里「最该先测」的形态。

> **架构判断**：当「导出」需要现算时，导出器就不是纯粹的「发布层」，
> 它变成了「编排层」。真正的问题不是「publish 调了 daily」，
> 而是「daily 把渲染和取数焊死了，导致别人只能整块调用它」。

---

### P0-3 `daily.py` 是巨型文件，一个文件里并存 9 种职责

```
$ wc -l digitallife/daily/
1196 digitallife/daily/     # 采集开始时是 1062，期间因其他子代理的改动增长
$ grep -c '^def \|^[A-Z_]* *=' digitallife/daily/
62
```

模块内的职责区块（按源码分节注释）：

| 区块 | 起始符号 | 职责 | 该去哪一层 |
|---|---|---|---|
| 时间与格式化 | `normalize_date` | `_hms`/`_hm`/`_fmt_dur`/`_cell`/`_parse_json` | 领域层工具 |
| 区间运算 | `_clip` | `_clip`/`_merge`/`_total`/`_intersect`/`_classify_power` | 领域层工具 |
| 当日结构 | `_fresh_structure` | `_fresh_structure`/`_focus_blocks`/`_structure_fallback`/`device_structure` | 领域层（本应属 `dbview`） |
| 当日切片 | `slices_for_day` | `slices_for_day`/`_event_counts` | 领域层（本应属 `slicer`） |
| markdown 渲染 | `_apps_list` | 20+ 个 `_render_*`/`_tl_line`/`_md_link`/`_apps_sentence` | **加工层 → 独立 renderer** |
| 叙事渲染 | `render_narrative` | `render_narrative`/`_device_narratives` | 加工层 |
| 模型概述 | `DAILY_PROMPT` | `DAILY_PROMPT`/`_llm_narrative` | 加工层 → 独立 prompt 模块 |
| 主入口 | `build_daily` | `build_daily`（约 170 行） | 编排 |

**最刺眼的是「当日结构」区块**：`daily.device_structure` 干的是 `dbview.build_day_structure` 的活。

```
$ grep -n "def device_structure" digitallife/daily/
259:def device_structure(con, date_key: str, device: str) -> dict:
$ grep -n "def build_day_structure" digitallife/domain/dbview.py
192:def build_day_structure(con: sqlite3.Connection, date_key: str, device: str | None = None) -> dict:
```

同一个概念两套实现并存，谁在什么条件下用哪一套，只有读代码才知道。

---

### P0-4 设备命名有四套口径（口径不一致 = 同一个设备在不同产出里改名）

| 实现 | 位置 | 返回什么 |
|---|---|---|
| `devices.label_of()` | `digitallife/domain/devices.py:120` | 注册表 `label` 字段（`主力电脑`/`手机 1`）|
| `contract.device_label()` | `digitallife/render/contract.py:36` | 注册表 `name` 字段（`D-001`/`iQQQZ3`）|
| `contract.Ctx.dev_label()` | `digitallife/render/contract.py:223` | 又一层包装 |
| `daily._device_label()` / `_device_heading()` | `digitallife/daily/:612/628` | 同类单台→「电脑/手机」；多台→`name_of()` |

```
$ grep -n "def label_of" digitallife/domain/devices.py
120:def label_of(device_id: str) -> str:
$ grep -n "def device_label\|def dev_label" digitallife/render/contract.py
35:def device_label(device: str) -> str:
176:    def dev_label(self) -> str:
$ grep -n "def _device_label\|def _device_heading" digitallife/daily/
612:def _device_label(dev: str) -> str:
628:def _device_heading(dev: str) -> str:
```

> 注：AGENTS.md §D.3 明确声明「日报里两条规则并存是有意的，勿统一掉」。
> 但**声明「有意」不等于已消除歧义**——同一设备在日报标题、日报正文、时段网页三处仍可能不同名。
> 这属于「业务上允许多形态」与「工程上应单点定义」没拆开的典型：正确做法是
> **单点定义一组策略函数**（`heading_of()` / `sentence_of()` / `display_of()`），
> 由 `devices.py` 持有策略，而不是让 `daily` 与 `contract` 各写一套。

---

### P1-5 同一概念多套实现（实测计数）

| 概念 | 实现处数 | 具体位置 | 是否已分叉 |
|---|---|---|---|
| **域名/URL 判定** | **5** | `dbview._domain_of`、`timeline_merge._readable_from_url`、`linktitle.host_of`+`is_hostlike`、`web._domain_of`、`web._is_url` | **是**：`dbview` 用模块级 `_HOST_OK` 正则，`web` 在函数内 `import re` 再重复同一正则 |
| **设备命名** | **4** | 见 P0-4 | **是** |
| **时长格式化** | **4** | `daily._fmt_dur:72`、`daily._span:357`、`base.fmt_dur:382`、`base.span:399`、`timeline_merge.span_seconds` | **是**：`daily._fmt_dur` 返回 `"1 小时 5 分"`，`base.fmt_dur` 返回 `"1 小时 05 分"`（补零）——**同一时长两种串** |
| **JSON 容错解析** | **2** | `daily._parse_json:89`、`publish._parse_json:140` | **逐字相同** |
| **结构化字段取用** | **3** | `daily._actions_of`、`web.items_of`、`contract.load_slices` | 各自实现 |

```
$ grep -n "def _domain_of" digitallife/domain/dbview.py digitallife/render/web.py
digitallife/domain/dbview.py:45:def _domain_of(url: str) -> str:
digitallife/render/web.py:22:def _domain_of(url: str) -> str:
$ grep -n "def _parse_json" digitallife/daily/ digitallife/emit/publish.py
digitallife/daily/:89:def _parse_json(s):
digitallife/emit/publish.py:209:def _parse_json(s):
$ grep -n "def _fmt_dur" digitallife/daily/; grep -n "def fmt_dur" digitallife/render/base.py
72:def _fmt_dur(seconds) -> str:
382:def fmt_dur(seconds) -> str:
```

**`_parse_json` 逐字重复的实证**：

```python
# digitallife/daily/:89-97
def _parse_json(s):
    if not s: return None
    if isinstance(s, (dict, list)): return s
    try: return json.loads(s)
    except (TypeError, ValueError): return None

# digitallife/emit/publish.py:209-148  ← 逐字相同
def _parse_json(s):
    if not s: return None
    if isinstance(s, (dict, list)): return s
    try: return json.loads(s)
    except (TypeError, ValueError): return None
```

这不是「复制粘贴忘删」，是**同一语义在两个层各自实现**。哪天 `daily` 那份改了容错范围，
`publish` 不会跟着改。

---

### P1-6 `vision.py` 里同名顶层函数定义了两次

```
$ grep -n "def _default_client" digitallife/produce/vision.py
440:def _default_client():
459:def _default_client():
```

```python
# digitallife/produce/vision.py:440-450
def _default_client():
    """统一走 llm.ChatClient（A 方案：删掉自带的 _SenseClient，消除重复实现）。

    历史：并行开发时 vision.py 曾自带一份最小直连客户端 _SenseClient，与 llm.py 的
    ChatClient 逻辑重复（S47 多真源）。现已删除，视觉通道只认 llm.ChatClient ——
    参数（thinking / max_tokens / image_url / 空内容重试）全部由 llm.py 单点持有。
    """
    from .llm import ChatClient
    return ChatClient()

# digitallife/produce/vision.py:459-463  ← 同名再定义一次
def _default_client():
    from .llm import ChatClient
    return ChatClient()
```

Python 里**后定义覆盖前定义**，那 17 行 docstring（记录了「曾自带 `_SenseClient`，
现已删除」的重要历史）**在运行时完全不可见**——`help()` 看不到，只有读源码的人看得到。
这是「重构做了一半」的化石。

**扫描命令**（全库重名顶层定义）：
```bash
python tmp/a3_dup.py    # 输出：重名顶层定义: digitallife/produce/vision.py {'_default_client': 2}
```

---

### P1-7 `daily.py` 有重复常量（值相同，所以永远不会被发现）

```
$ grep -n "^_TIMELINE_MIN_S = 120.0" digitallife/daily/
28:_TIMELINE_MIN_S = 120.0  # 日报只留单段 >=2 分钟的事件；碎片折叠，完整版进时段网页
30:_TIMELINE_MIN_S = 120.0  # 日报只留单段 >=2 分钟的事件；碎片折叠，完整版进时段网页
```

第 28 行与第 30 行逐字相同。值相同所以测试全绿——**只要哪天有人只改了第 28 行，
第 30 行会把它覆盖回去，而没有任何测试会变红**。这是典型的「假绿」温床。

---

### P1-8 兼容别名把已迁走的实现又挂回旧模块

```
$ grep -n "^_merge_browser_web = \|^_span_seconds = " digitallife/daily/
486:_merge_browser_web = merge_browser_web
497:_span_seconds = span_seconds
```

```python
# digitallife/daily/:485-486
# 兼容别名：合并逻辑已迁到 timeline_merge（旧名保留，smoke T14 仍 import 这个名字）
_merge_browser_web = merge_browser_web

# digitallife/daily/:496-497
# 兼容别名：实现已统一到 timeline_merge.span_seconds（smoke T14g/T14h 仍 import 这个名字）
_span_seconds = span_seconds
```

实现迁走了，旧名字留着，因为 `smoke.py:467` 还在 `from digitallife.daily import _span_seconds`。
结果是 `daily` 对外暴露的符号集合里混着「自己的实现」和「别人的实现的别名」，
调用方无法从 import 语句判断真正的依赖在哪一层。**测试成了重构的阻力**，方向搞反了。

---

### P1-9 全局可变状态污染测试

```
$ grep -n "_TIMELINE_CACHE:" digitallife/emit/publish.py
110:_TIMELINE_CACHE: dict[tuple[str, int], tuple[list[dict], str]] = {}
$ grep -n "_ZIP_CACHE:" digitallife/produce/vision.py
177:_ZIP_CACHE: dict[str, zipfile.ZipFile] = {}
$ grep -n "^_mem" digitallife/render/linktitle.py
39:_mem: dict[str, dict] | None = None
$ grep -rn "global " --include=*.py digitallife scripts
digitallife/produce/ocr.py:36:        global _engine
scripts/batch_vision.py:223:        global _DEVICE, _READER
digitallife/render/linktitle.py:41:        global _mem
digitallife/render/linktitle.py:75:        global _mem
```

| 位置 | 状态 | 后果 |
|---|---|---|
| `publish._TIMELINE_CACHE` | 无界 dict | 753 天发布期间只增不清；进程内重复发布读到脏缓存 |
| `vision._ZIP_CACHE` | `zipfile.ZipFile` 句柄 | 跨调用泄漏，靠 `close_zip_cache()` 手工收 |
| `linktitle._mem` | 模块级可变单例 | 测试之间不隔离 |
| `ocr._engine` | 模块级单例 | 同上 |
| `batch_vision._DEVICE/_READER` | 脚本级全局 | 被 smoke import（`smoke.py:1262`）后污染测试进程 |

**更严重的是 import 期定死的环境变量**：

```python
# digitallife/produce/vision.py 模块顶层，import 那一刻求值
MAX_TOKENS = int(os.environ.get("DL_START_TOKENS") or 8192)          # L46
IMAGE_MAX_SIDE = int(os.environ.get("DL_IMAGE_MAX_SIDE") or 1280)    # L224
IMAGE_QUALITY = int(os.environ.get("DL_IMAGE_QUALITY") or 72)        # L225
MAX_SEND_IMAGES = int(os.environ.get("DL_MAX_SEND_IMAGES") or 8)     # L235
MAX_ASPECT_RATIO = float(os.environ.get("DL_MAX_ASPECT") or 12.0)    # L244
MIN_SIDE_PX = int(os.environ.get("DL_MIN_SIDE_PX") or 64)            # L245
```

**测试无法在运行中改这些参数**——`import digitallife.vision` 那一刻就固化了。
想测「送图上限是 8」，只能在 import 前设环境变量或 reload 模块。
`smoke.py:22-23` 确实抢先设了 `DL_DB`/`DL_WORK_DIR`——这是**用执行顺序兜住设计缺陷**，
一旦有人调整 import 顺序就全线崩。

全项目 `os.environ` 出现 **26 处**（`vision.py` 独占 8 处）。

---

### P2-10 `scripts/` 与 `digitallife/` 之间没有包边界，靠 `sys.path.insert` 硬接

```
$ grep -rn "sys.path" --include=*.py digitallife scripts
scripts/batch_vision.py      L23   sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
scripts/build_slice_html.py  L29   sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
scripts/build_slice_html.py  L30   sys.path.insert(0, str(Path(__file__).resolve().parent))
scripts/fetch_titles.py      L27   sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
scripts/fetch_titles.py      L28   sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
scripts/finish_phone2.py     L30   sys.path.insert(0, str(ROOT))
scripts/prune_results.py     L18   sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
scripts/rebuild_daily.py     L24   sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
scripts/rewrite_style.py     L38   sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
scripts/smoke.py             L17   sys.path.insert(0, str(ROOT))
scripts/smoke.py             L1155 _sys.path.insert(0, str(ROOT / "scripts"))
```

**11 处**。而 `slice_pages` 被以**裸包名**导入：

```
$ grep -rn "from slice_pages" --include=*.py scripts
scripts/build_slice_html.py  L34   from slice_pages import base, apps, timeline, vision, web
scripts/build_slice_html.py  L35   from slice_pages.contract import (
scripts/fetch_titles.py      L31   from slice_pages import linktitle
digitallife/render/apps.py  L11   from slice_pages import base
digitallife/render/timeline.py L10  from slice_pages import base, linktitle
digitallife/render/vision.py L9   from slice_pages import base
digitallife/render/web.py   L11   from slice_pages import base
```

意味着 `digitallife/render/` 在 Python 眼里是**顶层包**，与 `digitallife` 平级——
两个包之间没有任何声明式依赖，全靠运行时把路径塞进 `sys.path`。

`pyproject.toml` 里 `[tool.uv] package = false`，**没有被打成可安装包**。
后果：IDE 无法静态索引、`python -m pytest` 从仓库根跑不起来、
任何「移到 `src/` 布局」的重构都要重写这 11 处。

---

### P2-11 巨型文件清单

```
$ find . -name "*.py" -not -path "./.venv/*" -not -path "*/__pycache__/*" -not -path "./tmp/*" | xargs wc -l | sort -rn | head -12
 11294 total
  1366 ./scripts/smoke.py
  1196 ./digitallife/daily/
   823 ./digitallife/produce/vision.py
   576 ./digitallife/produce/llm.py
   499 ./digitallife/domain/timeline_merge.py
   495 ./digitallife/emit/publish.py
   425 ./digitallife/domain/dbview.py
   424 ./scripts/build_slice_html.py
   400 ./digitallife/render/base.py
   358 ./digitallife/ingest/sources/iqqz3.py
   337 ./digitallife/domain/slicer.py
```

**10 个文件 >350 行，6 个 >400 行，3 个 >500 行**（`smoke`/`daily`/`vision`）。

---

### P2-12 依赖图全貌（223 条内部边，**无模块级循环**）

```
=== FILES SCANNED: 43 ===
=== AST PARSE FAIL: [] ===
=== INTERNAL DEPENDENCY EDGES (223) ===
=== 跨包下划线私有符号引用 (15) ===
=== 循环依赖 ===
(无模块级环)
```

好消息：**没有 import 环**。这已在及格线以上——同规模 Python 项目到这里多数已经烂了。

fan-in 最高（被依赖越多 = 越底层）：

| 模块 | 被几个模块依赖 |
|---|---|
| `digitallife.model` | 11 |
| `digitallife.clean` | 8 |
| `digitallife.tz` | 7 |
| `digitallife.sources.base` | 6 |
| `dbview`/`slicer`/`vision`/`daily`/`timeline_merge`/`schema_check`/`llm` | 各 4 |

fan-out 最高：`smoke.py`(18) > `cli.py`(11) > `vision.py`(6)。

**但 fan-in 高 ≠ 层序对。** `daily` 被 4 个模块依赖，说明它是「事实上的基础设施」，
可它自己 1196 行（快照，实时值请用 wc -l 复核）、嵌渲染、嵌结构计算——**基础不稳，上面四层全在赌它**。

---

## 2. 目标分层

### 2.1 分层图

```
⑦ 编排层   cli.py  /  scripts/*.py
              │  只往下
              ▼
⑥ 呈现层   digitallife/render/*  ·  build_slice_html.py
              │  只消费 ⑤ 的产出物 + ③ 的【公开】API
              │  ❌ 现状：web.py:10 / timeline.py:16 抓 ③ 的私有符号
              ▼
⑤ 发布层   publish.py
              │  只读 ④ 的产出物（daily_summary / slice_result）
              │  ❌ 现状：publish.py:415/356 反向调用 ④ 的函数
              ▼
④ 加工层   daily.py  ·  vision.py  ·  llm.py  ·  prompts/
              │
              ▼
③ 领域层   model · devices · tz · clean · slicer · dbview · timeline_merge · schema_check
              │
              ▼
② 存储层   schema.py  ·  store.py  ·  paths.py
              │
              ▼
① 采集层   sources/{base,manictime,screenmemo,iqqz3,tai,shion}
```

### 2.2 各层边界与依赖规则

| 层 | 模块 | 允许依赖 | **禁止依赖** | 现状 |
|---|---|---|---|---|
| ① 采集层 | `sources/*` | ②③ | ④⑤⑥⑦ | ✅ 合规 |
| ② 存储层 | `schema`/`store`/`paths` | ③ | ④⑤⑥⑦ | ✅ 合规（`store.py` 函数内 import `devices` 属③，可接受）|
| ③ 领域层 | `model`/`devices`/`tz`/`clean`/`slicer`/`dbview`/`timeline_merge`/`schema_check` | ② | ④⑤⑥⑦ | ⚠️ `model.py:13` import `devices`（③内部互依，需声明层序）|
| ④ 加工层 | `daily`/`vision`/`llm`/`prompts` | ②③ | ⑤⑥⑦ | ✅ 合规 |
| ⑤ 发布层 | `publish.py` | ②③④的**产出物** | **④的函数** | ❌ **`publish.py:415/356`** |
| ⑥ 呈现层 | `digitallife/render/*`+`build_slice_html.py` | ⑤的产出物 + ③的**公开** API | **④的私有符号** | ❌ **`web.py:10`/`timeline.py:16`** |
| ⑦ 编排层 | `cli.py`/`scripts/*.py` | 全部 | — | ✅ 合规 |

### 2.3 违规调用逐个列出（判决的硬证据）

| # | 违规调用 | 方向 | 位置 |
|---|---|---|---|
| 1 | `from .daily import build_daily` | ⑤ ← ④ | `publish.py:415` |
| 2 | `from .daily import normalize_date` | ⑤ ← ④ | `publish.py:425` |
| 3 | `from digitallife.timeline_merge import _clean_title` | ⑥ ← ③(私有) | `slice_pages/web.py:10` |
| 4 | `from digitallife.timeline_merge import _clean_title` | ⑥ ← ③(私有) | `slice_pages/timeline.py:16` |
| 5 | `from digitallife.vision import _collect_descriptions, _slice_events` | ⑦ ← ④(私有) | `finish_phone2.py:36` |
| 6 | `device_structure` 与 `dbview.build_day_structure` 并存 | ④ 内重复 ③ | `daily.py:259` vs `dbview.py:192` |
| 7 | `_fmt_dur` 两份且**输出格式不同** | ④ / ⑥ 双份 | `daily.py:72` / `base.py:382` |
| 8 | `_parse_json` 两份**逐字相同** | ④ / ⑤ 双份 | `daily.py:89` / `publish.py:209` |
| 9 | `_merge_browser_web`/`_span_seconds` 别名把 ③ 的实现挂回 ④ | ④ 暴露 ③ | `daily.py:486,497` |
| 10 | `_domain_of` 两份 | ③ / ⑥ 双份 | `dbview.py:45` / `web.py:22` |

---

## 3. 拆分方案（具体目标文件清单）

### 3.1 `daily.py`（当前 1196 行（快照，实时值请用 wc -l 复核））→ 5 个文件

| 目标文件 | 行数预估 | 职责一句话 |
|---|---|---|
| `digitallife/daily/__init__.py` | ~40 | 只导出 `build_daily`，保持 `from digitallife.daily import build_daily` 兼容 |
| `digitallife/daily/structure.py` | ~180 | 当日结构：`device_structure`/`_focus_blocks`/`_structure_fallback`——**与 `dbview` 二选一，删掉重复那份** |
| `digitallife/daily/render.py` | ~420 | `render_narrative`/`_render_device`/`_render_device_body`/`_device_label`/`_device_heading`/`_tl_line`/`_md_link`/`_apps_sentence` 等全部渲染 |
| `digitallife/daily/prompt.py` | ~100 | `DAILY_PROMPT` + `_llm_narrative` |
| `digitallife/daily/build.py` | ~170 | `build_daily`（编排：取数 → 渲染 → 落库） |

**同时删掉**：`daily.py:28` 或 `:30` 的重复常量（保留一条）；
`_merge_browser_web`/`_span_seconds` 别名（同时改 smoke 的 import）。

### 3.2 `vision.py`（823 行（快照，实时值请用 wc -l 复核））→ 4 个文件

| 目标文件 | 职责 |
|---|---|
| `digitallife/vision/__init__.py` | 导出 `enhance_slice` |
| `digitallife/vision/images.py` | `_is_degenerate`/`_sample_evenly`/`_downscale`/`_read_image`/`_sniff_mime`/`_collect_images`/`_collect_descriptions`（**纯字节处理，最该先单测**）|
| `digitallife/vision/session.py` | `_chat`/`_strip_fence`/`_parse_structured`/`_conflicts`/`_summary_check`/`_write_result` |
| `digitallife/vision/enhance.py` | `enhance_slice` 主编排 + 空内容重试 + `REWORK_ROUNDS` |

**同时删掉**：`vision.py:459-463` 重复的 `_default_client`（保留带 docstring 的 L440 那份）。

### 3.3 `timeline_merge.py`（499 行）→ 3 个文件

| 目标文件 | 职责 |
|---|---|
| `digitallife/timeline/__init__.py` | 导出 `merge_browser_web`/`display_title`/`span_seconds`/`readable_from_url`/`clean_title` |
| `digitallife/timeline/identity.py` | `is_web`/`is_path`/`page_key`/`_strip_www`/`_short_scheme_ref` + **`clean_title`/`readable_from_url` 升格为公开**（去掉 `_`）|
| `digitallife/timeline/merge.py` | `merge_browser_web`/`_drop_page_echoes`/`_fold_span_twins`/`display_title` |

**关键**：`_clean_title` 必须**升格为公开 API**（去掉 `_`），否则 `web.py`/`timeline.py` 的私有依赖无法合法化。

### 3.4 `base.py`（400 行）→ 3 个文件

| 目标文件 | 职责 |
|---|---|
| `slice_pages/tokens.py` | `CSS`(L21-163) + `JS`(L164-263)——**纯静态资源，242 行** |
| `slice_pages/html.py` | `esc`/`link`/`seg`/`more_button`/`card`/`fmt_dur`/`span` |
| `slice_pages/page.py` | `page`/`body_dock`/`write`（页面外壳） |

### 3.5 `publish.py`（495 行（快照，实时值请用 wc -l 复核））→ 2 个文件

| 目标文件 | 职责 |
|---|---|
| `digitallife/publish/render.py` | `_render_slice_md`/`_norm_timeline`/`_clip_timeline`/`_slice_timeline` |
| `digitallife/publish/__init__.py` | `publish_all` 编排 + ndjson 导出 + manifest |

**同时**：`_daily_text` 改为**读库**（`daily_summary.output_text`），切断 `publish → daily` 反向依赖。

### 3.6 新建两个共享工具模块（消除重复实现的落点）

| 新文件 | 收编的重复实现 |
|---|---|
| `digitallife/textutil.py` | `_parse_json`（daily+publish 两份）、`_cell`（daily+publish 两份）、`_hms`/`_hm`（daily+publish+vision 三份）|
| `digitallife/urlutil.py` | `_domain_of`（dbview+web 两份）、`readable_from_url`、`clean_title`、`span_seconds` |

### 3.7 `scripts/` 侧的边界修复

| 动作 | 解决 |
|---|---|
| `pyproject.toml` 去掉 `package = false`，正常打包；`slice_pages` 改为 `digitallife.pages` | 11 处 `sys.path.insert` 全部可删 |
| `slice_pages` 移入 `digitallife/pages/` | 呈现层与领域层同包内，私有符号问题暴露得更早 |
| `smoke.py` 拆成 `tests/` 下的 pytest 模块 | 见 [`testing.md`](./testing.md) |

---

## 4. 「高内聚低耦合」判决（分段打分，含证据）

> 口径：**内聚**看「一个模块里的东西是不是一件事」，**耦合**看「改一处要动几处」。
> 判定三档：**做到了 / 部分做到 / 没做到**。

### 4.1 采集层（`sources/*`，6 文件）—— ✅ **做到了**

**内聚证据**：5 个适配器结构高度一致，都继承 `sources/base.SourceAdapter`，
都实现 `Fingerprint` + `sha256_file` 幂等，都只产出 `Event`。

**耦合证据**（依赖扫描输出，sources 部分）：

```
digitallife.sources.manictime  -> clean / model / paths / sources.base / tz     ← 全部向下
digitallife.sources.screenmemo -> clean / model / paths / sources.base
digitallife.sources.tai        -> clean / model / paths / sources.base / tz
```

`fan-out = 5`，全部指向 ②③ 层，**零反向**。新增源 = 加一个文件 + 注册一行，不动既有文件。

**扣分项（不改变结论）**：`manictime.py` 与 `screenmemo.py` 各有一份 `_extract_member`
（签名相同、实现相近），应抽到 `sources/base.py`。

---

### 4.2 存储层（`schema.py` 187 + `store.py` 136 + `paths.py` 58）—— ✅ **做到了**

**证据**：`store.events_between` 把 DL-5 的区间查询规则**单点持有**，
`device` 过滤走 `devices.query_aliases`（DL-7 的修法）：

```python
# digitallife/ingest/store.py:95-101
    if device:
        # 库内同一台设备可能以旧值（pc/phone）或注册 id（pc-main/phone-1）存在，
        from . import devices as _dv
        aliases = _dv.query_aliases(device)
        sql += " AND device IN (" + ",".join(["?"] * len(aliases)) + ")"
        args.extend(aliases)
```

**这是全项目最干净的一处**：DL-7（设备别名导致静默返回空）的修法落在唯一正确的层——
存储层，而不是散在 9 个调用点。

**唯一瑕疵**：`publish._dev_clause` 又把同样的别名逻辑实现了一遍，应直接调 `store`。

---

### 4.3 领域层（`model` 97 / `devices` 136 / `tz` 66 / `clean` 156 / `slicer` 337 / `dbview` 425 / `timeline_merge` 499 / `schema_check` 201）—— ⚠️ **部分做到**

**做对的**：
- `devices.py` 是**真正的唯一真源**：`DEVICES` 注册表 + `LEGACY_ALIASES` + `sql_device_filter` 三位一体，DL-7 的修法中心化在这里。
- `timeline_merge.py` 的四层归并内聚性很好，499 行装的是**一件事的四个判据**，不算臃肿。

**没做到的**：
1. `dbview._domain_of:45` 与 `web._domain_of:22` 是同一函数的两份实现，且 `web` 那份把正则内联重复（函数内 `import re`）。
2. `timeline_merge._readable_from_url` 是域名处理的**第三套**写法（用 `urlsplit` 而非 `split`）。
3. `daily.device_structure:259` 与 `dbview.build_day_structure:192` 概念重叠。
4. `slicer._span` 返回 `tuple[int,int]`，`timeline_merge.span_seconds` 返回 `float`——**同名不同义**。

---

### 4.4 加工层（`daily` 1196 / `vision` 823 / `llm` 576 / `prompts` 151）—— ❌ **没做到**

**内聚崩了的证据**：
- `daily.py` 同时住着：区间运算、当日结构、切片查询、markdown 渲染、模型 prompt、主编排 —— **9 种职责**（见 P0-3 表），62 个顶层符号。
- `daily.py` 里 **5 处函数内 `import devices`**（L308/336/618/795/1039），说明作者每次需要它都得临时抓——**依赖关系不写在文件头上，等于没有依赖关系**。
- `render_narrative` 内部再定义渲染子函数，与 `contract` 各写一套设备命名（见 P0-4）。

**耦合失控的证据**：
- `vision.py` 有同名 `_default_client` 两份（L440/L459）；
- `vision.py` 的 6 个模块级环境变量常量**在 import 期定死**，测试无法在运行中改；
- `daily.py:28/30` 重复常量（值相同所以永不报错）。

---

### 4.5 发布层（`publish.py` 495 行（快照，实时值请用 wc -l 复核））—— ⚠️ **部分做到**

**做对的**：
- **「现算不读库」是正确决策**（2026-10-24 用户裁决），避免「改了渲染代码却搬旧快照」的隐患。
  实测印证：库里 `output_text` 753 天全空，`structured_json` 只留 `_narrative_only:true` + `narrative`。
- `_render_slice_md` / `_norm_timeline` / `_clip_timeline` 职责清晰。

**没做到的**：
1. **依赖 `daily` 黑盒而非可单测的渲染函数**（P0-2）：`build_daily` 里取数+结构+渲染+落库焊死，
   导致 `publish` 只能整块调用，且无法单测。
2. **重复实现**：`publish._parse_json:140` 与 `daily._parse_json:89` 逐字相同；
   `publish._cell` 与 `daily._cell` 逐字相同；`publish._hm/_hms/_hmm` 与 `daily._hms/_hm` 重复。
3. **全局缓存无生命周期**：`publish.py:179` `_TIMELINE_CACHE` 只增不清。
4. **`normalize_date` 这类纯工具被迫从 `daily` 借**（`publish.py:425`）——放错层。

> **判决依据**：发布层的**决策**是对的，**实现形态**是错的。所以是「部分做到」而非「没做到」。

---

### 4.6 呈现层（`slice_pages/*` 8 文件 + `build_slice_html.py` 424 行）—— ⚠️ **部分做到**

**做对的**：section 模块契约（`ANCHOR`/`LABEL`/`panel()`）是**真正的好设计**——
新增 section 只需加文件 + 在编排器注册，不动既有 section。8 个文件里 7 个在 200 行以下，
内聚度是各层里最高的。

**没做到的**：
- `web.py:10` / `timeline.py:16` 抓领域层私有符号（P0-1）；
- `base.py` 400 行里 242 行是 CSS+JS 静态字符串，却和 `fmt_dur`/`page` 这类逻辑函数混在一个模块；
- `base.fmt_dur` 与 `daily._fmt_dur` **输出格式不同**，同一时长在日报和时段页上不一样。

---

### 4.7 编排层（`cli.py` 246 行 + `scripts/*.py` 8 个）—— ⚠️ **部分做到**

**做对的**：`cli.py` fan-out 11，全部指向下层，**零反向依赖**，标准编排层写法。

**没做到的**：`scripts/` 下 8 个脚本**没有分层意识**——`batch_vision`(286)、`build_slice_html`(424)、
`rewrite_style`(199) 各自 `sys.path.insert`，与 `digitallife` 之间只有路径约定没有包边界（P2-10）。
`scripts/rewrite_style.py` 按 AGENTS.md §G.2 是**保留作反面教材**的废弃脚本，却仍能被 import、仍能跑。

---

### 4.8 总判决

| 层 | 判决 | 一句话依据 |
|---|---|---|
| 采集层 | ✅ **做到了** | 5 适配器同构，fan-out=5 全向下，加源不动既有文件 |
| 存储层 | ✅ **做到了** | DL-5/DL-7 的修法单点落在 `store.events_between` |
| 领域层 | ⚠️ **部分做到** | devices/timeline_merge 内聚好；工具函数三套并存 |
| 加工层 | ❌ **没做到** | daily.py 1196 行（快照，实时值请用 wc -l 复核） 9 种职责 + 5 处局部 import + 重复常量 |
| 发布层 | ⚠️ **部分做到** | 现算设计正确（用户裁决），但依赖 `daily` 黑盒而非可单测的渲染函数 |
| 呈现层 | ⚠️ **部分做到** | section 契约好；`web.py:10`/`timeline.py:16` 抓私有符号 |
| 编排层 | ⚠️ **部分做到** | `cli.py` 干净；`scripts/` 无包边界 |

**总体：2 层做到，4 层部分做到，1 层没做到。**

**结论一句话**：这个项目的**架构意图是对的**（有明显层意识、有契约设计、有真源概念），
**但纪律没落到物理边界上**——该私有的没私有（15 处），该单向的反向了（2 处），
该共享的各写各的（5 组重复实现）。

**它不是「没有架构」，是「架构写在文档里，没写进 import 语句」。**

---

## 5. 修复优先级（按投入产出比）

| 序 | 动作 | 工作量 | 收益 |
|---|---|---|---|
| 1 | 把 `daily` 的渲染拆成纯函数（`daily_render.py`），供 `daily` 与 `publish` 共用 | 4h | **不要改读库**（会退回旧隐患）；拆出后 publish 可单测，反向依赖自然消失 |
| 2 | `_clean_title` 升格为公开 API（`clean_title`） | 30min | 消除最危险的私有跨包依赖（2 处）|
| 3 | 删 `vision.py:459` 重复函数、`daily.py:28` 重复常量 | 10min | 消除两处「假绿温床」 |
| 4 | 抽 `textutil.py`（`_parse_json`/`_cell`/`_hms`）| 3h | 一次消除 6 处重复 |
| 5 | `pyproject` 正常打包，删 11 处 `sys.path` | 4h | 让 pytest/IDE 能工作，解锁后续一切测试 |
| 6 | 拆 `daily.py` → 5 文件 | 2d | **前置条件：先做第 5 项 + `testing.md` 的 pytest 化**，否则无回归网 |
| 7 | 拆 `vision.py`/`timeline_merge.py`/`base.py` | 3d | 同 6 |

> ⚠️ **第 6/7 项必须先有测试网**。当前零 pytest、smoke 187 条断言里正断言 181 条，
> 在这种状态下拆 1196 行（快照，实时值请用 wc -l 复核）的文件等于蒙眼手术。顺序不能倒。

---

## 附录 A：可重放的采集命令

### A.1 依赖图（223 条边 + 15 处私有引用 + 循环检测）

```bash
cd 01-Projects/digital-life
export PYTHONUTF8=1
python tmp/a3_deps.py
```

脚本做法：`ast.parse` 每个 `.py`，`ast.walk` 抓 `Import`/`ImportFrom`，
相对 import 按 `node.level` 还原成绝对包名，然后：
1. 过滤出 `digitallife.*`/`scripts.*` 的内部边；
2. 对 `ImportFrom` 的每个 `name`，若以 `_` 开头且来源包 ≠ 自身包 → 记为「跨包私有引用」；
3. DFS 找模块级环。

实测输出前几行：

```
=== FILES SCANNED: 43 ===
=== AST PARSE FAIL: [] ===
=== INTERNAL DEPENDENCY EDGES (223) ===
=== 跨包下划线私有符号引用 (15) ===
=== 循环依赖 ===
(无模块级环)
```

### A.2 重复实现扫描

```bash
python tmp/a3_dup.py
```

`PATTERNS` 覆盖：域名/URL 判定、设备 label 兜底、span 格式化、JSON 容错解析、
模板渲染、全局可变缓存、模块级 environ、`global` 语句。

### A.3 fan-in / fan-out

```bash
python tmp/a3_fan.py
```

每条 import 归到「最近的真实模块」（逐步截断包名直到命中已知模块）。

### A.4 全量行数

```bash
find . -name "*.py" -not -path "./.venv/*" -not -path "*/__pycache__/*" -not -path "./tmp/*" | xargs wc -l | sort -rn
```

---

## 附录 B：本轮抽查过的锚点（可复验）

```bash
cd 01-Projects/digital-life
grep -n "^_TIMELINE_MIN_S = 120.0" digitallife/daily/          # 应命中 2 行（28、30）
grep -n "def _default_client" digitallife/produce/vision.py               # 应命中 2 行（440、459）
grep -n "from .daily import" digitallife/emit/publish.py               # 应命中 2 行（346、356）
grep -n "_TIMELINE_CACHE:" digitallife/emit/publish.py                 # 应命中 1 行（110）
grep -n "from digitallife.timeline_merge import" digitallife/render/web.py   # 应命中 1 行（10）
```

实测输出（2026-10-25 采集）：

```
28:_TIMELINE_MIN_S = 120.0  # 日报只留单段 >=2 分钟的事件；碎片折叠，完整版进时段网页
30:_TIMELINE_MIN_S = 120.0  # 日报只留单段 >=2 分钟的事件；碎片折叠，完整版进时段网页
440:def _default_client():
459:def _default_client():
346:    from .daily import build_daily
356:        from .daily import normalize_date
110:_TIMELINE_CACHE: dict[tuple[str, int], tuple[list[dict], str]] = {}
10:from digitallife.timeline_merge import _clean_title, span_seconds
```

---

## 附录 C：与数据侧 / 工程侧文档的关系

- **工程规则**（DL-N 铁律、验证清单）：`01-Projects/digital-life/AGENTS.md`（本文不重复）
- **数据契约**（日报结构、设备命名口径）：`04-Areas/数字人生/AGENTS.md`
- **接口签名**：`docs/spec/interfaces.md`
- **本文定位**：只回答「层怎么分、边界在哪违规、怎么拆、内聚耦合判什么」
- **姊妹文档**：[`testing.md`](./testing.md)｜[`troubleshooting.md`](./troubleshooting.md)｜[`tmp-cleanup.md`](./tmp-cleanup.md)
