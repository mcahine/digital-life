# 数据源格式约定与设备标注

> 读者：以后接入新数据源、或新增设备时的 AI 代理 / 我自己。
> 真源：`digitallife/domain/devices.py`（设备注册表）｜`digitallife/ingest/sources/*.py`（各源适配器）

---

## 一、设备标注（多手机 / 多电脑）

### 为什么必须标

- 手机会**几部同时用**，时间上必然重叠。只有 `pc`/`phone` 两个粗粒度值，
  重叠时段的记录会混在一起，说不清「哪段时间在哪台设备上」。
- 电脑现在一台，但以后会有第二台。**现在不标，将来就要重导全部历史。**
- 原始文件里**没有可靠的设备标识**（实测：ManicTime 截图路径只有分辨率，
  IQQZ3 CSV 无设备列），所以设备归属只能由**导入配置声明**。

### 设备 id 规则

```
<类别>-<短名>        例：pc-main / pc-work / phone-1 / phone-2
类别 ∈ {pc, phone}   只允许小写字母数字与 -_，2-32 位
```

**一旦定下不要再改** —— `device` 参与 `event_id` 计算（`sha1(device|source|kind|start|end|key)`），
改了等于把所有历史记录判成新记录。

### 注册表（唯一真源）

`digitallife/domain/devices.py`：

> ⚠️ **本文不复制设备清单**（会漂移）。唯一真源是 `digitallife/domain/devices.py` 的 `DEVICES`，
> 当前为 **三台**（一台 PC + 两台不同的手机）。要看清单请直接读代码或跑：
>
> ```bash
> cd 01-Projects/digital-life && python -c "from digitallife import devices as d; [print(x.device_id, x.name, x.label) for x in d.all_devices()]"
> ```

**新增设备 = 在 `DEVICES` 里加一行**，历史数据不受影响。
**两台手机 ≠ 一台**：`iQQQ Neo11`（ScreenMemo）与 `iQQQZ3`（IQQZ3）是不同设备，
换设备归属会生成新 `event_id`，旧记录不会自动消失——重导后必须显式清理旧 `device` 的行，否则数量翻倍。

### 导入时决定归属

优先级：`DL_DEVICE_<SOURCE>` 环境变量 > 适配器默认值。

```bash
# 第二部手机的 ScreenMemo 数据
DL_DEVICE_SCREENMEMO=phone-2 python -m digitallife.cli ingest --source screenmemo
# 第二台电脑的 ManicTime 备份
DL_DEVICE_MANICTIME=pc-work python -m digitallife.cli ingest --source manictime
```

### 历史数据兼容

旧库里的 `pc` / `phone` 通过 `LEGACY_ALIASES` 自动解析到注册 id，**不需要迁移数据**：

```
pc     -> pc-main
phone  -> phone-1
```

> **S45：旧别名各自的条数属于增长型指标，本文不写死。**
> 需要条数时现查（采集日期随查询自带）：
>
> ```bash
> cd 01-Projects/digital-life && python -c "import os;from digitallife.store import open_db;c=open_db();print(c.execute('SELECT device,COUNT(*) FROM event GROUP BY device').fetchall())"
> ```
>
> 2026-10-25 实测的完整快照见 `04-Areas/数字人生/AGENTS.md` §B.1（带日期，非本文职责）。

### 查询时不能写 `device = ?`

库里的历史行仍是旧值（`pc`/`phone`），新写入是注册 id。因此**任何按设备过滤的 SQL
必须用 `devices.sql_device_filter()`**，它生成 `device IN (?,…)` 并把别名一并列入：

```python
from digitallife import devices as dv
sql, params = dv.sql_device_filter("pc-main")   # -> ("device IN (?,?)", ["pc-main", "pc"])
```

反过来，`device = 'pc-main'` 这类精确匹配会**静默返回空**（时段 0 片、事件 0 条、
视觉选不到片）。2026-10-21 改造时正是 9 处精确匹配同时踩中（daily×4 / slicer×3 /
vision×1 / publish×2），全部表现为「明明有数据却查不到」。

### 设备呈现（单日页）

一天一个 HTML，页内四个 section（时间线 / 应用 / 网站 / 视觉）各自带设备 tab。规则：

- 某个设备在某 section **0 行时不生成该 tab**（实测「手机 0」点开只有一句空提示，纯噪音）。
- 只有一个设备有内容时，整条 tab 控件不渲染。
- 设备标签走 `contract.device_label()`，返回**真实设备名**（`D-001` / `iQQQZ3` / `iQQQ Neo11`）。
  **不要退回「电脑 / 手机」**——用户 2026-10-22 明确否掉了那个写法。
  （原文这里写的是「同类只有一台时显示电脑/手机」，与 `device_label()` 实现不符，2026-10-25 更正。）
- 手机侧的 `timeline` 目前 `url` 恒为空（ScreenMemo/IQQZ3 不记 URL），
  所以手机时间线是**应用级**，页面上要写明这一点，不要让人误以为漏数据。

### 日报里的显示

- 同类只有一台 → 沿用「电脑使用 / 手机使用」；
- 同类有多台 → 用**真实设备名**（当前实测为 `## iQQQZ3 使用` / `## iQQQ Neo11 使用`）。

> 原文此处写「主力电脑使用 / 手机 2 使用」（用的是 `label`），与代码不符：`daily._render_device`
> 走的是 `devices.name_of()`。2026-10-25 更正。**权威定义见
> `04-Areas/数字人生/AGENTS.md` §「日报结构契约」。**

---

## 二、各数据源格式约定

新增数据源或格式变化时，先对着这张表核对；**不要改 `event` 表结构去容纳源特有字段**，
特有信息进 `payload` JSON。

### manictime（pc）

| 项 | 约定 |
|---|---|
| 形态 | 单个 zip，内含 `ManicTimeCore.db` / `ManicTimeReports.db` + `Screenshots/` |
| 时间 | DB 内为本地时间，转 UTC 毫秒 |
| 结构权威 | **是**。应用名/窗口标题/URL/活跃状态一律以此为准 |
| 截图 | zip 内相对路径 `Screenshots/YYYY-MM-DD/*.webp`，**从不落盘**，直读 |
| 采样 | 约 1 分钟一张 |

### screenmemo（phone）

| 项 | 约定 |
|---|---|
| 形态 | 分片 SQLite zip（实测 42 分片，**仅 16 个含 `shots` 表**）|
| 截图路径 | `/data/user/0/...` 这种**安卓设备路径**，本机不存在 |
| 结论 | 结构字段可用；**图片送不进模型**，除非另外提供图片文件 |

### iqqz3 / AppUsage（phone）

| 项 | 约定 |
|---|---|
| 形态 | 每天一个目录，内含 9 类 CSV（`Activity`/`App`/`Battery`/`Install`/`Notification`/…）|
| 目录位置 | 顶层 `YYYY-MM-DD/`（2025-01~04）+ `daily/YYYY-MM-DD/`（2025-05 起）|
| **多快照** | 一天可能有 **2-3 个快照**（如 `_13-00-00` 与 `_19-59-45`）。**必须全部读**，只读最后一个是 bug（曾丢 Install 244 行 / Notification 99 行）|
| 去重 | 两个快照内容常相同，用 `event_id` 去重，不要靠文件名 |
| 时间 | `Activity` 有 `Date`+`Time`；`Usage` 只有当日汇总，按 `00:00 + 序号秒` 错开 |
| 假锁屏 | 客户端被杀后会膨胀成超长锁屏（实测 3.7 天），需用截图证伪 |

### tai（pc）

| 项 | 约定 |
|---|---|
| 形态 | 3 个 CSV：时段 / 每日 / 网页统计 + 2 个 xlsx |
| **文件名陷阱** | 文件名写「2023年03月-2025年03月」，**实际数据只有 2024-12-31 ~ 2025-03-11**。判断范围要读数据首尾，不要信文件名 |
| 每日.csv | 日聚合，与时段数据重复 → 按 SKIP 处理，不重复入库 |

### shion（pc）

| 项 | 约定 |
|---|---|
| 形态 | 浏览器历史 SQLite |
| **无 end 时间** | 28,805 条 **100% 没有 `end_utc_ms`**（只记访问瞬间）。dbview 会给 app/web 点事件补 60 秒默认时长，否则 `ce>cs` 恒假、整类记录被静默丢弃 |

---

## 三、数据落盘分层（先看这张表再决定往哪写）

| 内容 | 位置 | 说明 |
|---|---|---|
| 事件库 | `知识库/.data/digital-life/digital-life.sqlite` | 678 MB；`paths.db_path()` 取，**禁止硬编码** |
| 全量导出 | `.data/digital-life/exports/digital-life-events.ndjson` | 530 MB |
| 解包中间件 | `.data/digital-life/work/` | manictime / screenmemo / shion 的解包结果，可删 |
| 清洗报告 | `04-Areas/数字人生/_index/clean_report.json` | 只在跑 `cli ingest` 时产出；缺文件 ≠ 没清洗（账在 `source_file`）|
| 发布清单 | `04-Areas/数字人生/_index/manifest.json` | `/api/dl/stats` 读它，缺则 503 |
| 片层 / 日层 md | `04-Areas/数字人生/10-时段/`、`20-日报/` | 进 git |
| 时段网页 | `04-Areas/数字人生/10-时段/<日期>.html` | 进 git，浏览器打开 |

`.data/` 由根 `.gitignore` 的 `*.data` 规则排除；`_index/*` 排除但 `!manifest.json` 放行。

---

## 四、接入新源 / 新格式的检查清单

1. 在 `sources/` 加适配器，`name` 与 `model.SOURCES` 一致；
2. `device` 填**注册 id**（不是 pc/phone）；
3. 在 `sources/__init__.py` 的 `ADAPTERS` 注册；
4. 在 `model.SOURCES` 加入源名（否则 `Event.__post_init__` 会拒）；
5. 在 `devices.py` 注册它对应的设备（若引入新设备）；
6. 补冒烟断言：行数对拍（全字段去重 vs 库内）、时间范围、幂等；
7. 跑 `python scripts/smoke.py`，**必须全绿**；
8. 注入一个可观测缺陷确认断言会红（S20）。