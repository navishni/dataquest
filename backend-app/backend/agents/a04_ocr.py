"""Agent 04 - OCR. Deterministic engine wrappers behind one interface.

Engines register themselves in ENGINES (name -> callable(PIL.Image, timeout) -> [(text, bbox, conf0_1)]).
Built-in: tesseract (pytesseract, if the binary is present) and paddleocr (if importable). `engine` selects one;
default is the first available in config.ocr.engines order. Add TrOCR/EasyOCR by registering another callable.

Confidence normalisation: Tesseract word confidences are 0-100 -> /100, line confidence = mean of its word
confidences (words with conf<0 ignored); PaddleOCR already returns 0-1. Lines sorted top-to-bottom then
left-to-right. Region requests crop the page image and boxes are mapped back to full-page pixel coordinates.
Preprocess: grayscale + autocontrast only (coordinates are never altered); deskew/denoise are NOT done (limit).
handwriting_detected is only a flag: True when mean line confidence < 0.45 with >=3 lines, else False.
Timeout per page from config.ocr.timeout_seconds -> TIMEOUT.
"""
import io
import os
import shutil
from typing import Callable, Optional

from pydantic import BaseModel, ConfigDict, Field

from ..common import audit, config
from ..common.errors import AgentError, ErrorCode
from ..common.models import Location
from ..common.store import store
from .a03_native_text import page_id

Engine = Callable[..., list]
ENGINES: dict[str, Engine] = {}


def _tesseract(img, timeout: int, lang: str = "eng") -> list:
    import pytesseract
    try:
        d = pytesseract.image_to_data(img, lang=lang, output_type=pytesseract.Output.DICT, timeout=timeout)
    except RuntimeError as e:  # pytesseract raises RuntimeError on timeout
        raise AgentError(ErrorCode.TIMEOUT, "OCR timed out") from e
    except Exception:  # noqa: BLE001
        raise AgentError(ErrorCode.ENGINE_FAILED, "Tesseract failed")
    lines: dict = {}
    for i, t in enumerate(d["text"]):
        if not t.strip():
            continue
        key = (d["block_num"][i], d["par_num"][i], d["line_num"][i])
        x, y, w, h = d["left"][i], d["top"][i], d["width"][i], d["height"][i]
        c = float(d["conf"][i])
        L = lines.setdefault(key, {"w": [], "box": [x, y, x + w, y + h], "c": []})
        L["w"].append(t)
        L["box"] = [min(L["box"][0], x), min(L["box"][1], y), max(L["box"][2], x + w), max(L["box"][3], y + h)]
        if c >= 0:
            L["c"].append(c / 100.0)
    return [(" ".join(L["w"]), L["box"], sum(L["c"]) / len(L["c"]) if L["c"] else 0.0) for L in lines.values()]


def _paddle(img, timeout: int, lang: str = "en") -> list:  # pragma: no cover - optional engine
    import numpy as np
    from paddleocr import PaddleOCR
    res = PaddleOCR(lang=lang, show_log=False).ocr(np.array(img), cls=True)
    out = []
    for line in (res[0] or []):
        pts, (text, conf) = line
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        out.append((text, [min(xs), min(ys), max(xs), max(ys)], float(conf)))
    return out


def _register() -> None:
    configured_tesseract = os.environ.get("PF_TESSERACT_CMD", "").strip()
    tesseract_path = configured_tesseract or shutil.which("tesseract")
    if tesseract_path and (os.path.isfile(tesseract_path) or shutil.which(tesseract_path)):
        try:
            import pytesseract
            if configured_tesseract:
                pytesseract.pytesseract.tesseract_cmd = configured_tesseract
            ENGINES["tesseract"] = _tesseract
        except ImportError:
            pass
    try:
        import paddleocr  # noqa: F401
        ENGINES["paddleocr"] = _paddle
    except ImportError:
        pass


_register()


class OcrInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_id: str
    page_number: int = Field(ge=1)
    region: Optional[list[float]] = None  # [x1,y1,x2,y2] in page pixels
    engine: Optional[str] = None


class OcrLine(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str
    location: Location
    confidence: float = Field(ge=0, le=1)


class OcrOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    engine: str
    lines: list[OcrLine]
    handwriting_detected: Optional[bool] = None


def run(inp: OcrInput) -> OcrOutput:
    from PIL import Image, ImageOps
    avail = [e for e in config.get("ocr.engines", []) if e in ENGINES] or list(ENGINES)
    if not avail:
        raise AgentError(ErrorCode.ENGINE_FAILED, "No OCR engine is installed; configure Tesseract with PF_TESSERACT_CMD or install PaddleOCR")
    name = inp.engine or avail[0]
    if name not in ENGINES:
        raise AgentError(ErrorCode.INVALID_INPUT, "Unknown or unavailable OCR engine", {"available": avail})
    png = store.page_image(inp.source_id, inp.page_number)
    img = Image.open(io.BytesIO(png)).convert("L")
    pw, ph = img.size
    ox = oy = 0
    if inp.region:
        x1, y1, x2, y2 = inp.region
        if not (0 <= x1 < x2 <= pw and 0 <= y1 < y2 <= ph):
            raise AgentError(ErrorCode.INVALID_INPUT, "Region outside page")
        img, ox, oy = img.crop((int(x1), int(y1), int(x2), int(y2))), int(x1), int(y1)
    img = ImageOps.autocontrast(img)
    raw = ENGINES[name](img, config.get("ocr.timeout_seconds", 30))
    lines = []
    for text, box, conf in raw:
        b = [box[0] + ox, box[1] + oy, box[2] + ox, box[3] + oy]
        lines.append(OcrLine(text=text, confidence=round(min(1.0, max(0.0, conf)), 4),
                             location=Location(bbox=[float(v) for v in b], page_width=pw, page_height=ph)))
    lines.sort(key=lambda l: (round(l.location.bbox[1] / 10), l.location.bbox[0]))
    hw = None
    if lines:
        hw = len(lines) >= 3 and (sum(l.confidence for l in lines) / len(lines)) < 0.45
    out = OcrOutput(engine=name, lines=lines, handwriting_detected=hw)
    store.put("ocr", f"{page_id(inp.source_id, inp.page_number)}:{name}", out.model_dump(mode="json"))
    audit.append(event_type="ocr_run", object_type="page", object_id=page_id(inp.source_id, inp.page_number),
                 details={"engine": name, "lines": len(lines)})
    return out
