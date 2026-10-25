# 额度与限流：能拿到什么、拿不到什么、怎么兜（2026-10-23）

> 用户问：能不能读到 5 小时限额和周限额？
> **答：都读不到。** 本文记录实测证据，以及在上游不暴露额度时怎么用探针兜底。

---

## 一、结论先行

| 想知道的 | 能否拿到 | 依据 |
|---|---|---|
| 5 小时额度剩余 | ❌ 拿不到 | 响应头无 `x-ratelimit-*`；无额度查询端点 |
| 周额度剩余 | ❌ 拿不到 | 同上 |
| 每分钟限流（rpm）是否触顶 | ✅ 能间接判断 | 429 正文 `{"message":"rpm exhausted","type":"quota_exceeded_error"}` |
| 当前是否还能发请求 | ✅ 能 | 极小探针请求的成功/失败 |

---

## 二、实测证据（三条，都不成立）

### 1. 响应头没有任何额度字段

打一次真实请求，把**全部**返回头打印出来：

```text
content-type: application/json
content-length: 78
connection: keep-alive
date: Fri, 23 Oct 2026 13:03:12 GMT
access-control-allow-origin: *
access-control-expose-headers: Content-Disposition,Content-Transfer-Encoding,Content-Type
x-request-id: 27f60d4fc2bf46e981118404e5ed8bae
```

**没有 `x-ratelimit-remaining` / `x-ratelimit-reset` / `x-quota-*` 任何一个。**
注意 `access-control-expose-headers` 只暴露了三个与文件传输有关的头，
说明上游**设计上就没打算**把额度信息给客户端。

### 2. 没有额度查询端点

| 端点 | 结果 |
|---|---|
| `GET /v1/usage` | 404 |
| `GET /v1/quota` | 404 |
| `GET /v1/rate_limit` | 404 |
| `GET /v1/me` | 404 |
| `GET /v1/account/usage` | 404 |
| `GET /v1/models` | **200**（对照组：证明路径是对的，不是全站 404）|

### 3. 桥接层同样是空值

`dsh-bridge` 的 `bridge_status` 里四个通道的 `quota` 全为：

```json
{"remaining": null, "total": null, "knownAccounts": 0, "accounts": 1}
```

`remaining` 与 `total` 都是 `null`，说明桥接层也没从任何地方拿到额度。

---

## 三、容易混淆的一点：`quota_exceeded_error` 不是额度耗尽

现在实际收到的 429 正文是：

```json
{"error":{"message":"rpm exhausted","type":"quota_exceeded_error","code":"8"}}
```

`type` 里带 `quota` 字样，但 `message` 明确是 **rpm exhausted** —— 这是
**每分钟请求数**打满，不是 5 小时 / 周额度耗尽。两者处置完全不同：

| 类型 | 特征 | 正确处置 | 错误处置的后果 |
|---|---|---|---|
| rpm 限流 | `message: rpm exhausted` | 跨 60s 窗口退避，继续跑 | 停下整批 → 白白浪费几小时 |
| 额度耗尽 | 无 rpm 字样，常见 `quota`/`insufficient`/`balance` | **停下等刷新** | 硬跑 → 一直 429，全条超时，墙钟全废 |

**按 `type` 字段判断会判错**（它把两种都叫 `quota_exceeded_error`）。
必须按 `message` 内容分类，见 `scripts/batch_vision.py` 的 `_classify()`。

---

## 四、兜底方案：探针看门狗

因为额度不可读，只能**间接探**。已实现于 `scripts/batch_vision.py`：

```
每轮开始 → probe_quota()（8 token 的极小请求）
  成功 → 继续跑
  失败且判定 rpm  → 等 90s 再探一次，成功就继续（不停整批）
  失败且判定额度  → 每 10 分钟探一次，最多等 6.5 小时
                     （6.5h > 一个 5 小时窗，够跨过一个刷新点）
                    恢复 → 继续；超时 → 干净收工
```

**收工时不会丢进度**：每片成功即 `commit`，选片靠
`NOT EXISTS(slice_result)` 幂等，下次重跑自动从断点继续。

参数：

| 参数 | 默认 | 含义 |
|---|---|---|
| `--pause-min` | 10 | 额度耗尽后每多少分钟探一次 |
| `--max-wait-h` | 6.5 | 最多等多少小时（覆盖一个 5 小时窗） |
| `--no-watchdog` | 关 | 关闭看门狗 |

守则见 `AGENTS.md` DL-13，哨兵见冒烟 **T31a–f**。

---

## 五、为什么是 6.5 小时

用户提到「5 小时额度」。看门狗的等待预算取 **6.5 小时**，理由：

- 如果额度窗口是滚动的 5 小时，最长等待就是 5 小时；6.5 留了 30% 余量给探针间隔与抖动。
- 如果实际是**每日/每周**额度（真耗尽了要等更久），6.5 小时等不到，此时看门狗会
  干净退出并打印「已完成的都已提交，可随时续跑」——**不会卡死，也不会假装成功**。

**注意：以上「5 小时」「周额度」是用户口径，上游没有任何接口能证实其真实周期。**
本文只记录能观测到的事实，不推断窗口长度。
