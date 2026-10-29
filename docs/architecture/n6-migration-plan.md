# N6 目录重构方案（可执行 + 可回滚）

> 目的：按**工作管线顺序**分组，而不是按文件类型堆放。
> 前置：T1/T2 已完成（pytest 26 条 + smoke 213/0 + verify_vault 12/0），有测试网兜底。
> 本文件是**执行前的方案**，含逐步骤判据与回滚点。

## 一、为什么不能一次性大搬（真值）

引用面实测（`tmp/n6_recon.py`）：

| 目标 | 需改代码引用 | 需改文档引用 |
|---|---|---|
| `digitallife/` 19 个模块重新分组 | **97 处** | **125 处** |
| `scripts/` 9 个脚本移动 | **164 处** | — |
| 合计 | **386 处** | |

一次搬 27 个文件 = 386 处同时改，且 `git` 会把「移动」记成「删除+新增」（历史断掉），
**无法用「产物逐字节不变」单独证明每一步都正确**——中间态一旦错，回滚代价高。

## 二、目标结构（按管线，不是按类型）

```text
digitallife/
  __init__.py
  cli.py                  # 入口（唯一）
  paths.py                # 路径（唯一）
  textutil.py             # 跨层小工具（T3 已建）
  ingest/                 # ① 采集 -> 入库
    __init__.py
    model.py store.py schema.py clean.py tz.py
    sources/              #   （现有 5 适配器原样搬入）
  domain/                 # ② 领域：切片与当日结构
    __init__.py
    slicer.py dbview.py devices.py timeline_merge.py
  produce/                # ③ 加工：视觉 + 叙事
    __init__.py
    vision.py llm.py schema_check.py ocr.py
    prompts/              #   （现有 1 个提示词模块）
    daily/                #   （T4 已拆好的 6 文件）
  emit/                   # ④ 发布与呈现
    __init__.py
    publish.py
    render/               #   （现 scripts/slice_pages/ 搬入）
scripts/                  # 只剩运维入口，不再是主体
  smoke.py verify_vault.py batch_vision.py fetch_titles.py
  rebuild_daily.py prune_results.py finish_phone2.py rewrite_style.py
  build_slice_html.py
tests/                    # 不变
```

## 三、分步执行（每步独立可验收、可回滚）

| 步 | 动作 | 改动面 | 出口判据 | 回滚 |
|---|---|---|---|---|
| **N6-a** | `digitallife/{model,store,schema,clean,tz}.py` + `sources/` → `digitallife/ingest/` | 约 30 处 import + 文档 | ①pytest 全绿 ②smoke 213/0 ③verify_vault 12/0 ④753md+753html 逐字节不变 ⑤`git status` 显示 rename | `git checkout` 该步 |
| **N6-b** | `{slicer,dbview,devices,timeline_merge}.py` → `digitallife/domain/` | 约 28 处 | 同上 | 同上 |
| **N6-c** | `{vision,llm,schema_check,ocr}.py` + `prompts/` → `digitallife/produce/`；`daily/` 也移入 | 约 25 处 | 同上 | 同上 |
| **N6-d** | `publish.py` → `digitallife/emit/`；`scripts/slice_pages/` → `digitallife/emit/render/` | 约 20 处 + pyproject package-dir | 同上 + `import slice_pages` 的兼容别名 | 同上 |
| **N6-e** | 更新文档 125 处 + `AGENTS.md §B` 目录表 + `interfaces.md` 模块清单 | 纯文档 | 文档里的路径全部可 `ls` 到 | 同上 |

**每步都必须**：
1. 用 `git mv`（不用 `mv`，否则 git 记成删除+新增，历史断掉）；
2. 先 `git status` 核对暂存区，**禁止 `git add -A`**（S38）；
3. 跑四项出口判据，全绿才进下一步；
4. 每步一个 commit（可回滚边界）。

## 四、兼容层（保证外部调用不断）

`digitallife/__init__.py` 里保留旧路径别名：

```python
# N6 兼容层：旧路径继续可用，新代码用新路径
from .ingest import clean, model, schema, store, tz           # noqa: F401
from .domain import dbview, devices, slicer, timeline_merge   # noqa: F401
from .produce import llm, ocr, schema_check, vision           # noqa: F401
from .emit import publish                                     # noqa: F401
```

> `import digitallife.clean` 这类老写法要么在 `__init__` 里注册 `sys.modules` 别名，
> 要么在 `digitallife/clean.py` 放一层 `from .ingest.clean import *` 薄壳。
> **两者择一，并在 `tests/test_package.py` 加断言守住。**

## 五、本方案未执行的原因（如实记录）

- 改动面 386 处，用户明确要求「顺序倒了就是返工」「不要蒙眼手术」；
- N6 是 §5.2 的**最后一步**，B3 批注明「必须等 T1/T2」——T1/T2 刚在本轮完成；
- 单步 N6-a 就需约 30 处精确改动 + 4 项验收，一轮内做完 a–e 五步风险过高。

**建议**：按 a→b→c→d→e 分 5 轮执行，每轮独立验收与提交。
本轮已把方案、判据、兼容层、回滚点全部落盘到本文件，**执行可下一轮直接照做**。