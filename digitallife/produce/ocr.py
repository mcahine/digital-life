"""本地 OCR —— rapidocr-onnxruntime + PIL（可选依赖）。

实测（本机，2026-10-21，原图 1936x1066）：原图 7.5–18.8 秒，缩到 1280 宽 13.9 秒，
1024 宽 10.3 秒 —— 所以默认 max_side=1280：既压住耗时，又不至于把 11px 的系统
小字压成糊。分辨率降太多会让「识别到字但认错字」变多，比不识别更危险。

rapidocr 是可选依赖（pip install -e .[ocr]）：缺了必须明确抛 OcrUnavailable 并指路，
不许静默返回空串 —— 空串会被上层当作「这张图没文字」，把整条链路骗过去。
"""
from __future__ import annotations

import io
from pathlib import Path

OCR_ENGINE = "rapidocr"
INSTALL_HINT = "pip install -e .[ocr]   # 或在项目根执行 uv sync --extra ocr"

_engine = None


class OcrUnavailable(RuntimeError):
    """缺 rapidocr / PIL，或本机无法初始化 OCR 引擎。"""


def _load_pil():
    try:
        from PIL import Image  # noqa: WPS433  延迟导入：缺依赖时才报 OcrUnavailable
    except Exception as exc:  # pragma: no cover - 取决于环境
        raise OcrUnavailable(
            "缺少 PIL（pillow），无法预处理截图。请安装可选依赖：" + INSTALL_HINT
        ) from exc
    return Image


def _load_engine():
    global _engine
    if _engine is not None:
        return _engine
    try:
        from rapidocr_onnxruntime import RapidOCR  # noqa: WPS433
    except Exception as exc:
        raise OcrUnavailable(
            "缺少 rapidocr-onnxruntime，本地 OCR 不可用。请安装可选依赖：" + INSTALL_HINT
        ) from exc
    try:
        _engine = RapidOCR()
    except Exception as exc:  # 模型文件损坏 / onnxruntime 初始化失败
        raise OcrUnavailable(
            "rapidocr 初始化失败（%s: %s）。重装可选依赖通常可修：%s"
            % (type(exc).__name__, exc, INSTALL_HINT)
        ) from exc
    return _engine


def available() -> bool:
    """探测依赖是否齐全；缺依赖返回 False，绝不抛。"""
    try:
        _load_pil()
    except OcrUnavailable:
        return False
    try:
        import importlib.util

        return importlib.util.find_spec("rapidocr_onnxruntime") is not None
    except Exception:
        return False


def _prepare(path: Path, max_side: int) -> bytes:
    Image = _load_pil()
    with Image.open(path) as im:
        im = im.convert("RGB")
        w, h = im.size
        long_side = max(w, h)
        if max_side and long_side > max_side:
            ratio = max_side / float(long_side)
            im = im.resize((max(1, int(w * ratio)), max(1, int(h * ratio))), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, format="PNG")
        return buf.getvalue()


def ocr_image(path: str | Path, max_side: int = 1280) -> str:
    """缩图后 OCR，返回按行拼接的纯文本（无文字返回空串）。"""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError("截图不存在：%s" % p)
    engine = _load_engine()
    png = _prepare(p, max_side)
    try:
        result, _elapse = engine(png)
    except Exception as exc:
        raise OcrUnavailable(
            "OCR 识别失败（%s: %s）；文件 %s。请确认该图可正常打开，或重装 %s"
            % (type(exc).__name__, exc, p, INSTALL_HINT)
        ) from exc
    lines: list[str] = []
    for item in result or []:
        # rapidocr 返回 [box, text, score]
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            text = str(item[1]).strip()
            if text:
                lines.append(text)
    return "\n".join(lines)
