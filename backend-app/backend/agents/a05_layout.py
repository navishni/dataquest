"""Agent 05 - Layout Detection.

Label set (published): text, title, table, figure, chart, equation, header, footer, list, caption, signature,
stamp, form_field.   layout_class values: single_column, multi_column, form, invoice_like, slide,
spreadsheet_like, other.

Detectors are pluggable: DETECTORS = [callable(source_id, page_number) -> (regions, page_w, page_h)], each region
{type, bbox[x1,y1,x2,y2] pixels, confidence}. An ML detector (DocLayout-YOLO / PP-Structure / LayoutParser) registers
itself by appending to DETECTORS (first one that returns regions wins). Built-in `geometric_detector`:
 * PDFs: PyMuPDF text/image blocks + PyMuPDF table finder. Rule-based confidences (documented, not model scores):
   table (ruling-line finder) 0.80, text 0.90, figure/image 0.85, title (font >= 1.3x median, <=2 lines) 0.70,
   header/footer (top/bottom 7% of page) 0.60, list (bullet/number prefix) 0.75, caption (Figure/Fig./Table prefix) 0.70.
 * Images/scans: OCR lines (agent 04) grouped into paragraph regions by vertical gap, confidence = mean line conf.
Regions overlapping at IoU >= config.layout.iou_merge are merged (higher confidence/larger type priority wins).
Regions below config.layout.min_confidence are kept out of `regions` and reported as LOW_CONFIDENCE_REGION warnings.
region_id = sha256(source_id|page|rounded bbox)[:16]. layout_class is derived from geometry (x-center column
clustering, table area share, form_field count, landscape/low-region-count for slides) - never guessed.
"""
import hashlib
import re
from statistics import median
from typing import Callable, Optional

from pydantic import BaseModel, ConfigDict, Field

from ..common import audit, config
from ..common.errors import AgentError, ErrorCode
from ..common.models import Location, WarningItem
from ..common.store import store
from .a03_native_text import page_id

LABELS = ["text", "title", "table", "figure", "chart", "equation", "header", "footer", "list", "caption",
          "signature", "stamp", "form_field"]
LAYOUT_CLASSES = ["single_column", "multi_column", "form", "invoice_like", "slide", "spreadsheet_like", "other"]
PRIORITY = {"table": 5, "chart": 5, "equation": 5, "figure": 4, "title": 3, "caption": 3, "header": 2, "footer": 2,
            "list": 2, "form_field": 2, "signature": 2, "stamp": 2, "text": 1}
_BULLET = re.compile(r"^\s*([•●\-\*]|\d+[.)]|[a-z][.)])\s+")
_CAPTION = re.compile(r"^\s*(figure|fig\.|table)\s*\d+", re.I)


class LayoutInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_id: str
    page_number: int = Field(ge=1)


class Region(BaseModel):
    model_config = ConfigDict(extra="forbid")
    region_id: str
    type: str
    location: Location
    confidence: float = Field(ge=0, le=1)


class LayoutOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    layout_class: str
    regions: list[Region]
    warnings: list[WarningItem] = []


def region_id(source_id: str, page_number: int, bbox: list[float]) -> str:
    key = f"{source_id}|{page_number}|{[round(v) for v in bbox]}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def iou(a: list[float], b: list[float]) -> float:
    x1, y1, x2, y2 = max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    ua = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / ua if ua > 0 else 0.0


def containment(inner: list[float], outer: list[float]) -> float:
    x1, y1, x2, y2 = max(inner[0], outer[0]), max(inner[1], outer[1]), min(inner[2], outer[2]), min(inner[3], outer[3])
    inter = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    area = (inner[2] - inner[0]) * (inner[3] - inner[1])
    return inter / area if area > 0 else 0.0


def geometric_detector(source_id: str, page_number: int):
    import pymupdf
    meta = store.meta(source_id)
    scale = config.get("page_classification.render_dpi", 200) / 72
    if meta["detected_mime"] == "application/pdf":
        doc = pymupdf.open(stream=store.get_file(source_id), filetype="pdf")
        if doc.needs_pass:
            password = store.get_password(source_id)
            if not password or not doc.authenticate(password):
                raise AgentError(ErrorCode.PASSWORD_REQUIRED, "Password-protected PDF could not be opened")
        if page_number > len(doc):
            raise AgentError(ErrorCode.NOT_FOUND, "Page not found")
        page = doc[page_number - 1]
        rm, rect = page.rotation_matrix, page.rect
        pw, ph = int(round(rect.width * scale)), int(round(rect.height * scale))

        def px(r) -> list[float]:
            r = pymupdf.Rect(r) * rm
            return [round(r.x0 * scale, 2), round(r.y0 * scale, 2), round(r.x1 * scale, 2), round(r.y1 * scale, 2)]

        d = page.get_text("dict")
        sizes = [s["size"] for b in d["blocks"] for l in b.get("lines", []) for s in l["spans"] if s["text"].strip()]
        med = median(sizes) if sizes else 10
        regs = []
        try:
            tables = list(page.find_tables().tables)
        except Exception:  # noqa: BLE001
            tables = []
        for t in tables:
            regs.append({"type": "table", "bbox": px(t.bbox), "confidence": 0.80})
        for b in d["blocks"]:
            bb = px(b["bbox"])
            if b["type"] == 1:
                regs.append({"type": "figure", "bbox": bb, "confidence": 0.85})
                continue
            lines = b.get("lines", [])
            text = " ".join("".join(s["text"] for s in l["spans"]) for l in lines).strip()
            if not text:
                continue
            if any(containment(bb, r["bbox"]) > 0.6 for r in regs if r["type"] == "table"):
                continue
            size = max((s["size"] for l in lines for s in l["spans"]), default=med)
            y0 = bb[1] / ph
            y1 = bb[3] / ph
            if y1 <= 0.07:
                typ, conf = "header", 0.60
            elif y0 >= 0.93:
                typ, conf = "footer", 0.60
            elif _CAPTION.match(text):
                typ, conf = "caption", 0.70
            elif size >= 1.3 * med and len(lines) <= 2:
                typ, conf = "title", 0.70
            elif all(_BULLET.match("".join(s["text"] for s in l["spans"])) for l in lines if l["spans"]) and lines:
                typ, conf = "list", 0.75
            else:
                typ, conf = "text", 0.90
            regs.append({"type": typ, "bbox": bb, "confidence": conf})
        return regs, pw, ph
    # raster: group OCR lines
    from . import a04_ocr
    try:
        ocr = a04_ocr.run(a04_ocr.OcrInput(source_id=source_id, page_number=page_number))
    except AgentError as exc:
        if exc.code != ErrorCode.ENGINE_FAILED:
            raise
        from PIL import Image
        import io
        with Image.open(io.BytesIO(store.page_image(source_id, page_number))) as image:
            pw, ph = image.size
        return [], pw, ph
    if not ocr.lines:
        return [], (ocr.lines and ocr.lines[0].location.page_width) or 0, 0
    pw, ph = ocr.lines[0].location.page_width, ocr.lines[0].location.page_height
    regs, cur = [], []
    for l in ocr.lines:
        if cur and l.location.bbox[1] - cur[-1].location.bbox[3] > 1.5 * (cur[-1].location.bbox[3] - cur[-1].location.bbox[1]):
            regs.append(cur)
            cur = []
        cur.append(l)
    if cur:
        regs.append(cur)
    out = []
    for grp in regs:
        bb = [min(l.location.bbox[0] for l in grp), min(l.location.bbox[1] for l in grp),
              max(l.location.bbox[2] for l in grp), max(l.location.bbox[3] for l in grp)]
        out.append({"type": "text", "bbox": bb, "confidence": sum(l.confidence for l in grp) / len(grp)})
    return out, pw, ph


DETECTORS: list[Callable] = [geometric_detector]


def _merge(regs: list[dict], thr: float) -> list[dict]:
    regs = sorted(regs, key=lambda r: (-PRIORITY.get(r["type"], 0), -r["confidence"], r["bbox"]))
    kept: list[dict] = []
    for r in regs:
        if any(iou(r["bbox"], k["bbox"]) >= thr or (containment(r["bbox"], k["bbox"]) >= 0.9
               and PRIORITY.get(k["type"], 0) >= PRIORITY.get(r["type"], 0)) for k in kept):
            continue
        kept.append(r)
    return sorted(kept, key=lambda r: (r["bbox"][1], r["bbox"][0]))


def classify_layout(regs: list[dict], pw: int, ph: int) -> str:
    if not regs:
        return "other"
    area = max(pw * ph, 1)
    tab_share = sum((r["bbox"][2] - r["bbox"][0]) * (r["bbox"][3] - r["bbox"][1]) for r in regs if r["type"] == "table") / area
    if sum(1 for r in regs if r["type"] == "form_field") >= 4:
        return "form"
    if tab_share >= 0.6:
        return "spreadsheet_like"
    if tab_share >= 0.25 and sum(1 for r in regs if r["type"] in ("text", "title") and r["bbox"][3] < ph / 3) >= 2:
        return "invoice_like"
    if pw > ph * 1.3 and len(regs) <= 6:
        return "slide"
    body = [r for r in regs if r["type"] in ("text", "list") and (r["bbox"][2] - r["bbox"][0]) < 0.65 * pw]
    centers = sorted((r["bbox"][0] + r["bbox"][2]) / 2 for r in body)
    cols, last = 0, None
    gap = config.get("layout.column_gap_ratio", 0.04) * pw
    for c in centers:
        if last is None or c - last > 0.15 * pw + gap:
            cols += 1
        last = c
    return "multi_column" if cols >= 2 and len(body) >= 4 else "single_column"


def run(inp: LayoutInput) -> LayoutOutput:
    regs, pw, ph = [], 0, 0
    for det in DETECTORS:
        regs, pw, ph = det(inp.source_id, inp.page_number)
        if regs:
            break
    cfg = config.get("layout")
    regs = _merge(regs, cfg["iou_merge"])
    warns, keep = [], []
    from . import a04_ocr
    router = store.get("router", inp.source_id) or {}
    unit = next((u for u in router.get("units", []) if u.get("page_number") == inp.page_number), {})
    if unit.get("page_class") in ("scanned", "mixed", "image_only") and not a04_ocr.ENGINES:
        warns.append(WarningItem(code="OCR_UNAVAILABLE", page_number=inp.page_number,
                                 message="No OCR engine is installed; visual page content is retained but not read as text. Install Tesseract and set PF_TESSERACT_CMD, or install PaddleOCR."))
    for r in regs:
        if r["confidence"] < cfg["min_confidence"]:
            warns.append(WarningItem(code="LOW_CONFIDENCE_REGION", page_number=inp.page_number,
                                     message=f"{r['type']} region below confidence threshold"))
        else:
            keep.append(r)
    regions = [Region(region_id=region_id(inp.source_id, inp.page_number, r["bbox"]), type=r["type"],
                      location=Location(bbox=r["bbox"], page_width=pw, page_height=ph),
                      confidence=round(r["confidence"], 4)) for r in keep]
    out = LayoutOutput(layout_class=classify_layout(keep, pw, ph), regions=regions, warnings=warns)
    store.put("layout", page_id(inp.source_id, inp.page_number), out.model_dump(mode="json"))
    audit.append(event_type="layout_run", object_type="page", object_id=page_id(inp.source_id, inp.page_number),
                 details={"regions": len(regions), "layout_class": out.layout_class})
    return out
