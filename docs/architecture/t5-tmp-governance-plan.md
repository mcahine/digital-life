# T5 tmp 治理方案（只出方案，**未执行**）

> 本轮采集日期：2026-10-25（本地 UTC+8）
> 采集命令：`cd 01-Projects/digital-life && export PYTHONUTF8=1 && python tmp/t5_recon.py`
> **本文件只是方案。执行需用户批准**（用户明确要求：T5 只出方案，`.bak` 全保留或另行归档）。

## 一、真值（本轮实测，非估计）

| 项 | 值 |
|---|---|
| `tmp/` 文件数（递归） | **4,636** |
| 合计体积 | **505.9 MB** |
| `.bak` 数量 | **16** |

按前缀分布（前 12）：

| 前缀 | 数量 | 性质 |
|---|---|---|
| `*.json` | 3,574 | 片段缓存（`tmp/.pagecache/*.json`）+ 各次测量输出 |
| `*.py` | 219 | 一次性探针脚本 |
| `a0_` | 116 | 上一手主代理的探针 |
| `a2_` | 68 | 子代理2 的探针 |
| `*.log` | 53 | 运行日志 |
| `a1_` | 48 | 子代理1 的探针 |
| `probe_` | 42 | 更早的探针 |
| `b1_` `b2_` | 38 + 30 | **本轮的探针与验证脚本** |
| `fix_` `patch_` | 31 + 25 | 历史修补脚本 |
| `bench_` | 20 | 基准脚本 |

## 二、16 个 `.bak` 清单（**一个都不许删**，S41）

```text
a0_daily_inject.bak     56,583 B      a1_daily.py.bak        47,759 B
a0_lt.bak               11,048 B      a1_publish.py.bak      21,021 B
a0_s58.bak              55,380 B      b1_hb.bak              14,684 B
a0_web.bak               7,147 B      b1_vs.bak              14,175 B
b2_daily.py.bak         55,655 B      daily.bak              28,120 B
daily2.bak              30,311 B      daily3.bak             32,447 B
devices.bak              3,787 B      devices2.bak            5,059 B
devices3.bak             5,059 B      iqqz3.bak              15,369 B
```

**为什么一个都不能删**：上一手实证过「9 个中 7 个在 git 全历史里查不到对应 blob」
（`git hash-object --no-filters` + `git rev-list --all` 反查）。删即永久消失。
本轮新增的 `b1_hb.bak` / `b1_vs.bak` / `b2_daily.py.bak` 对应源码都还在工作区，
**但按 S41 仍不删，交用户裁决**。

## 三、三级分流（执行时才动）

| 级 | 范围 | 动作 | 判据 |
|---|---|---|---|
| **L1 保留** | 16 个 `.bak`、`tmp/t4_baseline.json`、`tmp/baseline_b1b2.json`、各 `freeze_*.json` | **原地不动** | 它们是还原点与对拍基线 |
| **L2 归档** | `tmp/a0_*` `a1_*` `a2_*` `a3_*` `a4_*`（上一手+子代理的探针与报告） | 移到 `tmp/_archive/20261025/` | 有历史价值但不再运行 |
| **L3 可清** | `tmp/.pagecache/*.json`（3,574 个，纯派生缓存）、`__pycache__` | 删（可重建） | 删后跑一次 `build_slice_html.py --date X` 能重建 |

> ⚠️ **L3 的 `.pagecache` 删了会变慢但不会错**——它是渲染缓存，键含 `_RENDER_VERSION`。
> 但注意 S66：删缓存会让下一次全量重建慢约 40%（实测单日 0.5s → 0.7s 量级）。

## 四、按日期分目录（用户原话「不要堆放」）

目标：`tmp/20261025/` 而不是 4,636 个平铺。
**但这会改掉所有 `tmp/xxx.py` 的字面路径**，涉及：

- `docs/` 里 13 处 `tmp/a0_*.py` 引用（交接文档自检脚本）；
- `docs/architecture/*.md` 里 8 处「可重放命令」；
- 数据侧 `AGENTS.md` §B 采集命令 2 处。

→ 所以「分目录」是**与 N6 同级的重构**，必须和 N6 一起做、不能单独做。

## 五、结论与建议

1. **本轮不执行任何删除或移动**（用户明确要求「执行需批准」）；
2. L3 的 `.pagecache` 是最安全的一档（纯派生、可重建、占比最大 3,574/4,636 = 77%）；
3. L2 归档建议与 N6 合并成一轮做，因为两者都要改文档路径；
4. **`.bak` 永远保留**，除非用户明确说「归档到工作区外」。

## 六、可重放采集脚本

```bash
cd 01-Projects/digital-life && export PYTHONUTF8=1
python tmp/t5_recon.py      # 输出本文件 §一/§二 的全部数字
```