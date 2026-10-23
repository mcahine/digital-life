# 模块接口契约（冻结）

> 本文件是并行开发的**唯一接口真源**。任何实现必须按此签名与数据形状落地；
> 需要变更接口时先改本文件并通知所有写者，不得各自发明（S53：同一文件同一时刻只有一个写者）。

## 0. 已实现（主代理所有，子代理只读，不得修改）

| 文件 | 提供 |
|---|---|
| `digitallife/paths.py` | `PACKAGE PROJECT WORKSPACE AREAS INBOX VAULT INDEX_DIR DATA_DIR DAILY_DIR SLICE_DIR GTD_VAULT DIARY_DIR REVIEW_DIR`；`work_dir() db_path() manifest_path() clean_report_path() exports_dir() ensure_dirs()`（`DL_WORK_DIR`/`DL_DB` 可覆盖）。**`INDEX_DIR` 在 vault 内（只放 manifest），`DATA_DIR = WORKSPACE/.data/digital-life` 放库/NDJSON/work** |
| `digitallife/devices.py` | `Device(device_id, kind, label, note)`；`DEVICES`（`pc-main`/`phone-1`）；`LEGACY_ALIASES`（旧 `pc`→`pc-main`、`phone`→`phone-1`）；`all_devices() get(id) kind_of(id) label_of(id) by_kind(kind) resolve_for_source(source) query_aliases(id) sql_device_filter(id)` |
| `digitallife/schema_check.py` | `Verdict(ok, issues)`；`validate_structured(obj)`；`reason(verdict)`——R1–R9 校验 screenmemo-v1 结构 |
| `digitallife/model.py` | `Event`（字段：device source kind start_utc_ms end_utc_ms app app_id title url path category payload；属性 `duration_s` `event_id` `dedupe_key()` `as_row()`）；`KINDS = ("app","web","screen","power","battery","notify","install")`；`SOURCES`；`DEVICES`（由 `devices.all_devices()` 派生，即 `pc-main`/`phone-1`）；`EVENT_COLUMNS` |
| `digitallife/tz.py` | `LOCAL_TZ`(UTC+8) `local_to_utc_ms(dt)` `utc_ms_to_local(ms)` `local_day(ms)->"YYYY-MM-DD"` `parse_local(s)` `parse_flexible(s)` `parse_hms_to_seconds(s)` |
| `digitallife/schema.py` | `SCHEMA_VERSION` `DDL` `init_db(con)`（已按 S30 先迁移后建索引） |
| `digitallife/store.py` | `connect(path=None)` `open_db(path=None)` `upsert_events(con, events, ingest_run)` `record_source_file(con, **kw)` `count_events(con, source=None)` `count_kind(con, source)` `time_span(con, source=None)` `events_between(con, lo_ms, hi_ms, device=None, kinds=None)` `upsert_capture_text(con, event_id, text, engine, model, now_ms)` `search_events(con, q, limit=20)` `rebuild_fts(con)` |
| `digitallife/clean.py` | `SourceReport`（`rows_read rows_kept dropped{} merged duplicated window_start_utc_ms window_end_utc_ms notes[]`；方法 `drop(reason,n=1) keep(n=1) as_dict()`）；`is_empty_event(ev)` `normalize_event(ev)` `merge_adjacent(events, gap_ms=120000, min_duration_s=30)`→`(list, merged, short_dropped)`；`build_report(reports)` |
| `digitallife/sources/base.py` | `SourceAdapter`（属性 `name`/`device`/`root`；抽象 `fingerprint()->list[Fingerprint]` / `iter_events(report)->Iterator[Event]`；`run()->(fps, events, report)`）；`Fingerprint(source_id,path,sha256,bytes,mtime,note)`；`sha256_file(p)` |
| `digitallife/sources/__init__.py` | `ADAPTERS: dict[str, type[SourceAdapter]]`（已 import 五个适配器模块，**适配器必须按下列类名实现**） |

## 1. 数据库表（schema.py 已建，可直接用）

`source_file / event / capture_text / slice / slice_result / daily_summary` + FTS5 `event_fts / capture_fts / slice_fts`。
新增列/表必须改 `schema.py` 的 `DDL` 并在 `MIGRATIONS` 登记（不要直接 exec ALTER）。

## 2. 待实现模块的**冻结签名**

### 2.1 `digitallife/sources/manictime.py`
```python
class ManicTimeSource(SourceAdapter):
    name = "manictime"; device = "pc-main"      # 注册 id，非裸 pc
    # root = WORKSPACE/00-Inbox 下的 ManicTimeBackup_2026-10-21.zip（在其 __init__ 里定位，
    #        允许环境变量 DL_MANICTIME_ZIP 覆盖；找不到时 fingerprint() 返回 [] 且 run() 报明确错误）
    def extract_db(self) -> tuple[Path, Path]      # 流式解包到 paths.work_dir()，返回 (reports_db, core_db)，已存在则复用
    def fingerprint(self) -> list[Fingerprint]     # zip 本身 + 两个 db（source_id 形如 "manictime:zip" / "manictime:reports"）
    def iter_events(self, report) -> Iterator[Event]
```
事件映射（Ar_Activity，`ReportId` 语义固定）：
- `ReportId=2` → kind=`power`，app=None，title=`Name`（Active / Away / Session lock）
- `ReportId=3` → kind=`app`，app=Ar_CommonGroup.Name，title=Ar_Activity.Name
- `ReportId=4` → kind=`web`（Name 以 http/https 开头）否则 kind=`app`（app=None，title=Name 当路径），url/path 相应填入
- `ReportId=1` → 跳过（Tags，仅 20 行）
- 截图：`Screenshots/YYYY-MM-DD/*.webp` → kind=`screen`，path=zip 内相对路径，start=文件名里的时间（`YYYY-MM-DD_HH-MM-SS_*`，本地时区），app 由同名时段的 app 事件就近归属（可用 ±120s 最近的 app 事件；无匹配则 None）
- 时间：用 `*UtcTime` 列（`tz.local_to_utc_ms` 只给无 UTC 列的源用）
- 必须调用 `clean.merge_adjacent`（C4）再产出；丢弃计入 report

### 2.2 `digitallife/sources/screenmemo.py`
```python
class ScreenMemoSource(SourceAdapter):
    name = "screenmemo"; device = "phone-1"    # 注册 id，非裸 phone
    def extract_db(self) -> Path                  # 解出主库 output/databases/screenshot_memo.db
    def shard_dbs(self) -> list[tuple[str, Path]] # [(app_pkg, path)]，从 output/databases/shards/**/**.db 解到 work_dir
    def fingerprint(self) -> list[Fingerprint]
    def iter_events(self, report) -> Iterator[Event]
```
- 每条 `shots_*` 行 → kind=`screen`，start=capture_time(ms)，path=file_path，app=app_registry 里的 app_name，payload 带 `{"ocr": bool}`
- 有 `ocr_text` 的行同时落 `capture_text`（engine=`screenmemo`，model=NULL）——由调用方写库，适配器只把文本放进 `Event.payload["ocr_text"]`
- `segments` → kind=`app`（**可选**：仅当 app_packages 非空时产出，title 用 app 名）
- `segment_samples` **不单独产出事件**（与 shots 重复，C6）；只用于给 shots 反查 segment_id，放进 payload
- **跳过**：`day_stats/totals/app_health_*/ai_*/search_docs/user_*/favorites/nsfw_*/mcp_*/app_stats/shard_registry/fts_registry/sqlite_sequence/android_metadata`（C7）

### 2.3 `digitallife/sources/iqqz3.py`
```python
class Iqqz3Source(SourceAdapter):
    name = "iqqz3"; device = "phone-1"         # 注册 id，非裸 phone
    def fingerprint(self) -> list[Fingerprint]   # 108 个日期目录（zip 仅校验 sha256，C1）
    def iter_events(self, report) -> Iterator[Event]
```
- `AUM_V4_Activity_*.csv` → kind=`app`（列 `App name,Date,Time,Duration`；Date 形如 `2025/1/4`，Duration 形如 `11:49:40` → `tz.parse_hms_to_seconds`）
- `AUM_V4_Usage_*.csv` → kind=`app`（`App name,Usage time,Access count`；无开始时间 → 用当天 00:00 + 序号错开，payload 标 `"granularity":"daily"`）
- `AUM_V4_Battery_*.csv` → kind=`battery`（title=Note）
- `AUM_V4_Notification_*.csv` → kind=`notify`（app=App name，payload={"count":N}，start=当天 00:00）
- `AUM_V4_Install_*.csv` → kind=`install`
- `AUM_V4_App_*.csv` → **不产事件**，产出 app_id→显示名映射（供 payload 归一，C11）
- `AUM_V4_Place_/CheckDevice_/DailyUsage_` → 跳过（无可用记录）

### 2.4 `digitallife/sources/tai.py`
```python
class TaiSource(SourceAdapter):
    name = "tai"; device = "pc-main"           # 注册 id
    def fingerprint(self) -> list[Fingerprint]
    def iter_events(self, report) -> Iterator[Event]
```
- 只导「时段」CSV（`时段,应用,描述,时长,分类`）：kind=`app`，app=`描述`，app_id=`应用`，duration_s=`时长`（秒）
- 网页 CSV（`时间,标题,网址,时长`）：kind=`web`，title=`标题`，url=`网址`，duration_s=`时长`（秒）
- 「每日」CSV 只用于交叉校验，不产事件（C8）
- 时间列格式 `12/31/2024 16:00:00` → `tz.parse_flexible`

### 2.5 `digitallife/sources/shion.py`
```python
class ShionSource(SourceAdapter):
    name = "shion"; device = "pc-main"         # 注册 id
    def extract_db(self) -> Path          # 只解新版 shion-2025-03-11.zip（C2）
    def fingerprint(self) -> list[Fingerprint]   # 两个 zip 都登记，旧版 note="superseded"
    def iter_events(self, report) -> Iterator[Event]
```
- `history` 表 → kind=`web`，title=title，url=url，start=`last_visited`(ms)，payload 带 domain 名
- `domain` 表 → app_id 归一（domain.name）
- `activity/moment/note/box/label/link/plan/program/remark/overview` 实测 **0 行**，跳过；若未来非空也要能容忍（用 `SELECT COUNT(*)` 判空后跳过）

### 2.6 `digitallife/dbview.py`
```python
def build_day_structure(con, date_key: str, device: str | None = None) -> dict
# 返回 {"date","apps":[{"app","app_id","seconds","sessions"}],
#       "timeline":[{"start":"HH:MM:SS","end":"HH:MM:SS","app","title","url"}],
#       "switch_count":int, "focus_blocks":[{"start","end","app","seconds"}],
#       "away":[{"start","end","reason"}], "screens":[{"event_id","path","ts"}],
#       "totals":{"active_s":int,"away_s":int,"locked_s":int}}
# 规则：away/locked 区间必须从「使用时长」中剔除（C13）；focus_blocks = 同一 app 连续 ≥15 分钟
def write_app_timeline(con, slice_id: str, structure: dict) -> None   # 写 slice_result.app_timeline_json（无 result 行时先建骨架）
```

### 2.7 `digitallife/slicer.py`
```python
WINDOW_MIN = 15; MAX_IMAGES = 24
def build_slices(con, device: str, day_key: str | None = None) -> list[dict]
# 全局 UTC 时间轴切 15 分钟窗（对齐到 :00/:15/:30/:45）；只对有事件的窗建片
# 片内截图数 > MAX_IMAGES 时必须二分（递归），子片 split_of = 父片 slice_id
# slice_id = sha1(f"{device}|{start_utc_ms}|{end_utc_ms}")[:16]
# 写 slice 表（status='pending'，source_basis='db'，event_count/image_count 实测填充）
# 返回 [{"slice_id","device","start_utc_ms","end_utc_ms","image_count","event_count","split_of"}]
def slice_events(con, slice_id: str) -> list[dict]   # 片内事件（供 vision 与 publish）
```

### 2.8 `digitallife/llm.py`
```python
class LLMError(RuntimeError): ...
@dataclass class ChatConfig: base_url: str; api_key: str; model: str; timeout: float = 180.0
def load_config() -> ChatConfig      # 环境 DL_LLM_BASE_URL/DL_LLM_API_KEY/DL_LLM_MODEL，
                                     # 回退 01-Projects/second-brain/config/llm.json；未配置返回空 base_url
def is_configured() -> bool
class ChatClient:
    def __init__(self, cfg: ChatConfig | None = None)
    def chat(self, messages: list[dict], max_tokens: int = 2048) -> dict      # {"content","model","usage"}
    def chat_images(self, text: str, image_paths: list[str], max_tokens: int = 4096) -> dict
      # 内联 image_url 部件（data URL）；单张 >3MB 先跳过并计数；总包 >40MB 从尾部丢图
      # 返回 {"content","model","usage","images_sent","images_dropped"}
# 硬约束：httpx.Client(trust_env=False)（S52）；非 200 抛 LLMError 带 status 与前 300 字
```

### 2.9 `digitallife/ocr.py`
```python
def available() -> bool                       # 探测 rapidocr + PIL，缺依赖返回 False（不抛）
def ocr_image(path: str | Path, max_side: int = 1280) -> str
# 用 PIL 先缩到 max_side 宽再 OCR；缺依赖抛 OcrUnavailable（明确指路 pip install -e .[ocr]）
class OcrUnavailable(RuntimeError): ...
```

### 2.10 `digitallife/prompts/segment.py`
```python
PROMPT_VERSION = "screenmemo-v1"
def build_prompt(slice_meta: dict) -> str
# slice_meta = {"start":"HH:MM:SS","end":"HH:MM:SS","images":[{"idx":1,"time":"HH:MM:SS","app":"..."}],
#               "db_timeline":[{"start","end","app","title"}]}
# 必须包含（smoke 会断言这些子串）：
#   "禁止使用OCR文本"  "## 关键操作"  "## 主要活动"  "## 重点内容"
#   "described_images"  "image_tags"  "image_descriptions"  "关键词："
#   以及本模块增补：「timeline[] 与 apps[] 的应用名与时间必须与随请求附带的 DB 结构一致」
```

### 2.11 `digitallife/vision.py`
```python
def enhance_slice(con, slice_id: str, client=None, reader: str = "model") -> dict
# reader="model"：截图直接送模型；reader="local"：先 ocr 落 capture_text，再用纯文本请求结构化；
#                reader="both"：先 ocr 落库，再把图连同 OCR 一起送模型
# 结果写 slice_result：overall_summary / structured_json / app_timeline_json（DB 基线）/
#                      conflict_count（模型 timeline 里 app 或时间与 DB 不一致的条数）/ raw_response
# 图片路径解析：path 是 zip 内相对路径时从 work_dir 解出；缺失图计入 payload 不中断
# 兜底：空内容重试上限 40 次（逐步放大 max_tokens）；结构打回重做 REWORK_ROUNDS=5 轮；
#      仍不合格**照常写库**，只记 structured_json._meta.schema_ok=False（不抛错、不写半截）
# 幂等选片：pending_slice_ids 用 NOT EXISTS(slice_result)，并按 devices.sql_device_filter 过滤
```

### 2.12 `digitallife/daily.py`
```python
def build_daily(con, date_key: str, client=None) -> dict
# 汇总当日：dbview.build_day_structure（pc+phone 各一份）+ 当日所有 slice_result.overall_summary
# **日层正文由模型写**（`_llm_narrative` + `DAILY_PROMPT`：把当天所有片层总结按时间序整篇喂模型，
# 要求自然段叙事、不用列表/时间轴、不省略、不编造）；模型不可用时才退回 `render_daily` 模板。
# 写 daily_summary（structured_json 记各设备时长与 narrative 原文）；**只产日层，不产周/月/季/年**
# 消费顺序：先 daily 后 publish——publish 只搬运库内 output_text，不会重新生成
#
# 2026-10-22 调整：日报正文**不含全量明细**（用户裁决 R12）——
#   应用节 = `### 应用（最突出的几个）` 一句话（前 6）+ 一行指路 10-时段/<date>/apps-<kind>.html
#   网站节 = `### 网站（最常去的）` 一句话（前 5）+ 一行指路 10-时段/<date>/web-<kind>.html
#   时间线 = `timeline_merge.merge_browser_web` 后按 span>=120s 压缩，整行 `[display_title](link_url)`
#   **已删除**：`### 应用明细（全量 N 个）` 大表、`### 网站访问（按域名汇总）` 表、`代表页面` 列
# 依赖：from .timeline_merge import display_title, merge_browser_web, span_seconds
#      （保留兼容别名 _merge_browser_web / _span_seconds，smoke T14 直接 import 它们）
```

### 2.13 `digitallife/publish.py`
```python
def publish_all(con, day_keys: list[str] | None = None, make_zip: bool = True) -> dict
# 写 VAULT/10-时段/YYYY-MM-DD/HHMM-HHMM.md（每片一篇，含 DB 时间线与 overall_summary）
# 写 VAULT/20-日报/YYYY-MM-DD.md（机器总结，带 frontmatter {"date":..., "source":"digital-life"}）
# 写 DATA_DIR/exports/digital-life-events.ndjson（全量 event 导出）
# 写 INDEX_DIR/manifest.json：{"generated_at","counts":{...},"sources":[sha256/区间],"files":[sha256]}
# make_zip=True 时打 DATA_DIR/exports/digital-life-<YYYYMMDD>.zip（只含 ndjson + manifest）
# 注意：manifest.json 写 INDEX_DIR（vault 内，供 /api/dl/stats），其余全部落 DATA_DIR
```

## 3. 写者归属（S53：禁止跨域写）

| 域 | 文件 | 写者 |
|---|---|---|
| 核心 | paths/model/tz/schema/store/clean/sources/base/sources/__init__ | **主代理**（子代理只读） |
| A | sources/{manictime,screenmemo,iqqz3,tai,shion}.py | 子代理 A |
| B | dbview.py slicer.py | 子代理 B |
| C | llm.py ocr.py prompts/segment.py vision.py | 子代理 C |
| D | daily.py publish.py | 子代理 D |
| E | docs/spec/*.md README.md scripts/smoke.py cli.py | **主代理** |
| F | devices.py schema_check.py timeline_merge.py scripts/{batch_vision,prune_results,rebuild_daily}.py | **主代理**（后续并入，无并行写者）|
| G | scripts/slice_pages/{base,contract}.py + build_slice_html.py | **主代理**（公共底座 + 编排器）|
| H | scripts/slice_pages/{apps,web,timeline,vision}_*.py | 页面作者（3 子代理并行，各写 4 页，互不越界）|

## 4. 编码约束（全子代理适用）

- Python 3.11+，`from __future__ import annotations`，只用标准库 + 已声明依赖；**不得新增第三方依赖**。
- **不得写 vault 与 00-Inbox 之外的任何路径**；解包只允许 `paths.work_dir()`（`DL_WORK_DIR` 可覆盖）。
- 不得修改 `../../` 之外的仓库文件（尤其 second-brain / stacksearch / GTD vault）。
- 所有时间写 UTC ms；本地展示才转 `tz.utc_ms_to_local`。
- 幂等：同一源重跑两次，`event` 行数不得变化（`Event.event_id` 已保证，不要自己另造主键）。
- 明确报错优于静默降级：找不到源文件、缺依赖、DSN 未配置 → 抛异常并写清"怎么修"。
- 每个模块必须可被 `python -c "import digitallife.<mod>"` 单独导入（不产生副作用、不建目录）。

---

## 5. 实施中被采纳的偏差与补充（2026-10-21，主代理裁决）

> 这些是并行实现期间暴露的真实接口缺口，已实测采纳。后续改动以此为准。

### 5.1 时间字段形状必须统一（域 B ↔ 域 D 的真实冲突）

`slicer.slice_events` 原始返回 `start_utc_ms/end_utc_ms`，而 `dbview` 的 `timeline` 返回已格式化的
`start/end`（"HH:MM:SS"）。域 D 初版直接消费导致切片表时间列为空。
**约定**：任何"对外暴露的时间线"一律提供 `start/end`（HH:MM:SS，本地时区）**并同时保留**
`start_utc_ms/end_utc_ms`；消费方必须容忍两种形状（域 D 已加 `_norm_timeline`）。

### 5.2 切片文件名的 Windows 约束

契约写的 `HHMM-HHMM.md` 若渲染成 `09:00-10:00.md` 会因 Windows 禁止冒号而
`OSError: [Errno 22]`。**约定**：stem 只用无冒号写法（`0900-1000`）。
同一天 pc 与 phone 落在同一时段时，撞名才加 `-pc`/`-phone` 后缀；不撞名保持无后缀。

### 5.3 日报形态（2026-10-22 已被叙事化取代）

原契约要求 `## 电脑使用` / `## 手机使用` **恒出两节**。现行为：

- **模型可用时**（正常路径）：`render_narrative` 产出 `# <date> 数字人生日报` + `## 今天` + 模型写的自然段，
  末尾附一行「数据规模：…；完整时间线见时段网页」。**没有固定的电脑/手机两节**——
  跨设备内容由模型在叙事里自行交代。
- **模型不可用时**（降级路径）：退回 `render_daily` 模板，仍按设备分段。

不变量保留：设备标签用 `devices.name_of()`，直接显示真实设备名（`D-001` / `iQQQZ3`），
多台时显示「主力电脑 / 手机 1」这类具体名字（见 data-sources.md §一）。

### 5.4 dbview 的 away 列表合并两类

`away` 列表同时装 away 与 locked，用 `reason` 字段区分（`'away'` / `'locked'`）；
`totals` 仍按契约分 `active_s / away_s / locked_s` 三项，且三者互不重叠（重叠部分按 locked 计）。

### 5.5 纯 power 窗不建片

"只对有事件的窗建片"细化为"只对含**实质事件**（app/web/screen 等）的窗建片"。
实测：一个只有 Away 的日子若按"有任何事件"建片，会摊出 44 个内容为空的片。
已固化为 smoke T10 断言。

### 5.6 二分只在能真正降图数时进行

若某窗内截图时间戳完全相同（同一毫秒），二分不减少任何一片的图数；
此时保留父片为叶子（`image_count` 允许超 `MAX_IMAGES`），而不是生成同内容的重复子孙片
（实测无护栏会产出 20 个重复分层片）。上游按时间戳唯一性产事件时不会触发。

### 5.6b 设备 id 与设备过滤（2026-10-22 增补，跨域契约）

`device` 不再是裸 `pc`/`phone`，而是注册 id（`pc-main`/`phone-1`）：

- 写入侧：适配器 `device` 属性填注册 id，`SourceAdapter.__init__` 经 `devices.get()` 归一。
- **读取侧（硬约束）**：任何按设备过滤的 SQL 必须用 `devices.sql_device_filter(id)`，
  它返回 `("device IN (?,?)", [新id, 旧别名])`。写 `device = ?` 会在历史行上静默返回空
  （本轮 9 处同缺陷：daily×4 / slicer×3 / vision×1 / publish×2）。
- 展示侧：`devices.name_of(id)`；未登记时退回 `device_id`。**不要退回「电脑/手机」**。

### 5.6c 落盘契约（vault vs `.data/`）

| 产物 | 路径 | 归属 |
|---|---|---|
| `10-时段/<日期>.html`（一天一页）、`10-时段/片层/<日期>/*.md`、`20-日报/*.md` | `paths.SLICE_DIR`（+ `paths.LAYERED_DIR`）/ `paths.DAILY_DIR` | 进 git，人工可见 |
| `manifest.json` | `paths.INDEX_DIR`（vault 内） | 进 git（`!` 单文件放行），`/api/dl/stats` 依赖 |
| 事件库、NDJSON、work | `paths.DATA_DIR`（`知识库/.data/digital-life/`） | 不进 git，可重建 |

**禁止**在任何代码里硬编码这些路径——一律走 `paths.*`，否则用户搬移数据区时会静默失效（S50）。

### 5.7 S58 教训（写测试的一方也要被测试）

smoke 的 T7 初版**没有判别力**：app 事件 09:00-11:00 与 Away 03:00-09:00 不重叠，
注入"关掉 C13 剔除"缺陷后仍全绿。已改为 app 横跨 Away（app 04:00-12:00 / Away 03:00-09:00，
期望 active=3h），并加判别哨兵 T7d。注入缺陷实测变红（active=28800 vs 期望 10800）。
**规则**：任何"剔除/过滤/合并"类断言，必须构造出**能触发该规则的数据形态**，并在交付前注入缺陷验红。

### 5.8 时段网页包契约（2026-10-22 新增）

用户裁决：**日报只留突出部分，全量明细归时段网页**。网页包不是「另一个数据源」，
而是 `event / slice / slice_result` 的**只读渲染层**——不落中间 JSON，删目录重跑即重建。

#### 文件与模块划分（S53 单写者）

| 路径 | 职责 | 写者 |
|---|---|---|
| `digitallife/timeline_merge.py` | 四层 timeline 归并（网页/路径孪生/同区间同义/同页回声） | 主代理 |
| `scripts/slice_pages/base.py` | 设计 token + `page/esc/link/seg/more_button/card/fmt_dur/write` | 主代理 |
| `scripts/slice_pages/contract.py` | `Ctx` / `load_slices` / `day_range_ms` / `device_label` | 主代理 |
| `scripts/build_slice_html.py` | 编排：装 Ctx、逐 section 渲染、设备 tab、目录锚点 | 主代理 |
| `scripts/slice_pages/apps.py` | 应用 section | 页面作者 |
| `scripts/slice_pages/web.py` | 网站访问 section | 页面作者 |
| `scripts/slice_pages/timeline.py` | 时间线 section | 页面作者 |
| `scripts/slice_pages/vision.py` | 视觉总结 section | 页面作者 |

#### section 模块契约（新增 section 必须遵守）

```python
ANCHOR = "apps"             # 锚点 id（目录浮钮跳转）
LABEL  = "应用"             # section 标题
def panel(pid, ctx: Ctx, *, hidden: bool = False):
    """返回 (HTML 片段, 行数)。

    pid 是该 section 内的面板 id（形如 apps-pc），用于 tab 的 aria-controls 与
    「展开全部」按钮的 data-for。hidden=True 表示该面板初始不可见。
    """
```

- `vision` 是**跨设备** section，签名不同：`panel(ctx) -> (html, 总片数, 已生成片数)`。
- 编排器统一决定 section 顺序与目录锚点；**页面外壳（header / 右下浮钮 / 主题脚本）只在 `base.py`**。
- **某设备在该 section 0 行时不要生成这个 tab** —— 由编排器过滤，模块只管如实返回行数。
- **不要自己写 CSS**：`base.CSS` 已定义 `.section/.section-head/.seg/.panel/.tw/table/.num/.ttl/ul.sub/.sites/.site/.visit/.more/.card/.empty/.dock/.jump/.fab/.bar`。

#### Ctx 与 slice 形状（实测可用）

```python
@dataclass
class Ctx:
    date_key: str
    con: sqlite3.Connection
    devices: list[str]        # 当日有数据的设备：pc-main / phone-1
    structures: dict          # dev -> dbview.build_day_structure(...)
    slices: dict              # dev -> load_slices(...)
    device: str               # 本页设备；跨设备页为 ""
    # ctx.structure / ctx.dev_slices / ctx.dev_label / ctx.has_data / ctx.all_slices()

# load_slices 每项：
# {slice_id, start_utc_ms, end_utc_ms, start:"HH:MM:SS", end, image_count, event_count,
#  summary, structured(原始 JSON 文本), model, conflict_count, present}
```

#### timeline_merge 输出契约

```python
rows = merge_browser_web(structure["timeline"])
# 每行：{start, end, app, title, url, link_url, sub:[...], web_count,
#        path_twin?: str, alt_of?: [str]}
```

| 字段 | 语义 | 展示层怎么用 |
|---|---|---|
| `link_url` | 挂在该行上的第一条网页地址 | 有值 → **整行**做成链接，锚文本 `display_title(row)` |
| `sub` | 其余网页事件 | 有空 → 缩进子行（`└ 标题`）；**空则不得产子行** |
| `path_twin` | 被折掉的本地路径副本 | 只作证据，不渲染成独立行 |
| `alt_of` | 被折掉的同义表述 | 同上 |

**硬约束**：同一 `(start, end)` 在输出里**只能出现一次**（用户 2026-10-22 反复强调）。
实测：2026-08-13 原始 910 条 → 534 行、重复区间 **0**、300 行带链接、26 行有子行。
改 `timeline_merge.py` 会同时影响日报与四个页面，**先跑 `smoke.py` 再跑两个样本日**。
