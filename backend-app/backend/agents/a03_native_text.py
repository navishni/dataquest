"""Agent 03 - Native Text Extraction. Deterministic; never OCRs.

PDF only (PyMuPDF get_text("dict")). DOCX/PPTX/HTML need a rendered PDF (LibreOffice) which is not part of
this build -> UNSUPPORTED_FORMAT. Spans are line-level; bboxes are scaled to the page image pixel space
(DPI = config.page_classification.render_dpi, default 200) and transformed through the page rotation matrix so
they are upright. Hidden (white / fully transparent) text is excluded and flagged HIDDEN_TEXT; text outside the
page box is flagged TEXT_OUTSIDE_PAGE.

Confidence = 1.0 - min(1, 3 * garbage_ratio) where garbage_ratio = (private-use code points + U+FFFD) / chars,
computed per line. has_usable_text = at least one span AND page garbage_ratio <= native_text.max_garbage_ratio.
"""
import re
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from ..common import audit, config
from ..common.errors import AgentError, ErrorCode
from ..common.models import Location, WarningItem
from ..common.store import store

_GARBAGE = re.compile(r"[-�]")


class NativeTextInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_id: str
    page_number: int = Field(ge=1)


class Span(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str
    location: Location
    font: Optional[str] = None
    confidence: float = Field(ge=0, le=1)


class NativeTextOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    page_id: str
    spans: list[Span]
    has_usable_text: bool
    warnings: list[WarningItem] = []


def page_id(source_id: str, page_number: int) -> str:
    return f"{source_id}-p{page_number}"


def garbage_ratio(text: str) -> float:
    return len(_GARBAGE.findall(text)) / len(text) if text else 0.0


def run(inp: NativeTextInput) -> NativeTextOutput:
    import pymupdf
    meta = store.meta(inp.source_id)
    if meta["detected_mime"] != "application/pdf":
        raise AgentError(ErrorCode.UNSUPPORTED_FORMAT, "Native text extraction supports PDF only")
    try:
        doc = pymupdf.open(stream=store.get_file(inp.source_id), filetype="pdf")
    except Exception:  # noqa: BLE001
        raise AgentError(ErrorCode.CORRUPT_FILE, "PDF is corrupt")
    if doc.needs_pass:
        password = store.get_password(inp.source_id)
        if not password or not doc.authenticate(password):
            raise AgentError(ErrorCode.PASSWORD_REQUIRED, "Password-protected PDF could not be opened")
    if inp.page_number > len(doc):
        raise AgentError(ErrorCode.NOT_FOUND, "Page not found")
    page = doc[inp.page_number - 1]
    scale = config.get("page_classification.render_dpi", 200) / 72
    rect = page.rect  # already rotated page rect
    pw, ph = int(round(rect.width * scale)), int(round(rect.height * scale))
    rm = page.rotation_matrix
    spans, warnings, all_text = [], [], ""
    d = page.get_text("dict", flags=pymupdf.TEXT_PRESERVE_LIGATURES)
    for blk in d["blocks"]:
        for line in blk.get("lines", []):
            vis = [s for s in line["spans"] if s["text"].strip() and s["color"] != 0xFFFFFF and s.get("alpha", 255) > 0]
            if len(vis) < len([s for s in line["spans"] if s["text"].strip()]):
                warnings.append(WarningItem(code="HIDDEN_TEXT", message="Hidden or white text excluded",
                                            page_number=inp.page_number))
            if not vis:
                continue
            text = "".join(s["text"] for s in vis).strip()
            r = pymupdf.Rect(line["bbox"]) * rm
            if not rect.contains(r) and (r & rect).is_empty:
                warnings.append(WarningItem(code="TEXT_OUTSIDE_PAGE", message="Text outside page box excluded",
                                            page_number=inp.page_number))
                continue
            bbox = [round(r.x0 * scale, 2), round(r.y0 * scale, 2), round(r.x1 * scale, 2), round(r.y1 * scale, 2)]
            gr = garbage_ratio(text)
            spans.append(Span(text=text, font=vis[0].get("font"),
                              location=Location(bbox=bbox, page_width=pw, page_height=ph),
                              confidence=round(max(0.0, 1.0 - min(1.0, 3 * gr)), 4)))
            all_text += text
    spans.sort(key=lambda s: (round(s.location.bbox[1] / 5), s.location.bbox[0]))
    usable = bool(spans) and garbage_ratio(all_text) <= config.get("native_text.max_garbage_ratio", 0.2)
    if spans and not usable:
        warnings.append(WarningItem(code="GARBLED_TEXT", message="Text layer looks garbled; OCR recommended",
                                    page_number=inp.page_number))
    # de-duplicate repeated warnings deterministically
    seen, uniq = set(), []
    for w in warnings:
        k = (w.code, w.page_number)
        if k not in seen:
            seen.add(k)
            uniq.append(w)
    out = NativeTextOutput(page_id=page_id(inp.source_id, inp.page_number), spans=spans,
                           has_usable_text=usable, warnings=uniq)
    store.put("native_text", out.page_id, out.model_dump(mode="json"))
    audit.append(event_type="native_text_run", object_type="page", object_id=out.page_id,
                 details={"spans": len(spans), "usable": usable})
    return out
