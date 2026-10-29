# digital-life 故障定位手册

> **产出**：子代理3｜**采集日期**：2026-10-25
> **采集环境**：`01-Projects/digital-life`，MSYS2 bash，`PYTHONUTF8=1`
> **适用范围**：本手册面向「现象已在线上暴露，需要定位根因」的场景。
> 防错规则（DL-N 铁律）见工程 `AGENTS.md` §C；本手册只讲**怎么查**。

---

## 0. 核心原则（先记住这三条，比下面的表更有用）

1. **先查库，再看文件，最后看代码**（S36）。产出不对时，90% 的根因在数据，不在渲染。
2. **每一层都有「自报成功」的假象**（DL-10）。跨层链路出问题时，必须找**端到端哨兵**，
   不能信任何单层的日志。
3. **命令里不传中文**（S34）。中文断言一律在 Python 进程内做，不走命令行参数。

---

## 1. 症状 → 排查路径 → 定位命令

### 1.1 日报某天没有设备节 / 结构异常

| 症状 | 排查路径 | 定位命令 |
|---|---|---|
| 日报里整个设备节消失 | ① 该设备当日有没有事件 → ② 有没有片 → ③ 设备别名是否对不上 | 见下 |

```bash
cd 01-Projects/digital-life && export PYTHONUTF8=1

# ① 当日该设备的事件数（关键：必须用别名集合，不能用 device = ?，见 DL-7）
python - <<'PY'
import sqlite3
from digitallife import devices as dv
DB = r"../../.data/digital-life/digital-life.sqlite"
con = sqlite3.connect(DB); con.row_factory = sqlite3.Row
day = "2026-08-13"
for d in dv.all_devices():
    al = dv.query_aliases(d.device_id)
    q = ("SELECT COUNT(*) n FROM event WHERE date(start_utc_ms/1000,'unixepoch','+8 hours')=? "
         "AND device IN (%s)" % ",".join("?"*len(al)))
    n = con.execute(q, [day]+al).fetchone()["n"]
    print(f"{d.device_id:10s} name={d.name:14s} 别名={al} 事件={n}")
PY
```

**判读**：
- 事件 = 0 → 数据没入库（查 `source_file` 表看该源最近一次导入时间）
- 事件 > 0 但片 = 0 → `slicer` 没跑，或跑了但 `device` 精确匹配失败（**DL-7 复发**）
- 事件和片都有 → 渲染问题，查 `daily._render_device` 的入参

```bash
# ② 该日该设备的片数
python -c "
import sqlite3
con=sqlite3.connect('../../.data/digital-life/digital-life.sqlite')
for r in con.execute('''SELECT device, COUNT(*) FROM slice
    WHERE date(start_utc_ms/1000,'unixepoch','+8 hours')='2026-08-13' GROUP BY device'''):
    print(r)
"
```

---

### 1.2 时段页某设备整段消失

**这是「设计如此」的概率远大于「渲染失败」**——先排除设计，再查代码。

| 判据 | 结论 |
|---|---|
| 该设备当日**该 section 0 行** | ✅ 正常：section 里设备 0 行时不生成 tab（AGENTS.md §D.6）|
| 两个设备都 0 行 | ✅ 正常：整页只剩「这一天没有任何设备使用记录」|
| 该设备有数据但页面没有 | ❌ 真 bug，重点查 `panel id` 撞号 |

```bash
# 查该日页面实际生成了几个 panel
python - <<'PY'
import re, pathlib
p = pathlib.Path("../../04-Areas/数字人生/10-时段/2026-08-13.html")
html = p.read_text(encoding="utf-8")
print("文件大小: %.1f KB" % (len(html.encode())/1024))
print("section 数:", len(re.findall(r"<section", html)))
print("tablist:", len(re.findall(r'role="tablist"', html)))
print("panel id:", sorted(set(re.findall(r'id="(panel[^"]*)"', html))))
# 关键：两个设备的 panel id 必须不同（不得用 dev.split("-")[0]）
PY
```

**已知真实事故**：`dev.split("-")[0]` 会把 `phone-1`/`phone-2` 都截成 `phone`，
两个 panel 撞号 → 页面上直接少一台设备。哨兵是冒烟 T20k。

---

### 1.3 视觉总结是空壳 / 模型 413 / 429

**这是本项目事故最密集的区域**（DL-10/11/12/13 全在这里）。按错误码分流：

| 上游返回 | 语义 | 处置 | 历史教训 |
|---|---|---|---|
| `429` + `message` 含 `rpm` | **每分钟限流** | 退避**跨过 60s 滚动窗**（20s+40s），不要停批 | 用 30s 总退避重试**全部再撞**（DL-11）|
| `429` + `message` 无 rpm | **额度耗尽** | 停下等刷新 | **按 `type` 分类必错**——`type` 恒为 `quota_exceeded_error`（DL-13）|
| `413` | **畸形截图**，与体积无关 | 过滤宽高比 >12 / 短边 <64 | base64 仅 0.16–0.53MB 也 413；真因是 1280×20 退化图，**一张毒掉整片**（DL-11）|
| `400 sensitive image` | 内容判定，重发必错 | 减少送图数（≤8）| ≥16 张必触发（DL-11）|

```bash
# 查某片为什么是空壳：看 _meta 里的取证字段
python - <<'PY'
import sqlite3, json
con = sqlite3.connect("../../.data/digital-life/digital-life.sqlite")
con.row_factory = sqlite3.Row
q = """SELECT slice_id, model, LENGTH(overall_summary) L, structured_json
       FROM slice_result WHERE LENGTH(COALESCE(overall_summary,'')) < 50 LIMIT 5"""
for r in con.execute(q):
    meta = {}
    try:
        meta = (json.loads(r["structured_json"] or "{}") or {}).get("_meta") or {}
    except Exception: pass
    print(r["slice_id"], "| 字数", r["L"], "| images_sent =", meta.get("images_sent"),
          "| schema_ok =", meta.get("schema_ok"))
PY
```

**判读黄金判据**：`_meta.images_sent == 0` 但 `collect_descriptions` 说「收集到 N 张」
→ **DL-10 盲总结**（两层日志各自正常，合起来一张图没送）。

```bash
# 统计有多少条是盲总结
python -c "
import sqlite3, json
con=sqlite3.connect('../../.data/digital-life/digital-life.sqlite')
n=blind=0
for (sj,) in con.execute('SELECT structured_json FROM slice_result'):
    n+=1
    try: m=(json.loads(sj or '{}') or {}).get('_meta') or {}
    except Exception: continue
    if m.get('images_sent')==0: blind+=1
print(f'总 {n} 片，其中 images_sent=0 的盲总结 {blind} 片')
"
```

---

### 1.4 建页变慢（DL-9 性能陷阱）

**判据不是「感觉慢」，是「SQL 条数是否随片数线性增长」**。

```bash
# 用 trace 回调统计 SQL 条数（比计时可靠，不受机器影响）
python - <<'PY'
import sqlite3, time
con = sqlite3.connect("../../.data/digital-life/digital-life.sqlite")
calls = []
con.set_trace_callback(calls.append)
t0 = time.time()
# 替换成真实调用（例：单日建页）
import sys; sys.path.insert(0, "../..")
from digitallife.dbview import build_day_structure
st = build_day_structure(con, "2026-08-13")
print("耗时 %.2fs, SQL 条数 %d" % (time.time()-t0, len(calls)))
print("前 5 条 SQL：")
for c in calls[:5]: print("  ", c[:100])
PY
```

**历史基线**（`docs/postmortem/perf-regression-2026-10-23.md`）：

| 指标 | 正常 | 退化时 |
|---|---|---|
| `load_slices`（单日 phone-2）| 1.6s | **102.9s** |
| 753 页全量重建 | ~40 分钟 | **5 小时+** |

**根因模式**：给「每片/每行一个派生值」时在**循环里查了 SQL**（DL-9）。
`EXPLAIN` 会显示走了错误的索引（如 `ix_event_kind` 而非时间索引，`kind='screen'` 命中 10 万行）。

---

### 1.5 库里 count 与 vault 文件数对不上

**先确认「应该对得上吗」**——本项目有两个数是**天然不等**的：

| 对比 | 该不该相等 | 真相 |
|---|---|---|
| `daily_summary` 行数 vs `20-日报/*.md` 数 | ✅ 应相等 | 都是 753 |
| `slice` 行数 vs `片层/*.md` 数 | ❌ **不等** | 片层 md 只有出了视觉总结的片才写（`DL_PUBLISH_ALL_SLICES=1` 可全量）|
| 有日报的天数 vs `10-时段/*.html` 数 | ✅ 应相等 | 一天一页 |

```bash
python - <<'PY'
import sqlite3, pathlib
DB = "../../.data/digital-life/digital-life.sqlite"
V = pathlib.Path("../../04-Areas/数字人生")
con = sqlite3.connect(DB)
def c(q): return con.execute(q).fetchone()[0]
print("daily_summary 行数      :", c("SELECT COUNT(*) FROM daily_summary"))
print("20-日报 md 数           :", len(list((V/"20-日报").glob("*.md"))))
print("10-时段 html 数         :", len(list((V/"10-时段").glob("*.html"))))
print("slice 行数              :", c("SELECT COUNT(*) FROM slice"))
print("片层 md 数              :", len(list((V/"10-时段"/"片层").rglob("*.md"))))
print("slice_result 行数       :", c("SELECT COUNT(*) FROM slice_result"))
PY
```

**实测（2026-10-25）**：

| 项 | 数量 |
|---|---|
| `daily_summary` | 753 |
| `20-日报/*.md` | 753 ✅ 相等 |
| `10-时段/*.html` | 753 ✅ 相等 |
| `event` | 1,124,831 |
| `slice` | 43,257 |
| `slice_result` | 9,679 |
| `source_file` | 513 |
| `link_title` | **0** ← 见 §3.3 |

---

### 1.6 `database is locked` / WAL 残留

```bash
$ ls -la ../../.data/digital-life/
-rw-r--r-- 1 D-001 197121 912461824 Oct 25 09:17 digital-life.sqlite
-rw-r--r-- 1 D-001 197121     32768 Oct 25 12:16 digital-life.sqlite-shm
-rw-r--r-- 1 D-001 197121         0 Oct 25 12:16 digital-life.sqlite-wal
```

| 现象 | 原因 | 处置 |
|---|---|---|
| `-wal` 文件**很大**（GB 级）| 有长事务未提交，检查点无法推进 | `PRAGMA wal_checkpoint(TRUNCATE)`；先确认没有残留进程 |
| `-wal` 文件 0 字节 | ✅ 正常：无未检查点数据 | 无需处理 |
| `database is locked` | 有别的进程持有写锁 | 查 PID（见下），**不要**删文件 |

```bash
# 查谁占着库（Windows）
powershell -NoProfile -Command "Get-Process python* -ErrorAction SilentlyContinue | Select-Object Id,StartTime,Path"
```

> ⚠️ **DL-1 铁律**：要备份库**禁止 `cp`**。库开 WAL，`cp` 只拷主文件，
> WAL 里未检查点的数据没合并，目标库 `integrity_check` 直接报 `database disk image is malformed`。
> 必须用 `sqlite3.Connection.backup()`。**实测毁过一次 617MB 库。**

---

## 2. 可观测性缺口与具体建议

### 2.1 现状盘点（实测）

```bash
$ grep -rn "logging\|logger" --include=*.py digitallife scripts | wc -l
（几乎只有 print）
$ grep -rn "^import logging\|from logging" --include=*.py digitallife scripts | wc -l
（0）
```

**当前状态**：

| 能力 | 有无 | 说明 |
|---|---|---|
| 结构化日志 | ❌ | 全是 `print()`，无法机读 |
| 日志级别 | ❌ | 无法区分 info/warn/error |
| trace id / 请求 id | ❌ | 跨层链路无法串联 |
| 耗时埋点 | ❌ | 靠人肉 `time.time()` |
| 错误分类 | ⚠️ 部分 | `batch_vision._classify` 有，其他模块没有 |
| 落盘日志 | ⚠️ 部分 | `tmp/*.log` 是手工 `tee` 出来的，无轮转 |

### 2.2 具体建议（按投入产出比排序）

#### 建议 1：引入 `logging`，5 行替换所有 `print`（成本 2h，收益最大）

```python
# digitallife/log.py（新建，约 40 行）
import logging, os, sys
def get(name: str) -> logging.Logger:
    lg = logging.getLogger(name)
    if not lg.handlers:
        h = logging.StreamHandler(sys.stderr)
        # 关键：结构化 —— 固定 key=value，日志可直接被 grep/awk 处理
        h.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)-5s [%(name)s] %(message)s"))
        lg.addHandler(h)
        lg.setLevel(os.environ.get("DL_LOG_LEVEL", "INFO"))
    return lg
```

**替换优先级**：`vision.py`（823 行（快照，实时值请用 wc -l 复核），事故最多）> `batch_vision.py` > `publish.py`。

#### 建议 2：给跨层链路加 `slice_id` 贯穿（成本 4h，解决 DL-10 类问题）

DL-10 的本质是「两层日志各自正常，合起来是错的」。修法不是加更多日志，是**让日志能被串起来**：

```python
# vision.enhance_slice 里，每条日志都带 slice_id + 阶段
log.info("stage=collect slice=%s images=%d missing=%d", slice_id, len(imgs), missing)
log.info("stage=send    slice=%s sent=%d model=%s", slice_id, sent, model)
log.info("stage=result  slice=%s chars=%d schema_ok=%s", slice_id, len(out), ok)
```

然后一条命令就能发现断链：

```bash
grep "slice=S123" run.log
# 若 collect 说 images=23 而 send 说 sent=0 → 断链，立刻可见
```

#### 建议 3：把 `_meta` 的取证字段标准化（成本 3h，代价几乎为零）

现在 `slice_result.structured_json._meta` 里已有 `images_sent`/`schema_ok`/`conflict_count`，
但字段名不统一、部分历史行是 `null`。建议固定一组：

```
_meta = {
  "images_collected": int,   # 收集到几张
  "images_sent":      int,   # 真送出去几张  ← DL-10 哨兵
  "images_degenerate":int,   # 被过滤的退化图 ← DL-11 哨兵
  "max_tokens_used":  int,   # 实际放大到多少 ← DL-12 哨兵
  "retries":          int,   # 退避重试次数   ← DL-13 哨兵
  "schema_rounds":    int,   # 打回重做轮次
  "elapsed_ms":       int,   # 端到端耗时
}
```

**每条都能反推一次历史事故**：`images_sent=0` → DL-10；`images_degenerate>0` → DL-11；
`max_tokens_used=65536` 且 `elapsed_ms>200000` → DL-12。

#### 建议 4：耗时埋点做成装饰器（成本 1h）

```python
import time, functools
def timed(fn):
    @functools.wraps(fn)
    def w(*a, **k):
        t0 = time.perf_counter()
        try:
            return fn(*a, **k)
        finally:
            dt = (time.perf_counter() - t0) * 1000
            get(fn.__module__).info("timed fn=%s ms=%.0f", fn.__name__, dt)
    return w
```

**优先埋**：`build_daily`、`publish_all`、`load_slices`、`enhance_slice`
—— 这四个覆盖了「建日报/建页/视觉」三条最慢的路径。

#### 不建的东西（避免过度工程）

- ❌ 不接 OpenTelemetry / Jaeger：单机脚本，成本远超收益
- ❌ 不建日志聚合服务：`tmp/*.log` + grep 足够
- ❌ 不做指标大盘：753 天数据，跑一次就知道

---

## 3. 完整现场排障演示（真实执行，全程只读）

> 本节记录一次真实的「观察 → 假设 → 验证 → 结论」，不是编的示范。

### 3.1 观察：753 篇日报的 mtime 全是今天上午

```bash
$ cd 01-Projects/digital-life
$ ls -la --time-style=+%Y-%m-%dT%H:%M ../../04-Areas/数字人生/20-日报/*.md | awk '{print $6}' | sort | uniq -c
      4 2026-10-25T12:02
    104 2026-10-25T12:03
     90 2026-10-25T12:04
     79 2026-10-25T12:05
     93 2026-10-25T12:06
     90 2026-10-25T12:07
     73 2026-10-25T12:08
     77 2026-10-25T12:09
     56 2026-10-25T12:10
     56 2026-10-25T12:11
     31 2026-10-25T12:12
```

**观察**：753 个文件在 12:02–12:12 的 **10 分钟内**全部被重写，均匀分布（每分钟 30–104 个）。

### 3.2 假设生成（三个候选，必须逐个排除）

| # | 假设 | 如何证伪 |
|---|---|---|
| H1 | 有进程在**持续**重写（每来一条数据重跑一次）| 看是不是一次性批处理 → 10 分钟均匀 = 一次性 |
| H2 | 某次 `publish --all` 全量重建 | 看库里的 `created_at` |
| H3 | 文件内容其实没变，只是被 `touch` 了 | 比对内容 |

**H1 先排除**：733 个文件 10 分钟均匀完成，速率稳定 → 是单次批处理，不是持续写入。

### 3.3 验证 H2：查库里的生成时间

```bash
$ python tmp/a3_diag_demo.py
=== daily_summary：753 行 ===
=== created_at 时间戳分布（日报是何时生成的）===
  2026-10-25  753
```

**库里 753 行的 `created_at` 全部是 2026-10-25**。H2 得到支持。

### 3.4 意外发现：库里 `output_text` 全空

进一步查内容时发现一个更大的事实：

```bash
$ python tmp/a3_diag_demo2.py
[验证 1] 库里 output_text 非空的行数
  非空行数: 0 / 753

[验证 2] md 文件是否真有内容
  md 文件数: 753
  最小 1224 B / 最大 27344 B / 合计 7.1 MB

[验证 3] 抽 3 天对比
  2026-08-13: 库 output_text=0 字节, structured_json=9520 字节, md 文件=27344 字节
  2024-09-28: 库 output_text=0 字节, structured_json=80 字节, md 文件=1313 字节
  2026-10-21: 库 output_text=0 字节, structured_json=80 字节, md 文件=5246 字节
```

**这是关键矛盾**：库里的正文是空的，但 md 文件有 7.1 MB 内容。

### 3.5 定位根因：读代码确认「现算不读库」

```bash
$ sed -n '392,410p' digitallife/emit/publish.py
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

```bash
$ sed -n '1161,1168p' digitallife/daily/
    # 只存"模型叙事原文"，不存渲染结果（2026-10-24 用户裁决：
    # 「日报不需要存储到数据库…本身就是基于数据库内容进行的导出」）。
    #   - narrative 是唯一不可重算的东西（要么调用模型，要么丢）
    #   - output_text / structured / breakdown 全部可由 slice+event 现算
    # persist=False（publish 走的路径）连 narrative 都不写，只把结果返回。
```

### 3.6 验证：库里到底留了什么

```bash
$ python -c "
import sqlite3, json
con = sqlite3.connect('file:../../.data/digital-life/digital-life.sqlite?mode=ro', uri=True)
r = con.execute(\"SELECT structured_json, model FROM daily_summary WHERE date_key='2024-09-28'\").fetchone()
print(r[0][:200]); print('model =', r[1])
"
{"_narrative_only": true, "narrative": "", "layer": "day", "date": "2024-09-28"}
model = None
```

### 3.7 结论（完整因果链）

1. **753 篇 md 的 mtime 全在今天上午** = 某次 `publish --all` 全量重建（H2 成立）。
   这是**正常运维动作**，不是 bug。
2. **库里 `output_text` 全空但 md 有内容** = 设计如此（2026-10-24 用户裁决）：
   - 库里只留 `narrative`（模型原文，唯一不可重算的部分）
   - 正文每次发布**现算**，保证 vault 永远等于「当前代码 + 当前数据」
3. **副作用（真正值得注意的）**：**日报正文不可追溯**。
   一旦渲染代码改动、或 `slice_result` 被重跑，vault 里的旧 md 就再也生成不出来了
   —— 没有任何快照可回滚。这是「现算」设计的必然代价，**必须被显式接受或补偿**。

### 3.8 顺带发现的第二个问题：`link_title` 表是空的

```bash
$ python -c "
import sqlite3
con=sqlite3.connect('../../.data/digital-life/digital-life.sqlite')
for t in ('event','slice','slice_result','daily_summary','source_file','link_title'):
    print(t, con.execute('SELECT COUNT(*) FROM '+t).fetchone()[0])
"
event 1124831
slice 43257
slice_result 9679
daily_summary 753
source_file 513
link_title 0            ← 空表
```

工程 AGENTS.md §D.6 说「抓取落 `link_title` 表，`status='failed'` 也落行」，
**实测 0 行**——说明 `fetch_titles.py` 从未跑过，或跑了没写进去。
时段页的链接标题目前走的是 `linktitle` 模块的**内存/磁盘缓存兜底**，不是这个表。

**这条要报给主代理**：文档描述的机制与库内实际不符（S36 类问题）。

### 3.9 排障方法论提炼

这次排障用了 4 步，值得固化成通用套路：

| 步 | 动作 | 本例 |
|---|---|---|
| 1 | **观察异常**（不看代码）| 753 个 mtime 挤在 10 分钟 |
| 2 | **生成有限假设表**（3 个以内，每个带证伪方法）| H1/H2/H3 |
| 3 | **用数据证伪，不是用代码解释** | 查 `created_at` → 排除 H1，支持 H2 |
| 4 | **意外发现 > 原假设**（本例：output_text 全空）| 打开了一个更大的问题 |

**关键纪律**：第 3 步之前**不许读渲染代码**。一旦先读代码，就会开始「用代码解释现象」，
而现象的真实原因可能在数据层（S17：禁止凭表象直接判断）。

---

## 4. 快速对照表（贴墙上）

| 现象 | 第一反应查什么 | 命令 |
|---|---|---|
| 日报缺设备节 | 事件数（**用别名**）| §1.1 |
| 时段页缺设备 | 先确认是不是「0 行不生成 tab」| §1.2 |
| 视觉空壳 | `_meta.images_sent` | §1.3 |
| 413 | **退化图**，不是体积 | §1.3 |
| 429 | **`message` 有没有 rpm**，不是 `type` | §1.3 |
| 建页慢 | **SQL 条数**随不随片数增长 | §1.4 |
| 计数对不上 | 先问「这两个数该相等吗」| §1.5 |
| database locked | 查 PID，**别删文件** | §1.6 |
| 产出与库不符 | 想想是不是「现算」设计 | §3 |

---

## 附录：可重放命令

```bash
cd 01-Projects/digital-life
export PYTHONUTF8=1
unset DL_DB DL_WORK_DIR

python tmp/a3_diag_demo.py     # 日报 created_at / output_text 分布
python tmp/a3_diag_demo2.py    # 库 vs vault 产物对比
python tmp/a3_tmp_stat.py      # tmp 目录盘点（见 tmp-cleanup.md）
```

---

## 附录 D：采集期间观测到的 smoke 回归波动（重要现场证据）

> 本附录记录的是 **2026-10-25 12:0x–12:4x 期间**，多个并行子代理同时改代码时，
> 同一条 `python scripts/smoke.py` 的三次真实输出。这是「**测试基线在多人并发下不可信**」的直接证据。

| 时刻 | 通过/失败 | 失败项 | 说明 |
|---|---|---|---|
| 12:0x | **187 / 0** | — | 我采集基线时的状态 |
| 12:3x | **191 / 1** | `T36d2 网站与时间线用同一个真相函数` | `linktitle.resolve()` 同源改造进行中 |
| 12:4x | **183 / 1** | `T31 额度看门狗` → `NameError: name '_web_src' is not defined` | `smoke.py` 自身被改出了未定义变量 |

### D.1 第三次失败是真缺陷，不是假红

```
$ grep -n "_web_src" scripts/smoke.py
1344:        _web_src = _insp2.getsource(_sd_web._page_label)
1347:        _web_code = _code_only(_web_src)
```

`_web_src` **只在 L1344 定义、L1347 使用**，两者在同一个 `try` 块内。
但 T31（额度看门狗，L1170 附近）却抛出了 `NameError: name '_web_src' is not defined`。

**这说明 `_web_src` 在 T31 的 `except` 兜底里被引用了**——即
`check("T31 额度看门狗", False, f"...{_web_src}...")` 之类的写法。
T31 本体一旦抛异常，兜底的 `f-string` 去引用一个**作用域外**的变量，于是
「原本应该报告 A 的错误」变成了「报告 B 的 NameError」。

**这正是 `testing.md` §2.1 说的风险 A 的活体样本**：

> 兜底把异常变成一条粗粒度失败 → 定位信息丢失。
> 更糟的是，如果兜底自己的 `detail` 表达式又出错，**连原始异常都被吞掉**。

**改进建议（强化 `testing.md` §2.1）**：

```python
# 反例（当前形态）：detail 里引用可能未定义的外部变量
except Exception as _e:
    check("T31 额度看门狗", False, f"{type(_e).__name__}: {_e} web={_web_src}")

# 正例：detail 只用当前作用域内必然存在的变量
except Exception as _e:
    check("T31 额度看门狗", False, f"{type(_e).__name__}: {_e}")
```

> **判据**：`except` 里的 `detail` 表达式，只允许引用
> ① 异常对象本身 ② 该 `try` 块**之前**就已定义的变量。不得引用块内变量——
> 块内变量恰恰是「可能在赋值前就抛异常」的那一批。

### D.2 这条波动本身说明的事

1. **181/187/191/183 都不是「常量」**，而是同一小时内不同代码状态的快照。
   这印证了 `testing.md` §6 的 S60 建议：**文档不写死数字，只写命令**。
2. **多子代理并发改同一批文件时，任何一次 smoke 结果都只对该时刻的代码树有效**。
   主代理汇报「冒烟通过」时，必须附**它自己跑的末行输出 + 跑的时刻**，不能引用本文的数字。
3. **兜底断言自身也可能出错**——这是现有 smoke 结构的一个未覆盖风险点，
   建议在 `testing.md` §2 的假绿风险清单里追加为「风险 F：兜底 detail 引用块内变量」。
