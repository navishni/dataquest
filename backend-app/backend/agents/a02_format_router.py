"""Agent 02 - Format Router. Deterministic.

Route ids (published enum): pdf_native, pdf_scanned, pdf_mixed, pdf_blank, image, docx, pptx, xlsx, csv, eml, html.
page_class values: native_text, scanned, mixed, blank, image_only.

PDF per-page classification (thresholds in config.page_classification, never inline):
  chars = text-layer characters; image_ratio = union-bound of image bboxes / page area (capped 1).
  chars >= native_text_min_chars and image_ratio < image_area_scanned_ratio -> native_text
  chars >= mixed_text_min_chars and image_ratio >= image_area_scanned_ratio -> mixed
  chars < mixed_text_min_chars and image_ratio >= image_area_scanned_ratio -> scanned
  chars < mixed_text_min_chars and 0 < image_ratio < scanned_ratio        -> image_only
  chars between mixed_min and native_min with no images                   -> native_text
  nothing at all                                                          -> blank
Pages are read lazily (one at a time) so 1000-page PDFs are never fully loaded into page objects.
Rotation is read from the page /Rotate entry (Tesseract OSD is only applied by agent 04 on images).
EML: body unit + one unit per attachment; attachments registered as child sources
(origin.type="email_attachment", parent_source_id).
"""
import email
import hashlib
import io
import uuid
import zipfile
from email import policy
from typing import Optional

from pydantic import BaseModel, ConfigDict

from ..common import audit, auth, config
from ..common.crypto import sha256_hex
from ..common.errors import AgentError, ErrorCode
from ..common.models import Unit, WarningItem
from ..common.store import store
from .a01_file_validation import DOC, DOCX, PPT, PPTX, XLS, XLSX, sniff_mime

ROUTES = ["pdf_native", "pdf_scanned", "pdf_mixed", "pdf_blank", "image", "docx", "pptx", "xlsx", "csv", "eml", "html",
          "odt", "odp", "ods", "epub", "rtf", "text", "json", "xml", "legacy_doc", "legacy_ppt", "legacy_xls"]
PAGE_CLASSES = ["native_text", "scanned", "mixed", "blank", "image_only"]


class RouterInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_id: str


class RouterOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_id: str
    route: str
    units: list[Unit]
    warnings: list[WarningItem] = []
    rotations: dict[int, int] = {}
    child_source_ids: list[str] = []


def unit_id(source_id: str, page_number: int) -> str:
    return hashlib.sha256(f"{source_id}:{page_number}".encode()).hexdigest()[:16]


def classify_page(chars: int, image_ratio: float) -> str:
    c = config.get("page_classification")
    scanned = image_ratio >= c["image_area_scanned_ratio"]
    if chars == 0 and image_ratio == 0:
        return "blank"
    if scanned:
        return "mixed" if chars >= c["mixed_text_min_chars"] else "scanned"
    if chars >= c["mixed_text_min_chars"]:
        return "native_text"
    return "image_only" if image_ratio > 0 else "blank"


def _pdf_units(data: bytes, source_id: str, password: Optional[str] = None):
    import pymupdf
    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception:  # noqa: BLE001
        raise AgentError(ErrorCode.CORRUPT_FILE, "PDF is corrupt")
    if doc.needs_pass:
        if not password or not doc.authenticate(password):
            raise AgentError(ErrorCode.PASSWORD_REQUIRED, "Password-protected PDF cannot be routed")
    units, rot = [], {}
    for i in range(len(doc)):
        page = doc[i]
        chars = len(page.get_text("text").strip())
        area = max(page.rect.width * page.rect.height, 1.0)
        img_area = 0.0
        for info in page.get_image_info():
            r = pymupdf.Rect(info["bbox"]) & page.rect
            img_area += r.width * r.height if not r.is_empty else 0
        units.append(Unit(unit_id=unit_id(source_id, i + 1), page_number=i + 1,
                          page_class=classify_page(chars, min(img_area / area, 1.0))))
        if page.rotation:
            rot[i + 1] = page.rotation
    return units, rot


def run(inp: RouterInput) -> RouterOutput:
    meta = store.meta(inp.source_id)
    data = store.get_file(inp.source_id)
    password = store.get_password(inp.source_id)
    mime, warnings, rot, children = meta["detected_mime"], [], {}, []
    if mime == "application/pdf":
        units, rot = _pdf_units(data, inp.source_id, password)
        classes = {u.page_class for u in units if u.page_class != "blank"}
        if not classes:
            route = "pdf_blank"
        elif classes <= {"native_text"}:
            route = "pdf_native"
        elif classes <= {"scanned", "image_only"}:
            route = "pdf_scanned"
        else:
            route = "pdf_mixed"
        for u in units:
            if u.page_class == "blank":
                warnings.append(WarningItem(code="BLANK_PAGE", message="Page has no content", page_number=u.page_number))
    elif mime.startswith("image/"):
        route = "image"
        count = max(meta.get("page_count") or 1, 1)
        units = [Unit(unit_id=unit_id(inp.source_id, i), page_number=i, page_class="scanned") for i in range(1, count + 1)]
    elif mime == PPTX:
        route = "pptx"
        n = max(meta.get("page_count") or 1, 1)
        units = [Unit(unit_id=unit_id(inp.source_id, i), page_number=i, page_class="native_text") for i in range(1, n + 1)]
    elif mime == DOCX:
        route = "docx"
        units = [Unit(unit_id=unit_id(inp.source_id, 1), page_number=1, page_class="native_text")]
        warnings.append(WarningItem(code="PAGE_COUNT_UNKNOWN", message="DOCX pagination requires rendering; one unit emitted"))
    elif mime == XLSX:
        route = "xlsx"
        n = max(meta.get("page_count") or 1, 1)
        units = [Unit(unit_id=unit_id(inp.source_id, i), page_number=i, page_class="native_text") for i in range(1, n + 1)]
    elif mime == "text/csv":
        route, units = "csv", [Unit(unit_id=unit_id(inp.source_id, 1), page_number=1, page_class="native_text")]
    elif mime == "text/html":
        route, units = "html", [Unit(unit_id=unit_id(inp.source_id, 1), page_number=1, page_class="native_text")]
    elif mime == "application/vnd.oasis.opendocument.text":
        route, units = "odt", [Unit(unit_id=unit_id(inp.source_id, 1), page_number=1, page_class="native_text")]
    elif mime == "application/vnd.oasis.opendocument.presentation":
        route = "odp"
        count = max(meta.get("page_count") or 1, 1)
        units = [Unit(unit_id=unit_id(inp.source_id, i), page_number=i, page_class="native_text") for i in range(1, count + 1)]
    elif mime == "application/vnd.oasis.opendocument.spreadsheet":
        route = "ods"
        count = max(meta.get("page_count") or 1, 1)
        units = [Unit(unit_id=unit_id(inp.source_id, i), page_number=i, page_class="native_text") for i in range(1, count + 1)]
    elif mime == "application/epub+zip":
        route, units = "epub", [Unit(unit_id=unit_id(inp.source_id, 1), page_number=1, page_class="native_text")]
    elif mime == "text/rtf":
        route, units = "rtf", [Unit(unit_id=unit_id(inp.source_id, 1), page_number=1, page_class="native_text")]
    elif mime in ("text/plain", "text/markdown"):
        route, units = "text", [Unit(unit_id=unit_id(inp.source_id, 1), page_number=1, page_class="native_text")]
    elif mime in ("application/json", "application/xml"):
        route, units = ("json" if mime == "application/json" else "xml"), [Unit(unit_id=unit_id(inp.source_id, 1), page_number=1, page_class="native_text")]
    elif mime == DOC:
        route, units = "legacy_doc", [Unit(unit_id=unit_id(inp.source_id, 1), page_number=1, page_class="native_text")]
    elif mime == PPT:
        route = "legacy_ppt"
        count = max(meta.get("page_count") or 1, 1)
        units = [Unit(unit_id=unit_id(inp.source_id, i), page_number=i, page_class="native_text") for i in range(1, count + 1)]
    elif mime == XLS:
        route, units = "legacy_xls", [Unit(unit_id=unit_id(inp.source_id, 1), page_number=1, page_class="native_text")]
    elif mime == "message/rfc822":
        route = "eml"
        units = [Unit(unit_id=unit_id(inp.source_id, 1), page_number=1, page_class="native_text")]
        msg = email.message_from_bytes(data, policy=policy.default)
        for att in sorted(msg.iter_attachments(), key=lambda a: a.get_filename() or ""):
            payload = att.get_payload(decode=True) or b""
            amime = sniff_mime(payload, (att.get_filename() or "").rsplit(".", 1)[-1].lower())
            if not payload or amime not in ("application/pdf", "image/png", "image/jpeg", DOCX, PPTX, XLSX, "text/csv"):
                warnings.append(WarningItem(code="ATTACHMENT_SKIPPED", message="Attachment type not supported"))
                continue
            cid = str(uuid.uuid5(uuid.UUID(inp.source_id), sha256_hex(payload)))
            if store.get("source", cid) is None:
                store.put_file(cid, payload, {
                    "source_id": cid, "display_name": (att.get_filename() or "attachment")[:200],
                    "filename": (att.get_filename() or "attachment")[:120], "sha256": sha256_hex(payload),
                    "detected_mime": amime, "size_bytes": len(payload), "uploaded_by": meta.get("uploaded_by"),
                    "tenant_id": meta.get("tenant_id"),
                    "origin": {"type": "email_attachment", "parent_source_id": inp.source_id}})
            children.append(cid)
    else:
        raise AgentError(ErrorCode.UNSUPPORTED_FORMAT, "No route for this format")
    out = RouterOutput(source_id=inp.source_id, route=route, units=units, warnings=warnings,
                       rotations=rot, child_source_ids=children)
    store.put("router", inp.source_id, out.model_dump(mode="json"))
    audit.append(event_type="format_router_run", object_type="source", object_id=inp.source_id,
                 details={"route": route, "units": len(units)})
    return out
