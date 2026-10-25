# 视觉通道提速与「盲总结」修复（2026-10-23）

> 本轮三件事：①查清视觉缺口 ②把视觉/日报/页面三条链路提速 ③给模块建 git。
> 本文只记**实测数字与归因**，改法与守则写入 `AGENTS.md` 的 DL-10/DL-11。

---

## 一、视觉缺口：不是"没跑完"，是"跑了但没看图"

用户问「还有哪些视觉总结没做完」。SQL 真值（`slice` / `slice_result`）：

| 设备 | 含图叶子片 | 已有 slice_result | 待做 |
|---|---|---|---|
| phone-2 | 5,940 | 5,940 | **0** |
| pc-main | 3,735 | 1,879 | **1,860** |

待做的 1,860 片分布在 **64 天**，全部在 2026-08（94）/ 09（530）/ 10（1,236）。

但更要紧的是**已有结果的质量**。把 `structured_json._meta` 摊开统计：

| 分类 | 条数 |
|---|---|
| reader=text（手机侧，用 ScreenMemo 自带画面描述） | 5,940 |
| **有图但一张都没送到模型**（images_total>0 且 images_sent=0） | **1,874** |
| 真的送了图的 | 1 |
| 无图 | 4 |

即 **PC 侧 1,879 条里有 1,874 条是「盲总结」**：视觉模型没看到任何画面，只凭 DB 时间线写。

### 根因（两处，缺一不成）

1. `vision._collect_images` 从 ManicTime zip 里**读到了**图片字节，并存成
   `images[i]["url"]`（data URL）—— 这一步是对的。
2. 但 `vision._chat` 发送时传的是 `images[i]["path"]`，即 zip 内的**相对成员名**
   `Screenshots/2026-08-14/xxx.webp`。`llm.chat_images` 对它做
   `Path(p).read_bytes()` → `FileNotFoundError` → 计 missing → 静默跳过 →
   `images_sent=0`。

### 为什么一直没被发现

两层计数器互相看不见：

- vision 层 `stats = {"total": 23, "missing": 0, ...}` —— 说"收集到 23 张，全 OK"
- llm 层 `skipped = {"missing": 23}` —— 说"23 张缺失"

日志只打 `reader=model`、`images_total=23`，**没有任何一处把 sent 与 total 并排打印**。
直到按 `_meta.images_sent==0` 分组统计才暴露。

---

## 二、提速：三条链路的真实瓶颈

| 链路 | 瓶颈 | 修法 | 实测 |
|---|---|---|---|
| 日报重建 | 串行（8 核用 1 核） | `--workers 4` 进程池 | 1.46 → **0.73 s/天**（2.0×）|
| 时段页重建 | 串行 | `--workers 6` 进程池 | 1.18 → **0.51 s/页**（2.3×）|
| 视觉 | 少数片被上游挂死 | 见下 | 见下 |

753 天全量：页面 **11.5 分钟**（原 15 分钟以上）、日报约 **9 分钟**。

### 视觉慢在哪：实测排除了两个假设

假设 A「图太大」—— **部分成立**。原图 1920×1080 = 2.07MP，一片 24 张 ≈ 50MP。
缩到长边 1280 后 ≈ 22MP。已实现 `_downscale`（PIL 软依赖）。

假设 B「图太多」—— **不成立，而且反了**。同一片 18 图实测：

| 送图张数 | 耗时 | 结果 |
|---|---|---|
| 16 | **6.2s** | 400 `sensitive image`（2/2 次复现）|
| 8 | 108.4s / 68.4s | 正常，8,275 / 5,718 字 |
| 4 | 107.4s | 正常，4,316 字，推理 **8,089** token |

**n=4 与 n=8 耗时相同（107s），且 n=4 的推理 token 更多。** 说明耗时由**推理量**决定，
与图片张数无关。张数只决定「会不会被上游判敏感」。

假设 C「max_tokens 设太大拖慢」—— 正在实测（`tmp/bench_mt3.log`）。

### 已确认的硬约束

- **≥16 张图会被上游判 `sensitive image`（400）**，重试无用。这是打顶到 8 的直接原因。
- `ReadTimeout` 在 `DL_LLM_TIMEOUT=180` 下仍在 190–200s 触发，说明有片确实会把上游拖到超时；
  上一轮 152 次 FAIL 里 150 次是 `LLMError`（多数 ReadTimeout）。

---

## 三、本次代码改动清单

| 文件 | 改动 |
|---|---|
| `digitallife/llm.py` | `chat_images` 接受 data URL（不再只认磁盘路径）|
| `digitallife/vision.py` | `_chat` 传 `url`；`_downscale` 缩图；`_sample_evenly` 抽 8 张；`image_count` 改用实际送出的张数 |
| `scripts/rebuild_daily.py` | `--workers` 进程池（默认 1）|
| `scripts/build_slice_html.py` | `--workers` 进程池（默认 1）|
| `scripts/batch_vision.py` | `--blind` 重做盲总结片 |
| `scripts/smoke.py` | T27a–e（图真的送到）、T28a–e（张数打顶），共 148 → **153** 项 |

### 冒烟新增的哨兵为什么必要

T27c 直接把「`_chat` 传 url 不传 path」钉住。按 S20 注入验证：
把 `urls = [i["url"]...]` 改回 `[i["path"]...]` → T27c 立刻变红；还原后复绿。
T27a 同理（去掉 `chat_images` 的 data URL 分支 → T27a 变红）。

---

## 四、教训

1. **两层计数器各自"正常"，合起来是错的**。跨层传递的中间结果必须有**端到端哨兵**，
   不能靠每层自报。这条已写成 DL-10。
2. **性能归因要看 token，不要看文件大小**。图片体积只是视觉 token 的代理指标，
   真正决定耗时的是模型生成的推理 token 数，而后者是随机的。
3. **上游的 400 也要分类**：`sensitive image` 是内容判定（不可重试），
   `MaxTokens invalid` 是参数越界（改参数即可），`ReadTimeout` 是容量/挂死（等下一轮）。
   原来全归成 `LLMError`，无法针对性处理。这条已写成 DL-11。
