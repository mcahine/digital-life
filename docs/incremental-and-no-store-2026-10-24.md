# 增量重建与「日报不入库」（2026-10-24）

> 用户裁决：
> ① 「视觉总结的片层内容可以记录数据库里，但是日报不需要存储到数据库，
>    包括时段也是，本身就是基于数据库内容进行的导出」
> ② 「日报和时段别总是全量重建，太费时间，选择日期重建挺好」
> ③ 「乃至日报和时段的重建并不是单个文件全部重建，把函数拆分，每个函数负责一个字段，
>    再交给其他函数进行总控」

---

## 一、日报不入库

### 改法

`daily_summary` 从「存渲染结果」降为「只存模型叙事原文」：

| 字段 | 改前 | 改后 |
|---|---|---|
| `output_text` | 5.6 MB 渲染结果 | **空**（现算）|
| `structured_json` | 5.2 MB 全量结构 | 只含 `narrative`（1 天有值）|
| `device_breakdown_json` | 1.1 MB | `{}` |

**实测：12.0 MB → 0.07 MB，省 11.9 MB。**

### 为什么只留 narrative

753 天里只有 **1 天**有模型写的叙事原文，其余 752 天是模板拼接。
模板随时可重算；模型叙事丢了就得重新调用 API。所以它是唯一值得存的字段。

### publish 改为现算

```python
def _daily_text(con, day):
    from .daily import build_daily
    res = build_daily(con, day, persist=False)   # 不写库
    return str((res or {}).get("output_text") or "").strip()
```

这顺带消掉一个长期隐患：以前库里的 `output_text` 是历史快照，改了渲染代码
忘了重跑，`publish` 就会把旧正文搬进 vault（AGENTS.md §G.10 记的老坑）。
现在不存在这个问题——正文永远是当前代码的即时输出。

`manifest.counts.daily` 也随之从「查表行数」改为「实际落盘文件数」。

---

## 二、checksum 缓存（两层）

### 第一次尝试放错了层

我先在**渲染层**加缓存（四个 section 片段），实测：

| | 冷 | 命中 |
|---|---|---|
| 建页耗时 | 3.59s | 1.96s |

看着省了 45%，但拆开测就清楚了：

```
build_day_structure: 1.301s   ← 查 event
load_slices:         1.867s   ← 查 slice + slice_result
数据加载合计:        3.168s
渲染四个 section:     约 0.3s
```

**取数据占 91%，渲染只占 9%。** 缓存在渲染层等于优化那 9%。

### 下沉到数据层

`tmp/.datacache/<date>.json` 存 `build_day_structure` + `load_slices` 的快照，
指纹 = 三张表的 `(COUNT(*), MAX(created_at))`。

**实测：3.62s → 0.66s（5.5×）**，且冷/热产出的 SHA1 完全一致（`2fd4ddb6273432f2`）。

`tmp/.pagecache/<date>.<section>.json` 保留四个 section 的片段缓存，作为第二道。

### 顺带修掉一处重复查询

`_day_devices` 自己调了一次 `build_day_structure`，`build_ctx` 紧接着又调一次——
同一天同一设备白查两遍（实测约 1.3s）。改成把已算好的结构传进去。

---

## 三、只重建指定日期

`rebuild_daily.py` 补 `--days`：

```bash
python scripts/rebuild_daily.py --days 2026-09-15 2026-09-16
```

`build_slice_html.py` 本来就有 `--date`。

---

## 四、过程中抓到的三个自身缺陷

### 1. 差点用「报错空转」的假数据报喜

第一轮测出「0.40s 冷启动」，差点直接报提速 9 倍。实际是建页失败：

```
FAIL OperationalError: ambiguous column name: created_at
生成 0 天单页（共 0.0 MB）
```

`slice` 和 `slice_result` 都有 `created_at`，join 后必须写 `r.created_at`。
**教训：计时要看返回码，0.4s 的「成功」比 3.6s 更可疑。**

### 2. 哨兵写得太弱，注入不变红（S20 白做）

T35a 第一版只检查 `_daily_text` 的**函数体**。注入「调用点改回读库」后仍然全绿——
因为注入的是 `for day in days:` 那一行，不在函数体里。
改为同时断言调用点 `text = _daily_text(con, day)` 后才变红。

### 3. 改完代码以为数据就变了

把 `publish` 改成现算后，我查 `daily_summary` 发现仍占 12 MB——
因为那 753 行是**之前**写下的，`persist=False` 根本不写库，旧数据不会自己消失。
必须显式执行一次清理（`tmp/lean_down.py`）才真正省下 11.9 MB。

---

## 五、哨兵（T35a–c）

| 哨兵 | 断言 | 注入验红 |
|---|---|---|
| T35a | `publish` 走现算 + 调用点未改回读库 | 改回读库 → 变红 ✅ |
| T35b | `build_daily` 支持 `persist=False` | — |
| T35c | 数据层缓存指纹用 `r.created_at` | — |

冒烟 **177 → 180**。

---

## 六、未做的一件事（有明确理由）

剩余 **291 片**片层缺时间戳前缀，我停在这里没硬补：

| 片 | DB 区间数 | 文本条目数 |
|---|---|---|
| `ea0006a0b929` | 23 | 15 |
| `9c0b812cc118` | 39 | 17 |
| `c979b29fa727` | 20 | 16 |

DB 记录的事件数是文本条目的一倍多——一段 15 分钟切了 20 个应用，总结只写了 15 条行为。
**硬对齐就是编造对应关系**。

试过两条路都不行：
- 纯 Python 机械分配 → 4 条描述被套上同一个区间，更糟
- 让模型按 DB 依据对齐 → 50 片挂 37

继续只能视觉重建（约 28 小时额度）。这 291 片保持现状：内容完整，只是没有时间戳前缀。