# 工作流：从原始痕迹到可读产出（2026-10-24 整理）

> 读者：要改这个模块的人。本文只讲**数据怎么流动、每一步的输入输出与判定标准**，
> 不讲踩坑史（那些在 `perf-regression-*` / `413-degenerate-image-*` 等复盘里）。
> 规则条文在 `AGENTS.md §C`（十三条 DL），接口契约在 `docs/spec/interfaces.md`。

---

## 一、五个阶段（总览）

```
① 采集入库        ② 切片           ③ 片层总结        ④ 日层叙事       ⑤ 发布
raw/ 源文件  →  event 表  →  slice 表  →  slice_result  →  daily_summary  →  vault 文件
              （统一事件）  （15min 网格）  （视觉/文字读图）  （二次总结）      （md + html）
```

| 阶段 | 入口命令 | 写哪张表 / 哪个文件 | 判定「做完」的标准 |
|---|---|---|---|
| ① 采集 | `cli ingest --source X` | `event` / `source_file` | `cli verify` 各源条数与跨度合理 |
| ② 切片 | `cli slice --device D` | `slice` | 有图叶子片的 `SUM(image_count)` == 源 `kind='screen'` |
| ③ 片层 | `scripts/batch_vision.py` | `slice_result` | 所有含图叶子片都有行（`NOT EXISTS` 为空）|
| ④ 日层 | `scripts/rebuild_daily.py` | `daily_summary` | 每个 `date_key` 都有行 |
| ⑤ 发布 | `cli publish` + `build_slice_html` | vault | 文件数与 manifest counts 对得上 |

**顺序不可倒**：⑤ 只搬运不重算，所以必须 ③ → ④ → ⑤。
改了 ③ 要重跑 ④（日报是快照），改了 ④ 要重跑 ⑤。

---

## 二、① 采集入库

**输入**：ManicTime zip / ScreenMemo 库 / IQQZ3 目录 / 浏览器历史导出。
**输出**：`event` 表，一行一个时间段或一次访问。

关键约束：

- `event_id = sha1(device|source|kind|start_ms|end_ms|dedupe_key)` —— **device 参与主键**，
  改设备名会造出全新行（历史教训：一次设备改名产生 122 万条重复行）。
- 按设备过滤**一律走 `devices.sql_device_filter()`**（生成 `IN (?,…)` 并把旧别名列入），
  禁止写 `device = ?`（DL-7）。
- 源特有字段进 `payload` JSON，**不改表结构**。
- 点事件（`end_utc_ms IS NULL`）消费时必须补 `POINT_EVENT_MS = 60_000`（DL-8）。

---

## 三、② 切片

**输入**：`event`。**输出**：`slice` 表。

- 15 分钟 UTC 网格；片内图片数 > `MAX_IMAGES(24)` 时递归二分，子片 `split_of` 指父片。
- `image_count` 允许 > 24（二分到极限后不再切，显式承载）。
- `slice.status` **全库不回写**，永远是 `pending`/`split`；
  「这一片做没做」的唯一判据是 `NOT EXISTS(SELECT 1 FROM slice_result WHERE slice_id=…)`，
  **不要用 status 选片**（§H 已知边界）。

---

## 四、③ 片层总结（最容易出错的一段）

**输入**：一个片的截图（或文字原料）+ DB 时间线基线。
**输出**：`slice_result` 一行，含 `overall_summary`（三节）与 `structured_json`（含 `_meta`）。

### 4.1 两种原料（reader）

| reader | 用于 | 观察来源 |
|---|---|---|
| `model` | pc-main | 送图。截图从 ManicTime zip 直读 → 缩图 → 均匀抽样 ≤8 张 |
| `text` | phone-2 | **不送图**。用 ScreenMemo 自带的 `payload["description"]` 中文画面描述 |

⚠️ 因为 `text` 模式天生 `images_sent=0`，**任何按 `images_sent=0` 找"盲总结"的查询都必须
加 `reader='model'` 限定**，否则会把 5,940 条手机侧结果误当盲总结重做（哨兵 T31h）。

### 4.2 前处理四道闸（缺一道就会莫名失败）

1. **退化图过滤**（`_is_degenerate`）：宽高比 >12 或短边 <64 不送。
   一张 1280×20 的畸形截图单独发送就必触发 413，把整片拖死。
2. **缩图**（`_downscale`）：长边 1280、WEBP q=72。
3. **抽样**（`_sample_evenly`）：最多 8 张，保留首尾，重编 `idx`。
   ≥16 张必被上游判 `400 sensitive image`。
4. **DB 基线**（`_db_timeline`）：结构字段永远以 DB 为准，模型只补画面内容。

### 4.3 三层兜底

| 层 | 触发 | 动作 |
|---|---|---|
| 空内容 | `content` 为空 | 翻倍 `max_tokens` 重试，起 8192 封顶 65536，最多 40 次 |
| 结构不合格 | `schema_check` R1–R9 不过 | 把**具体违规理由**回灌重做，最多 5 轮；**仍不合格照常写库**并记 `schema_ok=False` |
| 传输错误 | 429 / 413 / 5xx | 退避重试（2 次，20s+40s = 60s，刚好跨一个 rpm 窗）；400 不重试 |

**`batch_vision` 不得写后即删**。清理口径只有一个：`scripts/prune_results.py`。

### 4.4 片层输出的形态（当前状态，待改）

现在 `overall_summary` 是**三节清单**：

```
## 关键操作
- 06:03:30-06:04:16：在 Obsidian 章梗概笔记中梳理…
## 主要活动
- 小说大纲撰写（约 12 分钟）
## 重点内容
- 《基因飞升》的整体世界观：…
```

🔴 **用户要求改成「像写日记一样」的连续叙述**，不要时间戳前缀、不要清单句。
改法见本文第六节。

---

## 五、④ 日层叙事

**输入**：当天所有片层总结（按时间序整篇）。**输出**：`daily_summary.output_text`。

`DAILY_PROMPT` 的要求（不要动，这是定稿）：自然段、不用列表、不用时间轴、
不省略、不编造、不写周期回顾。

### 5.1 渲染函数的分工（关键）

`render_narrative()` 产出三块，**只有第一块该存在**：

| 块 | 内容 | 现状 |
|---|---|---|
| `## 今天` | 模型写的自然段叙事 | ✅ 正确 |
| `## 电脑使用` | 应用/网站/专注段/时间线 **统计罗列** | 🔴 **违反 R12，应删除** |
| `## 视觉总结` | **61 片总结逐片原样粘贴** | 🔴 **违反 R12，应删除** |

`docs/spec/requirements.md` R12 原文：
> 日报**只写突出部分**（前 6 应用 + 前 5 域名各一句 + 压缩时间线），
> **全量明细一律归时段网页** `10-时段/YYYY-MM-DD.html`。

实测后果：`2026-08-13.md` = **1,477 行 / 138 KB**，读起来是审计日志不是日记。

---

## 六、待改事项（本轮要做完）

| # | 改什么 | 判定标准 |
|---|---|---|
| 1 | 片层 `screenmemo-v1` 提示词 → 叙事体 | 单片 `overall_summary` 是连续段落，无时间戳前缀、无 `- ` 清单 |
| 2 | `render_narrative` 删掉 `## 视觉总结` 全量粘贴 | 日报行数从 1,477 降到合理量级（目标 < 200 行）|
| 3 | `render_narrative` 把统计罗列压成一句话 | 前 6 应用 + 前 5 域名各一句 + 压缩时间线，符合 R12 |
| 4 | 重做 `2026-08-13` | 当日日报与页面都体现新格式 |

改完必须：冒烟 171/0 → 注入验红 → 抽样人工读一遍。

---

## 七、⑤ 发布

`cli publish` 写 `20-日报/*.md` + NDJSON + `_index/manifest.json`；
`build_slice_html.py` 写 `10-时段/YYYY-MM-DD.html`（一天一页）。

**全量明细的唯一归属是网页**：时间线 / 应用 / 网站 / 视觉四 section，按设备 tab 切换。
日报只留突出部分——这条是 R12，判定日报是否"格式对"的唯一标准。

---

## 八、常用命令（并行参数不要漏）

```bash
cd 01-Projects/digital-life
export PYTHONUTF8=1
unset DL_DB DL_WORK_DIR

# ③ 片层（32 并发 + 240s 超时 + 60s 退避 = 净 5.7 片/分）
DL_LLM_TIMEOUT=240 python scripts/batch_vision.py --all --device pc \
    --reader model --workers 32 --rounds 14

# ④ 日层（753 天约 3.7 分钟）
python scripts/rebuild_daily.py --workers 4

# ⑤ 发布（753 页约 6 分钟）
python scripts/build_slice_html.py --all --workers 6
python -m digitallife.cli publish --no-zip

python scripts/smoke.py            # 必须 171/0
python scripts/prune_results.py    # 不合规必须 0
```
