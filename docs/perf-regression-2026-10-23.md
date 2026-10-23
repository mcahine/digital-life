# 性能事故复盘：load_slices 从 1.6s 退化到 103s

> 2026-10-23。一个「加个标注」的小改动，把整条产出链拖慢了 60 倍。

## 一句话

给「无画面描述」加标注时，我在 `load_slices` 里**逐片查了一次 event 表**。
单日 267 片 → 267 次全表扫描 → `load_slices` 从 1.6 秒变成 **103 秒**，
753 页时段页重建从 20 分钟变成 **5 小时**。

## 数字对比

| 操作 | 事故前 | 事故中 | 修复后 |
|---|---|---|---|
| `load_slices`（单日 phone-2） | 1.6s | **102.9s** | **1.6s** |
| `slices_for_day`（日报） | 1.4s | **70.7s** | **1.39s** |
| `build_day`（单页 HTML） | ~3.5s | **24s** | **3.5s** |
| 753 页全量重建 | ~40 分钟 | **5 小时+** | ~23 分钟 |

## 根因

我加的辅助函数长这样：

```python
def _slice_has_description(con, slice_id):
    rows = con.execute(
        "SELECT payload FROM event WHERE kind='screen' "
        "AND start_utc_ms >= (SELECT start_utc_ms FROM slice WHERE slice_id=?) "
        "AND start_utc_ms <  (SELECT end_utc_ms   FROM slice WHERE slice_id=?)",
        (slice_id, slice_id)).fetchall()
```

然后 `load_slices` 对**每片**都调一次。

### 为什么慢

`EXPLAIN QUERY PLAN` 显示：

```
SEARCH event USING INDEX ix_event_kind (kind=?)
```

**走的是 `kind` 索引，不是时间索引**。`kind='screen'` 命中 10 万+ 行，
再逐行比较 `start_utc_ms`。每片扫一遍全表 → 267 片就是 267 遍。

已有的 `ix_event_time` 和 `ix_event_dev_time` 完全没用上，
因为 WHERE 里 `kind` 先出现，优化器选了指选择性更差的索引。

## 修法

**一次性把当日的「有描述时间点」全取出来，再在内存里比较**：

```python
# load_slices / slices_for_day 开头，只查一次
desc_ts = [row[0] for row in con.execute(
    "SELECT start_utc_ms FROM event "
    "WHERE start_utc_ms >= ? AND start_utc_ms < ? AND kind='screen' "
    "AND payload LIKE '%\"description\"%'", (lo, hi))]

# 循环里纯内存判断
_has_desc = any(ss <= t < ee for t in desc_ts)
```

**1 次范围查询替代 N 次全表扫描。** 加上 `start_utc_ms` 走范围条件，
优化器会用 `ix_event_time`。

## 教训

### 1. 循环里出现 SQL，先想「能不能提出来」

逐片/逐行调用含 SQL 的函数，是最容易埋的 O(n²) 雷。
本次 267 片 × 10 万行 = 2,670 万次比较，全部浪费。

### 2. 「加个字段」也要看它怎么取

我给 `has_description` 设计时只想着「逻辑对不对」，没想「每片查一次库的代价」。
**功能正确 ≠ 实现可接受。**

### 3. 现象是「慢」，但根因不是「数据量大」

我一开始归因为「页面体积从 248KB 涨到 1MB，所以慢」——**这是错的**。
页面大小只影响写文件，不影响查库。真正的 60 倍退化藏在一条没注意的 SQL 里。

用户一句「又不是跑模型，现在就是跑程序，怎么会这么慢」直接点破了我的错误归因。

### 4. 慢的时候先 profile，别猜

```python
t = time.time(); load_slices(con, d, "phone-2"); print(time.time() - t)
# 102.9s  ← 一眼看出问题在哪一层
```

三个函数分别计时，瓶颈立刻现形。我如果早点这么做，能省下两小时。

## 当前状态

修复后：
- 冒烟 **143 / 0** 未回归
- 标注数量正确（2026-07-21：192/202 有描述，与逐片实测一致）
- 753 页重建约 23 分钟
