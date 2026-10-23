"""片（slice）级增强 —— 把 DB 时间线与截图合成一段结构化记录。

三种 reader 模式对应三种"视觉肌肉"的信任程度：
  model —— 截图直接送模型（保真最高，但要求网关开着）
  local —— 先本地 OCR 落 capture_text，再用纯文本请求结构化（不需要视觉模型，但会丢画面）
  both  —— 先 OCR 落库，再把图连同 OCR 一起送模型（模型以图为准、OCR 只作校对）

冲突口径：模型返回的 timeline 条目与 DB 基线（app_timeline_json）对不上就计一条。
conflict_count 是**可观测指标，不是失败判据** —— 设计文档 §5 明确以 DB 为准，不因冲突报错。

截图取图两条路（ManicTime 的截图**从不落盘**，只存 zip 内相对路径）：
  1. work_dir 里已解出的那份（历史行为，保留）
  2. 按 event.path 直接从 ManicTime 备份 zip 读单个 member（主流路径；不整体解包 5.5 万张 webp）

视觉通道实测事实（sensenova，2026-10）：
  - thinking 必须**显式** {enabled: false}；不传时推理 token 吃光 max_tokens，content 返回空串。
  - 图像部件只接受 {"type":"image_url","image_url":{"url":"data:image/webp;base64,..."}}；
    {"type":"image_base64",...} 被拒 HTTP 400 invalid content。
  - 单张真实截图约 11.7s。
"""
from __future__ import annotations

import base64
import io
import json
import os
import re
import time
import zipfile
from pathlib import Path

import httpx

from . import paths, store
from .prompts.segment import PROMPT_VERSION, build_prompt
from .schema_check import validate_structured
from .tz import utc_ms_to_local

#: 起始预算 8192，空内容时逐级放大到 MAX_TOKENS（65536 是上游硬上限）。
#: 为什么不直接给 65536（2026-10-23 实测，同一片 8 图，交替各跑 2 次）：
#:   max_tokens=65536 → 269.9s / 217.0s，推理 token 23,439 / 20,568
#:   max_tokens=8192  → 189.9s /  71.4s，推理 token  1,749 /  1,383
#: 预算越大，模型越倾向于展开长篇推理（上限成了"邀请"），耗时随之翻 3–4 倍。
#: 小预算下输出并没有变短（8,888 / 10,182 字 vs 8,751 / 6,865 字），信息量不减。
#: 给足上限只在"推理暴涨把预算吃光导致空内容"时才需要 —— 那时重试会把预算翻倍。
MAX_TOKENS = int(os.environ.get("DL_START_TOKENS") or 8192)
MAX_TOKENS_CEILING = 65536   # 空内容重试可放大到此；上游硬上限，不得越过
REWORK_ROUNDS = 5           # 结构不合格时最多打回重做 5 次（共 6 次机会）

MAX_IMAGE_BYTES = 3 * 1024 * 1024      # 单张内联上限（按 data URL 长度算）
MAX_TOTAL_BYTES = 40 * 1024 * 1024     # 整包上限
_JSON_FENCE = re.compile(r"^\s*``@(?:json)?\s*|\s*``@\s*$", re.IGNORECASE)
_HMS = re.compile(r"(\d{1,2}):(\d{2})(?::(\d{2}))?")

_MANICTIME_ZIP_ENV = "DL_MANICTIME_ZIP"
_MANICTIME_ZIP_NAME = "ManicTimeBackup_2026-10-21.zip"
_REPAIR_INSTRUCTION = (
    "你上一次的回复不是合法 JSON，无法解析。请只输出那一个 JSON 对象："
    "不要 Markdown 代码围栏、不要任何解释、前言或结语。字段与格式与上一条要求完全一致。"
)
_JSON_TAIL = (
    "## 六、输出纪律（本次请求的补充约束）\n"
    "只输出一个 JSON 对象：不要 ```json 围栏、不要任何解释文字。"
    "overall_summary 的值必须含且只含「## 关键操作」「## 主要活动」「## 重点内容」三个二级标题，"
    "每个标题下至少 3 条以 - 开头的列表项。"
    "请**直接给出 JSON**，不要展开长篇推理。"
)


_NO_IMAGE_NOTE = (
    "## 六、本片没有任何截图（重要）\n"
    "本次请求 **没有附带任何图片**。因此 described_images、image_tags、image_descriptions "
    "三个字段一律输出空数组 []，不要尝试描述不存在的画面。\n"
    "其余字段全部依据上面的 DB 时间线填写。请**直接输出 JSON**，不要展开长篇推理。"
)


class VisionError(RuntimeError):
    """片级增强的结构性失败（片不存在、通道未配置、模型返回空等）。"""


# ── 通用小工具 ──────────────────────────────────────────────────────────────

def _hms(ms: int) -> str:
    return utc_ms_to_local(ms).strftime("%H:%M:%S")


def _hms_to_s(text) -> int | None:
    m = _HMS.search(str(text or ""))
    if not m:
        return None
    h, mi, s = int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)
    return h * 3600 + mi * 60 + s


def _norm_app(name) -> str:
    return re.sub(r"\s+", "", str(name or "")).lower()


def _load_slice(con, slice_id: str) -> dict:
    row = con.execute("SELECT * FROM slice WHERE slice_id=?", (slice_id,)).fetchone()
    if not row:
        raise VisionError(
            "slice 不存在：%s。请先跑 slicer.build_slices 生成片（片是 15 分钟窗的派生物）。" % slice_id
        )
    return dict(row)


def _slice_events(con, slice_id: str, meta: dict) -> list[dict]:
    """优先用 slicer.slice_events（B 域契约）；只有模块不存在时才回退到事件表直查。

    回退**只吞 ImportError/AttributeError**：slicer 在时它的异常（例如未知 slice_id）
    必须原样抛出，否则回退会把「契约不一致」伪装成「跑通了」。
    """
    try:
        from .slicer import slice_events  # 延迟导入：B 域模块可能尚未落地
    except (ImportError, AttributeError):
        rows = store.events_between(con, meta["start_utc_ms"], meta["end_utc_ms"],
                                    device=meta.get("device"))
        return [dict(r) for r in rows]
    return [dict(r) for r in slice_events(con, slice_id)]


def _db_timeline(events: list[dict]) -> list[dict]:
    out = []
    for ev in events:
        if ev.get("kind") not in ("app", "web"):
            continue
        start = ev.get("start_utc_ms")
        end = ev.get("end_utc_ms") or start
        if start is None:
            continue
        out.append({
            "start": _hms(start),
            "end": _hms(end),
            "app": ev.get("app") or ev.get("title") or "",
            "title": ev.get("title") or "",
        })
    out.sort(key=lambda x: x["start"])
    return out


def has_result(con, slice_id: str) -> bool:
    """幂等判据：已有 slice_result 行即视为处理过（slice.status 全库无人回写，不能用它）。"""
    return con.execute("SELECT 1 FROM slice_result WHERE slice_id=?", (slice_id,)).fetchone() is not None


def pending_slice_ids(con, device: str | None = None, limit: int = 10) -> list[str]:
    """待处理片：event_count>0 且尚无 slice_result。按开始时间升序取前 limit 个。"""
    sql = ("SELECT s.slice_id FROM slice s WHERE s.event_count > 0 "
           "AND NOT EXISTS (SELECT 1 FROM slice_result r WHERE r.slice_id = s.slice_id)")
    args: list = []
    if device:
        # 设备在库里可能是旧值（pc/phone）或注册 id（pc-main/phone-1），必须都认，
        # 否则改造后这里一片都选不出来，视觉识别会静默停滞。
        from . import devices as _dv
        _clause, _al = _dv.sql_device_filter(device, "s.device")
        sql += " AND " + _clause
        args.extend(_al)
    sql += " ORDER BY s.start_utc_ms LIMIT ?"
    args.append(int(limit))
    return [r[0] for r in con.execute(sql, args)]


# ── 截图取图：work_dir 已解出 → ManicTime zip 直读 ─────────────────────────

def _manictime_zip() -> Path | None:
    override = (os.environ.get(_MANICTIME_ZIP_ENV) or "").strip()
    cands = [Path(override)] if override else []
    cands.append(paths.INBOX / _MANICTIME_ZIP_NAME)
    for c in cands:
        if c.is_file():
            return c
    return None


_ZIP_CACHE: dict[str, zipfile.ZipFile] = {}


def _zip_handle(zip_path: Path) -> zipfile.ZipFile:
    key = str(zip_path)
    zf = _ZIP_CACHE.get(key)
    if zf is None:
        zf = zipfile.ZipFile(zip_path)
        _ZIP_CACHE[key] = zf
    return zf


def close_zip_cache() -> None:
    for zf in _ZIP_CACHE.values():
        try:
            zf.close()
        except OSError:
            pass
    _ZIP_CACHE.clear()


def _resolve_image(ev: dict) -> Path | None:
    """兼容旧行为：path 已在磁盘上（绝对路径，或 work_dir 里解出来的那份）。"""
    raw = (ev.get("path") or "").strip()
    if not raw:
        return None
    direct = Path(raw)
    if direct.is_absolute() and direct.is_file():
        return direct
    work = paths.work_dir()
    clean = raw.lstrip("/\\")
    for cand in (work / clean, work / "manictime" / clean, work / "manictime" / Path(raw).name):
        if cand.is_file():
            return cand
    name = Path(raw).name
    if name:
        for cand in sorted(work.glob("**/" + name)):
            if cand.is_file():
                return cand
    return None


#: 送模型前把截图缩到长边这么多像素。2,073,600px（1920×1080）的原图在视觉 token 上
#: 约等于 1.6k token/张；一片 24 张就是 39k token，模型光"看图"就要几十秒。
#: 缩到 1280 长边后像素降到 ~0.9MP，实测同一片 24 图 90s 级 → 30s 级。
#: 1280 仍足以看清窗口标题、按钮文字与页面版式——视觉通道要的是「在做什么」，
#: 不是 OCR 抄字（OCR 另有 local reader）。可用 DL_IMAGE_MAX_SIDE 覆盖，0 = 不缩。
IMAGE_MAX_SIDE = int(os.environ.get("DL_IMAGE_MAX_SIDE") or 1280)
IMAGE_QUALITY = int(os.environ.get("DL_IMAGE_QUALITY") or 72)

#: 每片最多送这么多张图（均匀抽样，保留首尾）。实测依据（2026-10-23，同一片 18 图）：
#:   n=16 → 上游 400 "sensitive image"（6.2s 硬失败，2/2 次复现）
#:   n=8  → 108.4s / 68.4s，均正常出 8275 / 5718 字
#:   n=4  → 107.4s，推理 8089 token（比 n=8 还多）
#: 结论：**耗时由推理 token 决定，不由图片张数决定**（n=4 与 n=8 同为 107s）。
#: 但张数决定「会不会被上游判敏感」和视觉 token 的基座，所以取 8 打顶：
#: 15 分钟一片的画面变化有限，8 张均匀抽样足以支撑「在做什么」，DB 时间线仍是骨架。
#: 24 张全送既慢又会撞 400，收益为负。
MAX_SEND_IMAGES = int(os.environ.get("DL_MAX_SEND_IMAGES") or 8)


def _sample_evenly(items: list, n: int) -> tuple[list, int]:
    """均匀抽样 n 个，保留第一个和最后一个。返回 (抽样结果, 丢掉的数量)。"""
    if n <= 0 or len(items) <= n:
        return items, 0
    if n == 1:
        return [items[0]], len(items) - 1
    step = (len(items) - 1) / float(n - 1)
    picked = [items[int(round(i * step))] for i in range(n)]
    seen: set[int] = set()
    uniq: list = []
    for it in picked:
        k = id(it)
        if k not in seen:
            seen.add(k)
            uniq.append(it)
    return uniq, len(items) - len(uniq)


def _downscale(raw: bytes) -> bytes:
    """把截图缩到长边 IMAGE_MAX_SIDE 并重编码 WEBP。PIL 缺失时原样返回（不阻断）。

    PIL 在 [ocr] extra 里；视觉主路径不需要它也能跑，所以这里做软依赖。
    """
    if IMAGE_MAX_SIDE <= 0:
        return raw
    try:
        from PIL import Image
    except ImportError:
        return raw
    try:
        im = Image.open(io.BytesIO(raw))
        w, h = im.size
        if max(w, h) <= IMAGE_MAX_SIDE:
            return raw
        im = im.convert("RGB")
        scale = IMAGE_MAX_SIDE / float(max(w, h))
        im = im.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, "WEBP", quality=IMAGE_QUALITY, method=4)
        out = buf.getvalue()
        return out if out else raw
    except Exception:  # noqa: BLE001 —— 解码失败就发原图，缩图不能成为新的失败点
        return raw


def _read_image(ev: dict) -> bytes | None:
    """截图字节：先看磁盘，再从 ManicTime zip 直读 member（截图从不落盘，这是主流路径）。"""
    raw = (ev.get("path") or "").strip()
    if not raw:
        return None
    disk = _resolve_image(ev)
    if disk is not None:
        try:
            return disk.read_bytes()
        except OSError:
            return None
    zip_path = _manictime_zip()
    if zip_path is None:
        return None
    member = raw.replace("\\", "/").lstrip("/")
    try:
        return _zip_handle(zip_path).read(member)
    except (KeyError, OSError, RuntimeError, zipfile.BadZipFile):
        return None


def _sniff_mime(raw: bytes) -> str:
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "image/webp"
    if raw.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if raw.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if raw[:4] == b"GIF8":
        return "image/gif"
    return "image/webp"


def _collect_descriptions(events: list[dict]) -> list[dict]:
    """取出**已由上游 App 做过视觉描述**的文字原料（ScreenMemo 的 ai_image_meta.description）。

    为什么走这条路（用户 2026-10-22）：手机截图是安卓内部路径，本机一份都没有，
    送不进模型；而 ScreenMemo 自己已经给 99% 的截图写好了中文画面描述，
    存在 payload["description"] 里。用它当观察来源，**同样不需要图片**。

    与 prompts/segment.py 那条「禁止使用 OCR 文本」不冲突：那条禁的是**原始 OCR**
    （状态栏、按钮字，模型会把它复述成「行为」）。这里是上游 LLM 已经综合过的
    画面叙述（「图片1展示了起点读书APP的书架界面…」），层级等同于「看见了什么」。
    """
    out: list[dict] = []
    shots = [e for e in events if e.get("kind") == "screen"]
    for ev in sorted(shots, key=lambda e: e.get("start_utc_ms") or 0):
        raw = ev.get("payload")
        if isinstance(raw, dict):
            payload = raw            # _slice_events 已解析过，直接给的就是 dict
        else:
            try:
                payload = json.loads(raw or "{}")
            except (TypeError, ValueError):
                payload = {}
        desc = str(payload.get("description") or "").strip()
        if not desc:
            continue
        out.append({
            "idx": len(out) + 1,
            "time": _hms(ev.get("start_utc_ms") or 0),
            "app": ev.get("app") or "",
            "description": desc,
            "tags": payload.get("tags") or [],
            "event_id": ev.get("event_id") or "",
        })
    return out

def _collect_images(events: list[dict], meta: dict) -> tuple[list[dict], dict]:
    """挑出可送的截图并读字节；缺失/超限都计数，绝不中断整片。"""
    shots = [e for e in events if e.get("kind") == "screen"]
    ordered = sorted(shots, key=lambda e: e.get("start_utc_ms") or 0)
    images: list[dict] = []
    stats = {"total": len(shots), "missing": 0, "oversize": 0, "total_limit": 0,
             "missing_paths": [], "oversize_paths": []}
    sent_idx = 0
    for ev in ordered:
        raw = _read_image(ev)
        if not raw:
            stats["missing"] += 1
            stats["missing_paths"].append(ev.get("path") or ev.get("event_id") or "?")
            continue
        raw = _downscale(raw)
        url = "data:" + _sniff_mime(raw) + ";base64," + base64.b64encode(raw).decode("ascii")
        if len(url) > MAX_IMAGE_BYTES:
            stats["oversize"] += 1
            stats["oversize_paths"].append(ev.get("path") or "?")
            continue
        sent_idx += 1
        images.append({
            "idx": sent_idx,
            "src_idx": ordered.index(ev) + 1,
            "time": _hms(ev.get("start_utc_ms") or meta["start_utc_ms"]),
            "app": ev.get("app") or "",
            "url": url,
            "size": len(url),
            "event_id": ev.get("event_id") or "",
            "path": ev.get("path") or "",
        })
    total = sum(i["size"] for i in images)
    while len(images) > 1 and total > MAX_TOTAL_BYTES:
        dropped = images.pop()          # 从尾部丢，保留最早的画面
        total -= dropped["size"]
        stats["total_limit"] += 1
    return images, stats


# ── 模型通道 ────────────────────────────────────────────────────────────────

def _content_text(content) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        out = []
        for part in content:
            if isinstance(part, dict):
                if isinstance(part.get("text"), str):
                    out.append(part["text"])
            elif isinstance(part, str):
                out.append(part)
        return "".join(out)
    return str(content)


def _default_client():
    """统一走 llm.ChatClient（A 方案：删掉自带的 _SenseClient，消除重复实现）。

    历史：并行开发时 vision.py 曾自带一份最小直连客户端 _SenseClient，与 llm.py 的
    ChatClient 逻辑重复（S47 多真源）。现已删除，视觉通道只认 llm.ChatClient ——
    参数（thinking / max_tokens / image_url / 空内容重试）全部由 llm.py 单点持有。
    """
    from .llm import ChatClient

    return ChatClient()


def _usage_note(resp: dict) -> str:
    usage = resp.get("usage") or {}
    det = usage.get("completion_tokens_details") or {}
    return "finish_reason=%s, reasoning_tokens=%s, max_tokens=%s" % (
        resp.get("finish_reason"), det.get("reasoning_tokens"), MAX_TOKENS)


def _default_client():
    from .llm import ChatClient

    return ChatClient()


def _chat(client, prompt: str, images: list[dict], max_tokens: int = MAX_TOKENS) -> dict:
    """统一发送：只走 llm.ChatClient（有图用 chat_images，无图用 chat）。

    图片传**已编码的 data URL**（i["url"]），不是 i["path"]。
    path 是 zip 内的相对成员名（`Screenshots/.../x.webp`），本机没有这个文件；
    曾经把它传下去让 chat_images 自己读，结果每张都 FileNotFoundError → images_sent=0，
    视觉总结变成"盲总结"（2026-10-23 实测 1,874 条）。
    """
    if not images:
        return client.chat([{"role": "user", "content": prompt}], max_tokens=max_tokens)
    urls = [i["url"] for i in images if i.get("url")]
    return client.chat_images(prompt, urls, max_tokens=max_tokens)


# ── JSON 解析 + 一次修复重试 ────────────────────────────────────────────────

def _strip_fence(text: str) -> str:
    t = str(text or "").strip()
    t = _JSON_FENCE.sub("", t)
    if t and not t.startswith("{"):
        lo, hi = t.find("{"), t.rfind("}")
        if lo != -1 and hi > lo:
            t = t[lo:hi + 1]
    return t.strip()


def _parse_structured(text: str) -> tuple[dict, str | None]:
    raw = _strip_fence(text)
    try:
        obj = json.loads(raw)
    except ValueError as exc:
        return {}, "structured_json 解析失败：%s（前 200 字：%s）" % (exc, raw[:200])
    if not isinstance(obj, dict):
        return {}, "模型返回的不是 JSON 对象（是 %s）" % type(obj).__name__
    return obj, None


def _conflicts(structured: dict, baseline: list[dict]) -> tuple[int, list[dict]]:
    """模型 timeline 与 DB 基线对不上的条数（应用名 + 时间双维度）。"""
    model_tl = structured.get("timeline") or []
    if not isinstance(model_tl, list):
        return 0, []
    db_by_app: dict[str, list[tuple[int, int]]] = {}
    for item in baseline:
        s = _hms_to_s(item.get("start"))
        e = _hms_to_s(item.get("end"))
        if s is None:
            continue
        db_by_app.setdefault(_norm_app(item.get("app")), []).append((s, e if e is not None else s))
    if not db_by_app:
        return 0, []
    bad: list[dict] = []
    for item in model_tl:
        if not isinstance(item, dict):
            continue
        app = _norm_app(item.get("app"))
        # 模型按提示词返回的 timeline 条目用 time 键（{time,app,action,summary}，ScreenMemo 原约定），
        # 不是 start。只读 start 会让每条都被判"缺时间"，conflict_count 恒等于 timeline 条数，
        # 指标失去判别力（实测：与 DB 完全一致的样例也被计 1 条）。故 start 优先、time 兜底。
        start = _hms_to_s(item.get("start")) if item.get("start") is not None else _hms_to_s(item.get("time"))
        reason = None
        if not app or app not in db_by_app:
            reason = "应用不在 DB 基线"
        elif start is None:
            reason = "缺少可解析的开始时间"
        else:
            spans = db_by_app[app]
            # 与任一同名 DB 段起点相差 ≤120s、或落在该段区间内，即视为一致
            if not any(abs(start - s) <= 120 or (s <= start <= e + 120) for s, e in spans):
                reason = "时间与同名 DB 段不重叠"
        if reason:
            bad.append({"app": item.get("app"), "start": item.get("start"), "reason": reason})
    return len(bad), bad


def _summary_check(text: str) -> dict:
    heads = ["## 关键操作", "## 主要活动", "## 重点内容"]
    found = {h: (h in (text or "")) for h in heads}
    others = [ln.strip() for ln in (text or "").splitlines()
              if ln.strip().startswith("## ") and ln.strip() not in heads]
    return {"sections": found, "extra_heads": others,
            "all_present": all(found.values()), "exactly_three": not others}


def _write_result(con, slice_id: str, *, model: str, overall: str, structured: dict,
                  baseline: list[dict], conflicts: int, raw: str) -> None:
    """一次性写入（先算好再写，失败不留半截）。structured 为空对象时 structured_json 落 NULL。"""
    structured_json = json.dumps(structured, ensure_ascii=False) if structured else None
    con.execute(
        "INSERT INTO slice_result(slice_id, model, overall_summary, structured_json, "
        "app_timeline_json, conflict_count, raw_response, created_at) "
        "VALUES(?,?,?,?,?,?,?,?) "
        "ON CONFLICT(slice_id) DO UPDATE SET model=excluded.model, "
        "overall_summary=excluded.overall_summary, structured_json=excluded.structured_json, "
        "app_timeline_json=excluded.app_timeline_json, conflict_count=excluded.conflict_count, "
        "raw_response=excluded.raw_response, created_at=excluded.created_at",
        (slice_id, model, overall, structured_json,
         json.dumps(baseline, ensure_ascii=False), int(conflicts), raw, int(time.time() * 1000)),
    )
    con.commit()


def _ocr_fill(con, images: list[dict], now_ms: int) -> int:
    """reader=local/both：对能取到磁盘文件的截图跑 OCR 并落 capture_text；返回失败数。

    截图只存在 zip 内时没有磁盘文件，OCR 无法进行 —— 计入失败数但不中断整片。
    """
    from . import ocr as ocr_mod

    if not ocr_mod.available():
        raise ocr_mod.OcrUnavailable(
            "reader=local/both 需要本地 OCR，但当前环境缺依赖。安装：" + ocr_mod.INSTALL_HINT
        )
    failed = 0
    for item in images:
        text = ""
        try:
            disk = _resolve_image({"path": item["path"]})
            if disk is None:
                failed += 1
            else:
                text = ocr_mod.ocr_image(disk)
        except Exception:
            failed += 1
        if text:
            store.upsert_capture_text(con, item["event_id"], text, engine=ocr_mod.OCR_ENGINE,
                                      model=None, now_ms=now_ms)
        item["ocr"] = text
    return failed


# ── 主流程 ──────────────────────────────────────────────────────────────────

def enhance_slice(con, slice_id: str, client=None, reader: str = "model",
                  force: bool = False) -> dict:
    """把一个片补全成结构化记录，写 slice_result 并返回同名字段的 dict。

    force=False 时，已有 slice_result 的片直接跳过（幂等；slice.status 全库无人回写，不能用它）。
    """
    if reader not in ("model", "local", "both", "text"):
        raise VisionError("reader 只能是 model / local / both / text，收到：%r" % (reader,))
    meta = _load_slice(con, slice_id)
    if not force and has_result(con, slice_id):
        return {"slice_id": slice_id, "skipped": True, "reason": "已有 slice_result"}

    events = _slice_events(con, slice_id, meta)
    baseline = _db_timeline(events)
    images, stats = _collect_images(events, meta)
    described = _collect_descriptions(events)
    now_ms = int(time.time() * 1000)

    # 图片张数打顶：实测 16 张起会被上游判 sensitive image（400 硬失败），
    # 而耗时由推理 token 决定、与张数无关（n=4 与 n=8 同为 ~107s）。
    # 所以抽样纯粹是为了「不撞 400 + 省视觉 token」，不是为了让单条变快。
    # 抽样后必须**重编 idx**：提示词里的「图 N」与 R8 覆盖校验都以 idx 为准，
    # 不重编会让模型按 1..18 作答而提示词只列了 8 个。
    sampled, dropped_by_cap = _sample_evenly(images, MAX_SEND_IMAGES)
    if dropped_by_cap:
        for new_idx, it in enumerate(sampled, 1):
            it["idx"] = new_idx
        stats["sampled_out"] = dropped_by_cap
    images = sampled

    slice_meta = {
        "start": _hms(meta["start_utc_ms"]),
        "end": _hms(meta["end_utc_ms"]),
        "images": [{"idx": i["idx"], "time": i["time"], "app": i["app"]} for i in images],
        "db_timeline": baseline,
    }
    prompt = build_prompt(slice_meta)
    ocr_failed = 0
    if reader in ("local", "both") and images:
        ocr_failed = _ocr_fill(con, images, now_ms)
        body = ["### 图 %s（%s，%s）\n%s" % (i["idx"], i["time"], i["app"] or "未知应用",
                                            i.get("ocr") or "（无文字）") for i in images]
        if reader == "local":
            # 纯文本模式没有图，"禁止使用OCR文本" 那条会自相矛盾，必须改写成等价约束：
            # OCR 文本是唯一观察来源，但仍不许把按钮／菜单文字复述成"行为"。
            prompt = (prompt
                      + "\n\n## 七、本次为纯文本模式（无图）\n"
                        "下面给的是 OCR 文本，它是本次唯一的观察来源。你只能依据这些文本推断；"
                        "即便如此，也不要把按钮／菜单文字复述成行为，要归纳成「做了什么」。\n"
                        "各图 OCR 文本：\n" + "\n".join(body))

    if reader == "text" and described:
        # 上游 App 已给出的画面描述 = 本次唯一的观察来源。
        # 与 local(OCR) 的区别：这份文本已经是「看见了什么」的叙述，不是界面字串，
        # 所以只需提醒「按它归纳行为」，不必加「不要复述按钮文字」那条。
        rows = []
        for d in described:
            tag = ("｜标签：" + "、".join(d["tags"])) if d.get("tags") else ""
            rows.append("### 画面 %s（%s，%s）%s\n%s"
                        % (d["idx"], d["time"], d["app"] or "未知应用", tag, d["description"]))
        prompt = (prompt
                  + "\n\n## 七、本次为纯文本模式（无图）\n"
                    "下面每一段都是**上游 App 已经对截图做过的画面描述**，是本次唯一的观察来源。\n"
                    "按这些描述归纳「做了什么」，不要逐条复述，也不要把描述里的界面元素当成行为。\n"
                    "各画面的描述：\n" + "\n\n".join(rows))

    if client is None:
        client = _default_client()

    send_images = images if reader == "model" else []
    if send_images:
        full_prompt = prompt + "\n\n" + _JSON_TAIL
    else:
        # 无图时原始提示词会让模型对「请直接理解图片」展开长篇推理，实测 4096 个推理 token
        # 全被吃光、content 返回空串（finish_reason=length）；加这段限定后同一片稳定收敛。
        full_prompt = prompt + "\n\n" + _NO_IMAGE_NOTE
    resp = _chat(client, full_prompt, send_images, MAX_TOKENS)
    images_sent = int(resp.get("images_sent", len(send_images)))
    images_dropped = int(resp.get("images_dropped",
                                  stats["missing"] + stats["oversize"] + stats["total_limit"]))
    # 实测：sensenova-6.8-flash-lite 的推理量是**随机**的 —— 同一请求同一张图，两次分别只烧
    # 565 / 1131 个推理 token；偶尔一路烧到 max_tokens 上限，content 返回空串
    # （finish_reason=length）。空内容不代表「本片没内容」，重试可覆盖绝大多数情况；
    # 三次都空才判本片失败，且不写任何半截结果。
    # 用户策略：**没有内容一定要重试出来**。空内容重试上限提到很大（避免死循环），
    # 实际最常见的空内容原因是 max_tokens 被推理吃光，重试时逐步放大预算。
    _tries = 0
    while not str(resp.get("content") or "").strip() and _tries < 40:
        _tries += 1
        # 起始 8192，每轮翻倍，封顶 65536 —— 小预算够用时就不必为大预算付推理时间的账。
        _budget = min(MAX_TOKENS_CEILING, MAX_TOKENS * (2 ** (_tries - 1)))
        try:
            resp = _chat(client, full_prompt, send_images, _budget)
        except Exception:  # noqa: BLE001 —— 网络抖动也继续重试
            time.sleep(min(30, 2 * _tries))
            continue
    if not str(resp.get("content") or "").strip():
        raise VisionError(
            "模型连续 6 次返回空内容（%s；slice=%s）——推理 token 吃光 max_tokens 的已知抖动，"
            "本片不写结果，稍后重跑即可" % (_usage_note(resp), slice_id)
        )
    content = str(resp.get("content") or "")
    model_name = str(resp.get("model") or "")
    structured, parse_error = _parse_structured(content)
    repaired = False
    if parse_error:
        # 一次修复重试：原文 + 「请只输出 JSON」再问一次
        repaired = True
        repair_prompt = (prompt + "\n\n## 八、修复请求\n" + _REPAIR_INSTRUCTION
                         + "\n\n你上一次的回复原文如下：\n<<<\n" + content[:8000] + "\n>>>")
        try:
            resp2 = _chat(client, repair_prompt, send_images, MAX_TOKENS)
            content2 = str(resp2.get("content") or "")
            structured, parse_error = _parse_structured(content2)
            if content2.strip():
                content = content + "\n\n/* ===== 修复重试 ===== */\n\n" + content2
                model_name = str(resp2.get("model") or model_name)
        except Exception as exc:  # 修复失败不丢片：退化为「原文进 overall_summary」
            parse_error = "%s；修复重试失败：%s" % (parse_error, exc)

    # ── 结构化校验 + 打回重做（用户要求：结构不合理就打回重做，不许写半截/糊弄） ──
    # 规则见 digitallife/schema_check.py：必备字段、类型、三节标题与顺序、每节≥3 条要点、
    # 应用不得编造 DB 里没有的、有图时必须覆盖全部图片序号。
    # 不合格时把**具体的违规理由**回灌给模型重做；最多 REWORK_ROUNDS 轮，仍不合格则不写库并报错。
    db_apps = {(it.get("app") or "").strip() for it in baseline if (it.get("app") or "").strip()}
    # image_count 必须等于**这次真的送出去（并在提示词里列过）**的张数。
    # 旧写法用 stats["total"]（当日全部截图，含缺失/超限/被抽样掉的），
    # 于是 R8 要求覆盖 18 张而提示词只列了 24 个已送项里的一部分 —— 天生不可满足。
    verdict = validate_structured(
        structured,
        image_count=(len(described) if reader == "text" else len(send_images)),
        db_apps=db_apps,
        require_images=bool(send_images),
    )
    rework_rounds = 0
    while not verdict.ok and rework_rounds < REWORK_ROUNDS and content.strip():
        rework_rounds += 1
        head = "\n\n## 九、结构打回重做（第 %d 次）\n" % rework_rounds
        tail = "\n\n上次输出原文：\n<<<\n" + content[:8000] + "\n>>>\n\n"
        rework_prompt = (prompt + head
                         + "你上一次的输出**结构不合格**，必须按下列问题逐条修正后重新输出完整 JSON：\n"
                         + verdict.reason() + tail
                         + "只输出修正后的完整 JSON 对象，不要解释，不要只给补丁。")
        try:
            resp3 = _chat(client, rework_prompt, send_images, MAX_TOKENS)
        except Exception as exc:  # noqa: BLE001 —— 重做失败保留上一版，交给最终判定
            structured.setdefault("_meta", {})["rework_error"] = str(exc)[:200]
            break
        content3 = str(resp3.get("content") or "")
        if not content3.strip():
            continue
        st3, err3 = _parse_structured(content3)
        content = content + "\n\n/* ===== 结构打回重做 %d ===== */\n\n" % rework_rounds + content3
        model_name = str(resp3.get("model") or model_name)
        if st3:
            structured, parse_error = st3, err3
            verdict = validate_structured(
                structured, image_count=stats["total"], db_apps=db_apps,
                require_images=bool(send_images),
            )

    if not verdict.ok:
        # 用户策略（2026-10-21）：**有内容就入库，格式不合格不阻断**。
        # 打回重做只是尽力修正；改不好也照样写库，把问题记进 _meta 供后续筛。
        # 理由：宁可有一条格式不完美但内容完整的记录，也不要整片留空。
        structured.setdefault("_meta", {})["schema_ok"] = False
        structured["_meta"]["schema_issues"] = [i.as_dict() for i in verdict.errors[:8]]
    else:
        structured.setdefault("_meta", {})["schema_ok"] = True

    conflict_count, conflict_items = _conflicts(structured, baseline)
    summary_check = _summary_check(str(structured.get("overall_summary") or ""))
    if structured:
        overall = str(structured.get("overall_summary") or "").strip()
    else:
        # 解析不出结构化内容时，把模型原文留进 overall_summary，structured_json 置空，
        # 不整片丢弃 —— 内容至少可检索、可人工回看。
        overall = content.strip()[:8000]
        structured = {}

    if structured:
        structured.setdefault("_meta", {}).update({
            "missing_images": stats["missing_paths"],
            "oversize_images": stats["oversize_paths"],
            "total_limit_dropped": stats["total_limit"],
            "ocr_failed": ocr_failed,
            "reader": reader,
            "images_total": stats["total"],
            "images_sent": images_sent,
            "images_dropped": images_dropped,
            "prompt_version": PROMPT_VERSION,
            "summary_check": summary_check,
            "schema_check": verdict.as_dict(),
            "rework_rounds": rework_rounds,
            "parse_error": parse_error,
            "repaired": repaired,
            "conflicts": conflict_items,
        })

    _write_result(con, slice_id, model=model_name, overall=overall, structured=structured,
                  baseline=baseline, conflicts=conflict_count, raw=content)

    return {
        "slice_id": slice_id,
        "skipped": False,
        "reader": reader,
        "model": model_name,
        "overall_summary": overall,
        "structured": structured,
        "app_timeline": baseline,
        "conflict_count": conflict_count,
        "conflicts": conflict_items,
        "images_total": stats["total"],
        "images_sent": images_sent,
        "images_missing": stats["missing"],
        "images_oversize": stats["oversize"],
        "images_total_limit": stats["total_limit"],
        "images_dropped": images_dropped,
        "ocr_failed": ocr_failed,
        "summary_check": summary_check,
        "schema_check": verdict.as_dict(),
        "rework_rounds": rework_rounds,
        "parse_error": parse_error,
        "repaired": repaired,
        "raw_response": content,
    }
