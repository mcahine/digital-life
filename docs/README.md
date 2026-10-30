# docs/ 文档索引

> **读者**：要找某件事的答案、或判断某份文档还能不能信的人。
> **最后核实：2026-10-25**（本表每一行的「新鲜度」都是当天逐篇打开核对后填的，不是从文件名猜的）。
> **维护约定**：`docs/` 下新增/删除任何 `.md`，必须同步改本表；否则本表即失效。

## 这棵树管什么

`docs/` 只放**本模块（`01-Projects/digital-life`）的过程与契约文档**。
工程规则在上一级 `AGENTS.md`，数据口径在 `04-Areas/数字人生/AGENTS.md`，两边不重复。

```text
docs/
├── README.md              ← 本文件（索引，唯一入口）
├── WORKFLOW.md            数据怎么流动（五阶段总览）
├── HANDOFF.md            ★★ 交接文档（唯一入口）
├── HANDOFF.md             2026-10-23 那次交接的历史快照（内含失效链接）
├── phone-vision-runbook.md      phone-2 视觉长跑的运行手册
├── phone-vision-report-template.md  长跑收尾报告的填空模板
├── architecture/          ★ 分层 / 测试策略 / 故障定位（子代理3 产出中，3 篇）
├── postmortem/            事故复盘（按事件一篇，永久留痕）
└── spec/                  契约（签名/数据源/需求/任务 + 一致性对账）
```

## 一张表：哪份文档回答什么问题

> 「新鲜度」= 最后核实日期 + 当前是否与代码/数据一致。
> 🔴 = **已确认过期或含错误断言**，用前必须先看该行的「该改什么」。

| 文档 | 回答什么问题 | 读者 | 新鲜度（最后核实日期） |
|---|---|---|---|
| [README.md](README.md) | 文档在哪、哪份还能信 | 所有人（入口） | 2026-10-25 · 本文件 |
| [WORKFLOW.md](WORKFLOW.md) | 数据从源文件到 vault 的五个阶段、每步输入输出与判定标准 | 要改管线的人 | 🟡 2026-10-25 核实：五阶段描述仍成立；**但第 5 行写「§C 十三条 DL」已过期（现为 16 条）**，应改为「见 AGENTS.md §C」不写条数 |
| [phone-vision-runbook.md](phone-vision-runbook.md) | phone-2 那 5,940 片怎么跑、闸门是什么、性能口径多少 | 要重跑 phone-2 视觉的人 | 🟢 2026-10-25 核实：5,940/5,940 与库内一致；命令可跑 |
| [phone-vision-report-template.md](phone-vision-report-template.md) | 长跑收尾报告长什么样、要填哪些数 | 写收尾报告的人 | 🟢 2026-10-25 核实：纯模板，不含时效性断言（`{{...}}` 占位符）。依赖 `tmp/monitor.py` 产物，该脚本不在版本控制内 |
| [architecture/layering.md](architecture/layering.md) | 分层现状、边界泄漏在哪、模块该怎么拆 | 做架构改造的人 | 🟡 2026-10-25 新建（子代理3），**仍在产出中**；§3「拆分方案」里的目标文件**尚未创建**，读时按「计划中」对待 |
| [architecture/testing.md](architecture/testing.md) | 现有测试形态的问题（一大脚本 187 断言）、26 个模块零覆盖、怎么补 | 要补测试的人 | 🟡 2026-10-25 新建（子代理3），仍在产出中；文中「187 条断言」与我实测一致 |
| [architecture/troubleshooting.md](architecture/troubleshooting.md) | 线上现象已暴露时怎么定位根因（先查库→再看文件→最后看代码） | 排查故障的人 | 🟡 2026-10-25 新建（子代理3），仍在产出中 |
| [architecture/tmp-cleanup.md](architecture/tmp-cleanup.md) | `tmp/` 里 4 千多个文件怎么分级治理、哪些能删哪些不能 | 要清理 `tmp/` 的人 | 🟡 2026-10-25 新建（子代理3）。**只出方案不动手**（S41），执行须用户批准 |
| [spec/requirements.md](spec/requirements.md) | 这个模块必须满足哪些需求（R1–R12） | 所有人（需求基线） | 🟢 2026-10-25 核实：R1–R12 与实现一致；R12 的「一天一个 HTML」已落地 |
| [spec/design.md](spec/design.md) | 为什么是一张表+单一时间轴、清洗规则 C1–C13 的依据 | 要理解取舍的人 | 🟡 2026-10-25 核实：C1–C13 依据仍成立；**§1.2 末尾写「`.data/` 由 `*.data` 规则排除」已过期**（现行是显式 `.data/` 规则）。该改：更新该句 |
| [spec/interfaces.md](spec/interfaces.md) | 冻结签名、表结构、模块契约、实施偏差 | 改代码 / 加模块的人 | 🟢 2026-10-25 已就地修订：§0 设备清单、§5.3 日报形态重写；逐条对账见 [spec/CONSISTENCY.md](spec/CONSISTENCY.md) |
| [spec/data-sources.md](spec/data-sources.md) | 五个源各自的格式陷阱、设备归属规则、落盘分层 | 接新源 / 排数据问题的人 | 🟢 2026-10-25 已就地修订：删除硬编码设备清单与条数（S45）、更正 `device_label` 与日报设备名描述 |
| [spec/tasks.md](spec/tasks.md) | 分批台账、已修缺陷、交付实测、待决 | 想知道「做到哪了」的人 | 🟢 2026-10-25 已就地修订：交付实测全表重测、待决表逐条复核 |
| [spec/CONSISTENCY.md](spec/CONSISTENCY.md) | 每条契约在代码里的落点、一致与否、判决 | 要和文档对账的人 | 2026-10-25 · 本文件（新建） |
| [postmortem/README.md](postmortem/README.md) | 复盘目录本身怎么读、每篇一句结论 | 想快速定位事故的人 | 🟢 2026-10-25 核实：目录表与各篇标题一致 |
| [postmortem/2026-10-23-delivery.md](postmortem/2026-10-23-delivery.md) | 2026-10-23 那轮交付的最终数字与结论 | 找历史基线的人 | 🟢 2026-10-25 核实：事件 1,124,831 / 片 43,257 / 总结 9,679 **与当前库一致** |
| [postmortem/413-degenerate-image-2026-10-23.md](postmortem/413-degenerate-image-2026-10-23.md) | 413 与体积无关、真因是畸形截图 | 调视觉通道的人 | 🟢 2026-10-25 核实：`_is_degenerate` 已在 `vision.py`，阈值 12/64 与文一致 |
| [postmortem/quota-and-ratelimit-2026-10-23.md](postmortem/quota-and-ratelimit-2026-10-23.md) | 额度读不到、429 要读 `message` 不能读 `type` | 调视觉通道的人 | 🟢 2026-10-25 核实：`batch_vision._classify` 与文一致。文中「T31a–f」在 `postmortem/README.md` 记作「T31a–h」，以后者为准 |
| [postmortem/vision-perf-and-blind-fix-2026-10-23.md](postmortem/vision-perf-and-blind-fix-2026-10-23.md) | 盲总结（图片没送到模型却报成功）怎么发现与修复 | 调视觉通道 / 写哨兵的人 | 🟢 2026-10-25 核实：现象与 DL-10 一致 |
| [postmortem/perf-regression-2026-10-23.md](postmortem/perf-regression-2026-10-23.md) | 循环里查库导致单日 1.6s 退化到 103s | 改渲染/查询的人 | 🟢 2026-10-25 核实：与 DL-9 一致 |
| [postmortem/diary-style-2026-10-24.md](postmortem/diary-style-2026-10-24.md) | 片层清单体 vs 日层日记体的真因与改法 | 改提示词 / 改日报的人 | 🟢 2026-10-25 核实：`schema_check` 已无 R5b，与文一致 |
| [postmortem/incremental-and-no-store-2026-10-24.md](postmortem/incremental-and-no-store-2026-10-24.md) | 日报不入库、按日期增量重建 | 改发布链路的人 | 🟢 2026-10-25 核实：`daily_summary.output_text` 实测全空，与「不入库」一致 |

## 按问题找文档

| 我想知道… | 去这里 |
|---|---|
| 改代码怎么才不闯祸 | [../AGENTS.md](../AGENTS.md) §C（DL-N 铁律） |
| 数据现在有多少、什么口径 | [../../../04-Areas/数字人生/AGENTS.md](../../../04-Areas/数字人生/AGENTS.md) §A/§B |
| 日报**应该**长什么样 | [../../../04-Areas/数字人生/AGENTS.md](../../../04-Areas/数字人生/AGENTS.md) §D「日报结构契约」 |
| 某个函数签名 / 表结构 | [spec/interfaces.md](spec/interfaces.md) |
| 某个源格式的坑 | [spec/data-sources.md](spec/data-sources.md) |
| 这个模块当初为什么要这样设计 | [spec/design.md](spec/design.md) |
| 某个事故的根因 | [postmortem/](postmortem/README.md) |
| 文档和代码对不上怎么办 | [spec/CONSISTENCY.md](spec/CONSISTENCY.md)（含判决规则） |
| 整体分层与边界问题 | [architecture/layering.md](architecture/layering.md)（子代理3 产出中） |

## 死链自检（可执行）

```bash
cd 01-Projects/digital-life
python tmp/a4_linkcheck.py          # 扫描 docs/ 与两份 AGENTS.md 里的相对路径，报告不存在者
```

> 本轮已修：`HANDOFF.md` 指向的 `FINAL-2026-10-23.md` 不存在（该文件从未产出），
> 已在死链报告中标红，替代物为 [postmortem/2026-10-23-delivery.md](postmortem/2026-10-23-delivery.md)。
