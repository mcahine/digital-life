"""模型通道 —— 默认**直连** OpenAI 兼容上游（SenseNova），备用 dsh-model-bridge 网关。

两种模式按 base_url 自动判别（无需额外开关）：
  direct  —— base_url 是上游 /chat/completions（默认 https://token.sensenova.cn/v1/chat/completions）
  gateway —— base_url 命中 dsh-model-bridge（含 /dsh-bridge-gw/v1，或以 /chat 结尾）：
             POST {base_url}  body = {"channelId": <qoder-cn|qoder-global|workbuddy-intl|workbuddy-cn>,
                                      "body": <OpenAI 兼容 chat body>}
             响应是**上游 SSE 原样透传**，错误是普通 JSON {ok:false,error}。

直连的三个实测事实（照此实现，不要再试错）：
  1. thinking 是**必填 object**（默认 {enabled: true}）。不显式传 {enabled: false} 时，
     推理 token 会吃光 max_tokens，choices[0].message.content 返回空串而 message.reasoning 非空。
  2. 图片只能 {"type":"image_url","image_url":{"url":"data:image/webp;base64,<b64>"}}；
     {"type":"image_base64",...} 会被上游拒绝（HTTP 400 invalid content）。
  3. 响应正文在 choices[0].message.content；思考在 message.reasoning；
     usage.completion_tokens_details.reasoning_tokens 是判空归因的关键字段。
  4. **{enabled:false} 不等于关掉思考**（本机 5 次成对实验：false→1/5 空、true→3/5 空；
     reasoning_tokens 仍然 86~1024）。换成 {"type":"disabled"} 或加 reasoning_effort:"none"
     也没有更干净。上游对 thinking 的形状有校验（传字符串→400 invalid arguments），
     但不尊重 enabled=false。因此 chat() 默认带**一次自救重试**（放大 max_tokens 重发）。

S52：httpx.Client(trust_env=False) —— 本机系统代理（Clash 等 WinINET 代理）会把请求送进代理，
返回一个伪 502，把「上游不可达」误报成「上游宕机」；两种情况都必须显式关掉环境代理信任。
"""
from __future__ import annotations

import base64
import json
import mimetypes
import os
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

from .. import paths

# 直连上游默认值（模型名可被 DL_LLM_MODEL 覆盖）
DEFAULT_BASE_URL = "https://token.sensenova.cn/v1/chat/completions"
DEFAULT_MODEL = "sensenova-6.8-flash-lite"

# 网关模式默认值（仅在 base_url 已被判成网关形状时才参与拼接）
GATEWAY_PREFIX = "/dsh-bridge-gw/v1"
DEFAULT_GATEWAY = "http://127.0.0.1:8791" + GATEWAY_PREFIX
CHANNELS = ("qoder-cn", "qoder-global", "workbuddy-intl", "workbuddy-cn")
DEFAULT_CHANNEL = "qoder-cn"

# 单张内联上限 3MB、整包硬限 40MB（均按 base64 之后的载荷算：原始字节膨胀 4/3）
MAX_IMAGE_BYTES = 3 * 1024 * 1024
MAX_TOTAL_BYTES = 40 * 1024 * 1024

# content 为空但 reasoning 非空时的定级文案（测试按这个子串断言）
#: 上游硬上限（实测 400 报错信息："should be in [1, 65536]"）。任何放大 max_tokens 的路径
#: 都必须先被它钳制，否则会从「空内容」变成不可重试的 400。
#: 429/413/5xx 的退避重试：次数与单次上限。这三种都不是「这一片有问题」，
#: 重发通常就过；不重发等于把并发换来的产能又扔掉（见 DL-11）。
#:
#: 413 为什么也算可重试（2026-10-23 实测，反直觉）：报错文案是
#: "inference request payload too large"，但同一片原样重发**时好时坏**——
#: 同一 slice（8 张图、base64 仅 0.2MB、最长边 1280）连续 5 次全 413；
#: 换 1 张、2 张却成功；再换回 4 张又成功（328s）。
#: 请求体只有 0.1–0.5MB，远低于任何合理上限，**所以这不是体积问题**。
#: 真因疑为上游按 (账号/模型/时刻) 做的并发配额判定，回同一个 413 文案。
#: 保守做法：既压缩体积（已做：抽样 8 张 + 长边 1280），也允许退避重试。
#: 退避要能**跨出 rpm 的滚动窗口**，否则重试必然再撞同一堵墙。
#: 实测（2026-10-23，40 并发）：tries=4 / max=20s（总 2+4+8+16=30s）时，
#: 退避完仍是 429 "rpm exhausted" —— 因为 rpm 按 60s 滚动窗口计，30s 还在窗内。
#:
#: 但也不能无限加长：退避是**阻塞式 sleep，占着线程槽位不产出**。
#: 40 并发下 19/23 的失败是 rpm 时，一半槽位在睡觉，净产出从 7.9 掉到 3.96 片/分。
#: 折中：2 次退避（20s + 45s = 65s，刚好跨过一个 60s 窗），失败就交回 batch 层
#: 靠 --rounds 下一轮重试（那时窗口早已过去，且不占槽位）。
RATE_LIMIT_TRIES = int(os.environ.get("DL_RATE_LIMIT_TRIES") or 2)
RATE_LIMIT_BASE_S = float(os.environ.get("DL_RATE_LIMIT_BASE_S") or 20.0)
RATE_LIMIT_MAX_S = float(os.environ.get("DL_RATE_LIMIT_MAX_S") or 45.0)
_RETRYABLE_CODES = frozenset({413, 429})

MAX_TOKENS_CAP = 65536

THINKING_HINT = "thinking 吃光了 token，请增大 max_tokens 或确认 thinking.enabled=false"

# 本模型的推理量随机暴涨：实测同一片同一图，推理 token 在 1285 ~ 16384 之间跳，
# 涨到上限时 content 返回空串（耗时 900s 仍失败）。加一段 system 指令让模型直接产出，
# 实测可把该片从「16384 推理 / 空内容」压到「1285 推理 / 1170 字有效 JSON」。
JSON_SYSTEM_PROMPT = (
    "你是截图分析助手。严格按用户要求的结构直接输出 JSON，"
    "不要输出思考过程、不要解释、不要代码围栏以外的任何文字。"
)

CONFIG_HINT = (
    "模型通道未配置。直连上游请设 DL_LLM_API_KEY（可加 DL_LLM_MODEL，"
    "默认 " + DEFAULT_MODEL + "，端点默认 " + DEFAULT_BASE_URL + "）；"
    "走网关则设 DL_LLM_BASE_URL=" + DEFAULT_GATEWAY +
    " 与 DL_LLM_MODEL=<通道>/<模型>（通道取 " + "/".join(CHANNELS) + "）；"
    "也可写 01-Projects/second-brain/config/llm.json 作为回退。"
)
LLM_CFG_JSON = paths.PROJECT.parent / "second-brain" / "config" / "llm.json"


class LLMError(RuntimeError):
    """模型通道的任何失败（未配置 / 传输 / 非 200 / 空响应）都收敛到这里。"""


class _Retryable(LLMError):
    """可退避重试的上游错误（429 限流、5xx 服务端）。不对外暴露。"""


class EmptyContentError(LLMError):
    """content 为空（reasoning 吃光 max_tokens 或上游真空回）。带归因字段供上层决定要不要重试。"""

    def __init__(self, message: str, reasoning: str = "", usage=None, starved: bool = False):
        super().__init__(message)
        self.reasoning = reasoning
        self.usage = usage
        self.starved = starved          # True = reasoning 非空（thinking 吃光 token 型）


@dataclass
class ChatConfig:
    base_url: str
    api_key: str
    model: str
    timeout: float = 180.0
    # 契约 §2.8 的四个字段之外的两个可选补充（都有默认值，构造方式保持兼容）：
    thinking: bool = False      # False → 请求体带 thinking:{enabled:false}（默认，实测必需）
    mode: str = ""              # "" 自动判别 / "direct" / "gateway"
    retry_on_empty: bool = True  # 未显式指定 thinking 时，content 被思考吃光就放大 max_tokens 重试一次


def _env(name: str) -> str:
    return (os.environ.get(name) or "").strip()


def _env_flag(name: str, default: bool) -> bool:
    raw = _env(name).lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


def load_config() -> ChatConfig:
    """环境变量优先，回退 01-Projects/second-brain/config/llm.json；都缺时 base_url/model 为空串。"""
    base_url = _env("DL_LLM_BASE_URL")
    api_key = _env("DL_LLM_API_KEY")
    model = _env("DL_LLM_MODEL")
    timeout_raw = _env("DL_LLM_TIMEOUT")
    timeout = float(timeout_raw) if timeout_raw else 0.0
    thinking = _env_flag("DL_LLM_THINKING", False)
    retry_on_empty = _env_flag("DL_LLM_RETRY", True)
    mode = _env("DL_LLM_MODE").lower()
    if not (base_url and model) and LLM_CFG_JSON.exists():
        try:
            raw = json.loads(LLM_CFG_JSON.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raw = {}
        if isinstance(raw, dict):
            base_url = base_url or str(raw.get("baseURL") or raw.get("base_url") or "").strip()
            api_key = api_key or str(raw.get("apiKey") or raw.get("api_key") or "").strip()
            model = model or str(raw.get("model") or "").strip()
            if not timeout:
                timeout = float(raw.get("timeout") or 0.0)
    return ChatConfig(base_url=base_url.rstrip("/"), api_key=api_key, model=model,
                      timeout=timeout or 180.0, thinking=thinking, mode=mode,
                      retry_on_empty=retry_on_empty)


def _is_gateway(base_url: str) -> bool:
    url = (base_url or "").rstrip("/")
    if not url:
        return False
    return GATEWAY_PREFIX in url or url.endswith("/chat")


def is_configured() -> bool:
    """可用的最小条件。

    - 直连模式：有 api_key（base_url / model 可走默认值，缺 key 一定 401）；
    - 网关模式：有 model（回环来源免 token，非回环才需要 api_key）；
    - 什么都没有：False（chat 会抛带指路的 LLMError）。
    """
    cfg = load_config()
    if cfg.mode == "gateway" or _is_gateway(cfg.base_url):
        # 网关：回环来源免 token，但 base_url 与 model（含通道前缀）都必须给
        return bool(cfg.base_url) and bool(cfg.model)
    # 直连：base_url/model 都有默认值，唯一可能缺的就是 key
    return bool(cfg.api_key)


def _split_model(model: str) -> tuple[str, str]:
    """'workbuddy-intl/gpt-5' → ('workbuddy-intl', 'gpt-5')；无通道前缀时用 DL_LLM_CHANNEL/默认。"""
    head, sep, tail = model.partition("/")
    if sep and head in CHANNELS:
        return head, tail
    return (_env("DL_LLM_CHANNEL") or DEFAULT_CHANNEL), model


def _content_text(content) -> str:
    """OpenAI content 可能是字符串，也可能是部件数组。"""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        out = []
        for part in content:
            if isinstance(part, dict):
                if part.get("type") in (None, "text") and isinstance(part.get("text"), str):
                    out.append(part["text"])
            elif isinstance(part, str):
                out.append(part)
        return "".join(out)
    return str(content)


_MAGIC = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF8", "image/gif"),
)


def _sniff_mime(raw: bytes, name: str) -> str:
    """按魔数判型 —— Windows 的 mimetypes 走注册表，.webp 常被报成 image/png，
    而 data URL 的 MIME 与真实字节不符时上游可能直接拒收。"""
    for magic, mime in _MAGIC:
        if raw.startswith(magic):
            return mime
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "image/webp"
    return mimetypes.guess_type(name)[0] or "image/png"


def _data_url(path: Path) -> tuple[str, int]:
    raw = path.read_bytes()
    mime = _sniff_mime(raw, path.name)
    b64 = base64.b64encode(raw).decode("ascii")
    url = "data:" + mime + ";base64," + b64
    return url, len(url)


class ChatClient:
    def __init__(self, cfg: ChatConfig | None = None, thinking: bool | None = None):
        self.cfg = cfg or load_config()
        raw_url = (self.cfg.base_url or "").strip()
        forced = (self.cfg.mode or "").lower()
        if forced in ("direct", "gateway"):
            self.gateway = forced == "gateway"
        else:
            self.gateway = _is_gateway(raw_url)
        self.url = raw_url or (DEFAULT_GATEWAY if self.gateway else DEFAULT_BASE_URL)
        self._thinking_default = bool(self.cfg.thinking) if thinking is None else bool(thinking)

    # ── 请求体 ────────────────────────────────────────────────────────────
    @property
    def endpoint(self) -> str:
        return self.url

    def _thinking_on(self, thinking: bool | None) -> bool:
        return self._thinking_default if thinking is None else bool(thinking)

    def _payload(self, messages: list[dict], max_tokens: int, thinking: bool) -> dict:
        # thinking 必须是显式 object：上游把它当必填，缺省等于 {enabled: true}，
        # 推理 token 会吃光 max_tokens 导致 content 空串（实测踩过两次）。
        think_block = {"enabled": bool(thinking)}
        max_tokens = int(max_tokens)
        if self.gateway:
            channel, model = _split_model(self.cfg.model or DEFAULT_MODEL)
            return {
                "channelId": channel,
                "body": {
                    "model": model,
                    "messages": messages,
                    "max_tokens": max_tokens,
                    "stream": True,
                    "thinking": think_block,
                },
            }
        return {
            "model": self.cfg.model or DEFAULT_MODEL,
            "messages": messages,
            "max_tokens": max_tokens,
            "stream": False,
            "thinking": think_block,
        }

    def _headers(self) -> dict:
        headers = {"content-type": "application/json"}
        if self.gateway:
            headers["accept"] = "text/event-stream"
        else:
            headers["accept"] = "application/json"
        if self.cfg.api_key:
            headers["authorization"] = "Bearer " + self.cfg.api_key
        return headers

    # ── 传输层 ────────────────────────────────────────────────────────────
    def _post(self, payload: dict) -> dict:
        """带限流退避的发送。

        429 是「这一秒请求太多」，不是「这一片有问题」——退避几秒重发通常就过。
        旧实现把它和其他错误一起归成 LLMError 直接抛，调用方只能整片判失败再等下一轮
        （batch_vision 一轮要几分钟），40 并发时 18 次 429 就等于白扔 18 片产能。
        见 DL-11：429 的处置是「等」，不是「放弃」。

        可重试：429（限流）、413（上游容量判定，非体积问题，见常量注释）、5xx。
        不可重试：400（sensitive image 等内容判定）、401（凭据）、其余 4xx —— 立即抛出。
        """
        last: Exception | None = None
        for attempt in range(RATE_LIMIT_TRIES + 1):
            try:
                return self._post_once(payload)
            except _Retryable as exc:
                last = exc
                if attempt >= RATE_LIMIT_TRIES:
                    break
                time.sleep(min(RATE_LIMIT_MAX_S, RATE_LIMIT_BASE_S * (2 ** attempt)))
        raise last  # type: ignore[misc]

    def _post_once(self, payload: dict) -> dict:
        if not self.url:
            raise LLMError("模型通道未配置（缺 base_url）｜ " + CONFIG_HINT)
        if not self.gateway and not self.cfg.api_key:
            raise LLMError("直连上游缺 API key（DL_LLM_API_KEY）｜ " + CONFIG_HINT)
        try:
            with httpx.Client(trust_env=False, timeout=self.cfg.timeout) as c:
                if self.gateway:
                    return self._post_gateway(c, payload)
                resp = c.post(self.url, json=payload, headers=self._headers())
                if resp.status_code != 200:
                    body = resp.read().decode("utf-8", "replace")
                    msg = "上游返回 %s（%s）：%s" % (
                        resp.status_code, self.url, _error_text(body)[:300]
                    )
                    if resp.status_code in _RETRYABLE_CODES or resp.status_code >= 500:
                        raise _Retryable(msg)
                    raise LLMError(msg)
                return _parse_completion(resp.read().decode("utf-8", "replace"))
        except LLMError:
            raise
        except httpx.HTTPError as exc:
            raise LLMError(
                "连不上模型通道 %s（%s: %s）" % (self.url, type(exc).__name__, exc)
            ) from exc

    def _post_gateway(self, client: httpx.Client, payload: dict) -> dict:
        headers = self._headers()
        with client.stream("POST", self.url, json=payload, headers=headers) as resp:
            if resp.status_code != 200:
                body = resp.read().decode("utf-8", "replace")
                raise LLMError(
                    "模型网关返回 %s：%s ｜ %s"
                    % (resp.status_code, _error_text(body)[:300], CONFIG_HINT)
                )
            ctype = (resp.headers.get("content-type") or "").lower()
            if "event-stream" in ctype:
                return self._parse_sse(resp.iter_lines())
            return _parse_completion(resp.read().decode("utf-8", "replace"))

    def _parse_sse(self, lines) -> dict:
        chunks: list[str] = []
        reasoning: list[str] = []
        model = ""
        usage = None
        errors: list[str] = []
        for line in lines:
            line = (line or "").strip()
            if not line or line.startswith(":"):
                continue
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                obj = json.loads(data)
            except ValueError:
                continue
            err = obj.get("error")
            if err:
                errors.append(_error_text(json.dumps(obj, ensure_ascii=False)))
                continue
            model = obj.get("model") or model
            usage = obj.get("usage") or usage
            for choice in obj.get("choices") or []:
                if not isinstance(choice, dict):
                    continue
                delta = choice.get("delta") or {}
                text = _content_text(delta.get("content"))
                if text:
                    chunks.append(text)
                elif delta.get("reasoning"):
                    reasoning.append(_content_text(delta.get("reasoning")))
                elif delta.get("reasoning_content"):
                    reasoning.append(_content_text(delta.get("reasoning_content")))
                elif not delta:
                    chunks.append(_content_text((choice.get("message") or {}).get("content")))
        content = "".join(chunks)
        if not content and errors:
            raise LLMError("模型网关流内报错：%s ｜ %s" % (errors[0][:300], CONFIG_HINT))
        if not content.strip():
            _raise_empty("".join(reasoning), usage, self.gateway)
        return {"content": content, "model": model or self.cfg.model, "usage": usage,
                "reasoning": "".join(reasoning)}

    # ── 对外 ──────────────────────────────────────────────────────────────
    def _send(self, messages: list[dict], max_tokens: int, thinking: bool | None) -> dict:
        """发一次；默认路径下若 content 被思考吃光，则放大 max_tokens 重试一次。

        背景（实测）：sensenova-6.8-flash-lite 对 thinking:{enabled:false} 只当提示而非开关 ——
        同一提示词 5 次里仍有 1 次把 max_tokens 全烧在 reasoning 上。因此默认路径必须有一次
        自救重试；但**调用方显式指定 thinking 时不再重试**，否则「空 content」的诊断会被掩盖。
        """
        payload = self._payload(messages, max_tokens, self._thinking_on(thinking))
        try:
            return self._post(payload)
        except EmptyContentError as exc:
            if not exc.starved or thinking is not None or not self.cfg.retry_on_empty:
                raise
            # ★ 不得突破上游硬上限（实测报错：
            #   {"error":"field MaxTokens invalid, should be in [1, 65536]"}）。
            # 旧写法 bigger = max(max_tokens*4, 4096) 没有封顶：当调用方已按 MAX_TOKENS=65536
            # 发送时，放大成 262,144 → 直接 400，且因为是 400 而不是 EmptyContentError，
            # 外层「空内容重试」循环收不到，整片就 FAIL 掉了。
            bigger = min(MAX_TOKENS_CAP, max(int(max_tokens) * 4, 4096))
            # 已经顶到上限时**不再放大，但仍原样重发一次**：
            # 该模型 reasoning 暴涨是随机的，同一预算重发常常就出内容了（实测）。
            # 旧写法在这里 `raise`，等于「到上限就放弃」，把可救的片判死。
            if bigger < int(max_tokens):
                bigger = int(max_tokens)
            payload = self._payload(messages, bigger, self._thinking_on(thinking))
            result = self._post(payload)
            result["retried"] = True
            result["max_tokens_used"] = bigger
            return result

    def chat(self, messages: list[dict], max_tokens: int = 2048,
             thinking: bool | None = None) -> dict:
        """返回 {"content","model","usage","reasoning"}。"""
        return self._send(messages, max_tokens, thinking)

    def chat_images(self, text: str, image_paths: list[str], max_tokens: int = 4096,
                    thinking: bool | None = None) -> dict:
        """内联 image_url 部件；单张 >3MB 跳过并计数；整包 >40MB 从尾部丢图。

        入参可以是**磁盘路径**，也可以直接是**已编码好的 data URL**。
        后者是视觉通道的实际用法：截图在 ManicTime zip 里，vision 已经读成字节并编好 URL，
        再把 zip 内的相对成员名（`Screenshots/2026-08-14/x.webp`）传进来 —— 本机没有这个文件，
        `Path.read_bytes()` 必抛 OSError，被计成 missing 静默丢掉。实测 PC 侧 1,874 条
        slice_result 因此 **images_sent=0**：看着像"看了图在总结"，其实一条画面都没送到模型。
        """
        kept: list[dict] = []
        skipped = {"oversize": 0, "missing": 0, "total_limit": 0}
        for p in image_paths:
            if isinstance(p, str) and p.startswith("data:"):
                url, size = p, len(p)
            else:
                try:
                    url, size = _data_url(Path(p))
                except OSError:
                    skipped["missing"] += 1
                    continue
            if size > MAX_IMAGE_BYTES:
                skipped["oversize"] += 1
                continue
            kept.append({"type": "image_url", "image_url": {"url": url}})
            kept[-1]["_bytes"] = size
        total = sum(item["_bytes"] for item in kept)
        while kept and total > MAX_TOTAL_BYTES:
            dropped = kept.pop()          # 从尾部丢图
            total -= dropped["_bytes"]
            skipped["total_limit"] += 1
        for item in kept:
            item.pop("_bytes", None)

        dropped_total = sum(skipped.values())
        note = _drop_note(skipped)
        parts: list[dict] = [{"type": "text", "text": text + note}]
        parts.extend(kept)
        messages = [{"role": "system", "content": JSON_SYSTEM_PROMPT},
                    {"role": "user", "content": parts}]
        result = self._send(messages, max_tokens, thinking)
        result["images_sent"] = len(kept)
        result["images_dropped"] = dropped_total
        result["images_skipped"] = skipped
        return result


def _drop_note(skipped: dict) -> str:
    bits = []
    if skipped.get("oversize"):
        bits.append("%d 张因单张内联超过 %dMB 被跳过" % (skipped["oversize"], MAX_IMAGE_BYTES // (1024 * 1024)))
    if skipped.get("total_limit"):
        bits.append("%d 张因整包超过 %dMB 已从尾部丢弃" % (skipped["total_limit"], MAX_TOTAL_BYTES // (1024 * 1024)))
    if skipped.get("missing"):
        bits.append("%d 张截图文件缺失" % skipped["missing"])
    if not bits:
        return ""
    return "\n\n（注意：本片另有 " + "、".join(bits) + "，不要把缺失部分当作「无活动」推断。）"


def _error_text(body: str) -> str:
    """上游错误体可能是 OpenAI 形状 {error:{message}}；网关是 {ok:false,error}。"""
    try:
        obj = json.loads(body)
    except ValueError:
        return body.strip()
    if isinstance(obj, dict):
        for key in ("error", "message", "detail"):
            val = obj.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
            if isinstance(val, dict):
                inner = val.get("message") or val.get("msg") or val.get("code")
                if isinstance(inner, str) and inner.strip():
                    return inner.strip()
    return body.strip()


def _reasoning_tokens(usage) -> object:
    if not isinstance(usage, dict):
        return None
    details = usage.get("completion_tokens_details")
    if isinstance(details, dict):
        return details.get("reasoning_tokens")
    return None


def _raise_empty(reasoning: str, usage, is_gateway: bool) -> None:
    """空 content 的分级报错：reasoning 非空 = thinking 吃光 token（可自救）；两者都空 = 上游异常。"""
    rt = _reasoning_tokens(usage)
    where = "模型网关" if is_gateway else "上游"
    if (reasoning or "").strip():
        raise EmptyContentError(
            "%s content 为空但 reasoning 非空（reasoning_tokens=%s，括号内为思考开头）：%s ｜ %s"
            % (where, rt, (reasoning.strip()[:200]), THINKING_HINT),
            reasoning=reasoning, usage=usage, starved=True,
        )
    raise EmptyContentError(
        "%s返回空 content，且 reasoning 也为空（reasoning_tokens=%s）。可能是 max_tokens 过小、"
        "提示词被拒或该模型不可用。｜ %s" % (where, rt, CONFIG_HINT),
        reasoning=reasoning, usage=usage, starved=False,
    )


def _parse_completion(body: str) -> dict:
    """直连（及网关非 SSE 回包）的响应解析：正文取 choices[0].message.content。"""
    try:
        obj = json.loads(body)
    except ValueError as exc:
        raise LLMError("上游返回非 JSON 响应：%s" % body.strip()[:300]) from exc
    if not isinstance(obj, dict):
        raise LLMError("上游返回的 JSON 不是对象：%s" % body.strip()[:300])
    if obj.get("error"):
        raise LLMError("上游报错：%s" % _error_text(body)[:300])
    message: dict = {}
    choices = obj.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0]
        if isinstance(first, dict):
            raw_msg = first.get("message")
            if isinstance(raw_msg, dict):
                message = raw_msg
            elif first.get("text") is not None:
                message = {"content": first.get("text")}
    content = _content_text(message.get("content"))
    reasoning = _content_text(message.get("reasoning") or message.get("reasoning_content"))
    usage = obj.get("usage") or {}
    if not content.strip():
        _raise_empty(reasoning, usage, False)
    return {
        "content": content,
        "model": obj.get("model") or "",
        "usage": usage,
        "reasoning": reasoning,
    }
