# digital-life

把散在不同设备、不同软件里的个人使用记录，清洗成**一张统一事件表**，再分两层加工成总结：
**模型写的片层总结** → **模型写的日层叙事日报**，供第二大脑的日记系统消费。

> **改代码前先读 [`AGENTS.md`](./AGENTS.md)** —— 那里写了"怎么改才不闯祸"（含 WAL 复制毁库、
> 幂等键丢行、视觉参数定论、设备别名查询等实测教训）。本文件只做速览。

> 四条主轴：
> 1. **时间是线性的，设备只是维度** —— event 表以 UTC 毫秒为唯一主轴，`device` 是字段不是分区。
> 2. **数据库是骨架，截图是肌肉** —— ManicTime 的窗口标题/URL/活跃状态构成结构化骨架；
>    视觉模型只补画面内容，冲突以 DB 为准（`conflict_count` 可观测）。
> 3. **只产每日总结** —— 周/月/季/年由日记系统自己滚，本模块不碰 `50-回顾/`。
> 4. **Python 只搬运原料** —— 片层与日层总结都由模型写；禁止用模板把时间轴罗列成日报。

## 两层加工

```
原始痕迹（ManicTime DB + 截图 / ScreenMemo / IQQZ3 / Tai / shion）
        ↓ ingest → 统一 event 表（UTC 毫秒单一主轴）
        ↓ slice  → 15 分钟一片，超 24 图递归二分
        ↓ vision → 片层：模型读截图 + DB 结构，写完整总结（不截断、不省略）→ slice_result
        ↓ daily  → 日层：当天所有片总结整篇喂模型，二次总结成自然段叙事 → daily_summary
        ↓ publish→ 10-时段/*.md、20-日报/*.md、NDJSON、manifest
```

## 当前实况（2026-10-22 实测）

| 指标 | 值 |
|---|---|
| 统一事件表 | **1,123,058** 条（manictime 771,802 / iqqz3 203,461 / screenmemo 103,424 / shion 28,805 / tai 15,566）|
| 按设备 | `pc-main` 816,173 ｜ `phone-1` 306,885 |
| 时间片 | **96,371** 片（pc-main 33,129 / phone-1 53,064 / phone-2 10,178），15 分钟 UTC 网格，超限递归二分 |
| 截图守恒 | phone-1 103,424 / pc-main 55,527，与源 `kind='screen'` 完全一致 |
| 日总结 | **753** 天 |
| 视觉结果 | `slice_result` **7,819 片**（其中 phone-2 侧 5,940 片，已 100% 完成）|
| 主回归 | `scripts/smoke.py` **170 项** |

## 五个数据源

| 源 | 设备 | 形态 | 覆盖 |
|---|---|---|---|
| manictime | pc-main | 截图 + Reports.db 四条时间线 | 2026-08…10 |
| screenmemo | phone-1 | 分片 SQLite（42 分片，16 个含 `shots`）| 2026-05…08 |
| iqqz3 | phone-1 | 108 顶层目录 + `daily/`（287 条）| 2025-01…04 |
| tai | pc-main | 应用时段 + 网页统计 | 2024-12…2025-03 |
| shion | pc-main | 浏览器历史 | 2024-09…2025-03 |

> tai 与 shion 有 6,214 条交集，**跨源归并后恰好 33,967**。
> 设备 id 用注册表（`digitallife/devices.py`），旧值 `pc`/`phone` 经 `LEGACY_ALIASES` 只读兼容。

## 常用命令

```bash
cd 01-Projects/digital-life
export PYTHONUTF8=1          # Windows GBK 控制台必设（S33）
unset DL_DB DL_WORK_DIR      # 这两个覆盖变量会传染，收尾必须清

python -m digitallife.cli ingest  --source manictime --path <zip>
python -m digitallife.cli dbview  --date 2026-08-13
python -m digitallife.cli slice   --device pc-main
python -m digitallife.cli vision  --device pc-main --limit 10
python -m digitallife.cli daily   --date 2026-08-13
python -m digitallife.cli publish --no-zip
python -m digitallife.cli verify
```

批量与清理：

```bash
python scripts/smoke.py                                      # 170 项主回归
python scripts/batch_vision.py --all --workers 24 --rounds 8  # 批量视觉（须配 DL_LLM_TIMEOUT=240）
python scripts/batch_vision.py --all --blind --workers 24     # 重做"没送图就写了总结"的历史片
python scripts/rebuild_daily.py --workers 4                   # 重生成日报（753 天约 9 分钟）
python scripts/build_slice_html.py --all --workers 6          # 重生成单日页（753 页约 6 分钟）
python scripts/build_slice_html.py --all --prune            # 顺带删旧的日期目录与 _html/
python scripts/prune_results.py                             # 只报告；--apply 真删不合规结果
```

## 路径

| 内容 | 位置 |
|---|---|
| 代码 | `01-Projects/digital-life/`（**数据不落此处**）|
| 给人看的产物 | `04-Areas/数字人生/`：`10-时段/YYYY-MM-DD.html`（**一天一页全量**）+ `10-时段/片层/`（片层 md）、`20-日报/`、`_index/manifest.json` |
| 大体量中间物 | `知识库/.data/digital-life/`：`digital-life.sqlite`（678 MB）、`exports/*.ndjson`（530 MB）、`work/`（解包原始 DB）|
| 原始件 | `00-Inbox/` 原地读取，不复制、不留副本 |

> `.data/` 由根 `.gitignore` 的 `*.data` 规则排除；`_index/` 只剩 `manifest.json` 入库。
> 全部中间物**可重建**：删掉重跑 `ingest` 即可。

## 日报 vs 时段网页（分工，2026-10-22 用户裁决）

| | 日报 `20-日报/` | 时段网页 `10-时段/YYYY-MM-DD.html` |
|---|---|---|
| 内容 | **只留突出部分**：前 6 应用 + 前 5 域名各一句 + 压缩时间线（≥2 分钟） | **全量**：所有应用、所有域名与其全部页面、全部时间线、全部片 |
| 形态 | 模型写的自然段叙事 | 按页分类的可查表 + 完整记录 |
| 谁写 | `daily` 调模型 → `publish` 落盘 | `scripts/build_slice_html.py` 渲染数据库 |

**一天一个 HTML**（2026-10-22 用户裁决；视觉照搬 `00-Inbox/index.html`）：

```text
2026-08-13.html
├── header    日期 · 数字人生 + 星期 + 规模统计 + 深浅色按钮
├── 时间线    device tab；浏览器窗口与网页已合并为一行 + 整行链接
├── 应用      device tab；全量，前 12 个之后「展开全部」
├── 网站      device tab；按域名 details，展开是该域名的每一次访问
├── 视觉      逐片卡片，全量不折叠
└── 右下 fab  目录浮钮 → 四个锚点
```

片层 md 不在这页里，落 `10-时段/片层/YYYY-MM-DD/HHMM-HHMM.md`（只有出了视觉总结的片才写）。

> 三个口径：① 一个时间戳就够（同区间的窗口行与网页行合并，第一条当整行链接）；
> ② **没有「代表页面」这一列**；③ 不写中间 JSON，删掉 html 重跑即重建。

## 文档地图

九份文档，**各管一件事，不互相复制**：

| 文件 | 读者 | 管什么 |
|---|---|---|
| `[AGENTS.md](./AGENTS.md)` | 改代码的 AI | **动手前必读**。DL-1…DL-8 防错、视觉参数定论、时间线四层归并、网页包契约、十步自验 |
| `docs/spec/requirements.md` | 定范围的人 | R1–R12 需求口径。**R11** 两层加工、**R12** 全量归网页 |
| `docs/spec/design.md` | 理解设计的人 | 为什么一张表、幂等主键、切片算法、清洗 C1–C13、§5.1 渲染层为何不是第三数据层 |
| `docs/spec/interfaces.md` | 并行开发者 | **唯一接口真源**：函数签名、§5.8 页面模块契约、§3 写者归属 |
| `docs/spec/data-sources.md` | 接新源的人 | 五源格式约定、设备注册规则、`device = ?` 禁令、分页里的设备呈现 |
| `docs/spec/tasks.md` | 查进度的人 | B0–B14 批次状态、18 个已修缺陷、环境变量、待决项 |
| `../../04-Areas/数字人生/AGENTS.md` | 读写 vault 的人 | 数据视角：路径分层、三层数据、§C-bis 网页包口径、D1–D11 防错 |
| `../../AGENTS.md` | 所有人 | 工作区通用卡点 CP-1…CP-6 与 S1–S59（本模块只引用不复制）|

**分工原则**：工程视角（本项目）讲「怎么改不闯祸」，数据视角（vault）讲「东西该放哪、是什么」；
设计细节只在 `docs/spec/design.md`，任何一处改动都要同步检查这两份 AGENTS 的相关断言是否失效。
