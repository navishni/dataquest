"""Agent 01 - File Validation (gatekeeper).

Deterministic. Accept or reject an uploaded file safely and register it.
Algorithm: size check -> SHA-256 -> magic-byte MIME sniff (never the extension) -> extension/content
mismatch check -> safety (executables, VBA macros, zip bombs by ratio/depth/total, PDF JavaScript/Launch,
image decompression bombs) -> encryption handling (password memory-only) -> page count -> duplicate lookup ->
encrypted persistence (common/crypto via store) -> audit + ntfy for safety rejections.
Rejections are returned as status="rejected" with error {code,message}; the router maps them to the error
envelope. Fail closed: if the audit write fails the stored original is removed and ENGINE_FAILED is raised.
"""
import io
import re
import unicodedata
import uuid
import zipfile
from typing import Optional

from pydantic import BaseModel, ConfigDict

from ..common import audit, auth, config, notify
from ..common.crypto import sha256_hex
from ..common.errors import AgentError, ErrorCode
from ..common.store import store

EXT_FOR_MIME = {
    "application/pdf": {"pdf"},
    "image/png": {"png"}, "image/jpeg": {"jpg", "jpeg"}, "image/tiff": {"tif", "tiff"},
    "image/gif": {"gif"}, "image/bmp": {"bmp"}, "image/webp": {"webp"},
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": {"docx"},
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": {"pptx"},
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": {"xlsx"},
    "text/csv": {"csv", "tsv"}, "text/html": {"html", "htm"}, "message/rfc822": {"eml"},
    "application/vnd.oasis.opendocument.text": {"odt"},
    "application/vnd.oasis.opendocument.presentation": {"odp"},
    "application/vnd.oasis.opendocument.spreadsheet": {"ods"},
    "application/epub+zip": {"epub"}, "text/rtf": {"rtf"},
    "text/plain": {"txt", "text"}, "text/markdown": {"md", "markdown"},
    "application/json": {"json"}, "application/xml": {"xml"},
    "application/msword": {"doc"}, "application/vnd.ms-powerpoint": {"ppt"},
    "application/vnd.ms-excel": {"xls"},
}
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
PPTX = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
DOC = "application/msword"
PPT = "application/vnd.ms-powerpoint"
XLS = "application/vnd.ms-excel"
SAFETY_CODES = {"EXECUTABLE", "MACRO", "ZIP_BOMB", "PDF_ACTIVE_CONTENT", "IMAGE_BOMB"}


class Options(BaseModel):
    model_config = ConfigDict(extra="forbid", repr=False)
    password: Optional[str] = None  # memory only; never stored/logged/audited


class FileValidationInput(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)
    filename: str
    data: bytes
    options: Optional[Options] = None


class RejectError(BaseModel):
    code: str
    message: str
    reason: Optional[str] = None


class FileValidationOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_id: Optional[str] = None
    sanitized_filename: str
    sha256: str
    detected_mime: Optional[str] = None
    size_bytes: int
    page_count: Optional[int] = None
    status: str
    error: Optional[RejectError] = None
    duplicate_of: Optional[str] = None
    warnings: list[str] = []


def sanitize_filename(name: str) -> str:
    name = unicodedata.normalize("NFKC", name or "")
    name = re.split(r"[\\/]", name)[-1]
    name = "".join(c for c in name if unicodedata.category(c)[0] != "C")
    name = re.sub(r"[^\w.\- ()]", "_", name).strip(" .")
    if len(name) > 120:
        stem, _, ext = name.rpartition(".")
        name = (stem[:100] + "." + ext[:10]) if stem else name[:120]
    return name or "file"


def _ext(name: str) -> str:
    return name.rsplit(".", 1)[-1].lower() if "." in name else ""


def sniff_mime(data: bytes, ext: str) -> Optional[str]:
    h = data[:16]
    if h.startswith(b"%PDF"):
        return "application/pdf"
    if h.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if h[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if h[:4] in (b"II*\x00", b"MM\x00*"):
        return "image/tiff"
    if h[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if h[:2] == b"BM":
        return "image/bmp"
    if h[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if h[:2] == b"MZ" or h[:4] == b"\x7fELF" or h[:2] == b"#!":
        return "application/x-executable"
    if h[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
        return {"doc": "application/msword", "xls": "application/vnd.ms-excel",
                "ppt": "application/vnd.ms-powerpoint"}.get(ext, "application/x-ole")
    if h[:4] == b"PK\x03\x04":
        try:
            zf = zipfile.ZipFile(io.BytesIO(data))
            names = zf.namelist()
        except zipfile.BadZipFile:
            return "application/zip-corrupt"
        if "mimetype" in names:
            try:
                container_type = zf.read("mimetype").decode("ascii", errors="ignore").strip()
                if container_type in {"application/epub+zip", "application/vnd.oasis.opendocument.text",
                                      "application/vnd.oasis.opendocument.presentation",
                                      "application/vnd.oasis.opendocument.spreadsheet"}:
                    return container_type
            except (KeyError, OSError, RuntimeError):
                pass
        if any(n.startswith("word/") for n in names):
            return DOCX
        if any(n.startswith("ppt/") for n in names):
            return PPTX
        if any(n.startswith("xl/") for n in names):
            return XLSX
        return "application/zip"
    head = data[:2048]
    if b"\x00" in head:
        return None
    try:
        txt = data[:65536].decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        try:
            txt = data[:65536].decode("latin-1")
        except Exception:  # noqa: BLE001
            return None
    low = txt.lstrip().lower()
    if low.startswith("{\\rtf"):
        return "text/rtf"
    if low.startswith(("<!doctype html", "<html")):
        return "text/html"
    if low.startswith("<?xml") or (low.startswith("<") and ext == "xml"):
        return "application/xml"
    if re.match(r"(?im)^(from|received|mime-version|return-path|date|subject):", txt.lstrip()):
        return "message/rfc822"
    if ext in ("csv", "tsv") and txt.strip():
        return "text/csv"
    if ext in ("md", "markdown") and txt.strip():
        return "text/markdown"
    if ext == "json" and txt.strip():
        try:
            import json
            json.loads(txt)
            return "application/json"
        except (ValueError, TypeError):
            return None
    if ext in ("txt", "text") and txt.strip():
        return "text/plain"
    # Identify readable text even when the extension is wrong so users get a useful mismatch error.
    if txt.strip() and sum(ch.isprintable() or ch in "\r\n\t" for ch in txt) / len(txt) >= 0.96:
        return "text/plain"
    return None


def zip_safety(data: bytes, depth: int = 0) -> Optional[str]:
    lim = config.get("limits")
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        return None
    total = 0
    for info in zf.infolist():
        total += info.file_size
        if info.compress_size and info.file_size / max(info.compress_size, 1) > lim["max_zip_ratio"] \
                and info.file_size > 1_000_000:
            return "ZIP_BOMB"
        if total > lim["max_zip_uncompressed_bytes"]:
            return "ZIP_BOMB"
        if info.filename.lower().endswith((".zip", ".docx", ".xlsx", ".pptx")):
            if depth + 1 > lim["max_zip_depth"]:
                return "ZIP_BOMB"
            if info.file_size < lim["max_zip_uncompressed_bytes"]:
                try:
                    r = zip_safety(zf.read(info), depth + 1)
                except Exception:  # noqa: BLE001
                    r = None
                if r:
                    return r
    return None


def _reject(code: ErrorCode, msg: str, reason: Optional[str], name: str, sha: str, mime: Optional[str],
            size: int) -> FileValidationOutput:
    return FileValidationOutput(sanitized_filename=name, sha256=sha, detected_mime=mime, size_bytes=size,
                                status="rejected", error=RejectError(code=code.value, message=msg, reason=reason))


def _pdf_pages(data: bytes, password: Optional[str]) -> int:
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError
    try:
        if b"%%EOF" not in data[-4096:]:
            raise AgentError(ErrorCode.CORRUPT_FILE, "PDF is truncated (missing end marker)")
        r = PdfReader(io.BytesIO(data))
        if r.is_encrypted:
            if not password:
                raise AgentError(ErrorCode.PASSWORD_REQUIRED, "Password required")
            if not r.decrypt(password):
                raise AgentError(ErrorCode.PASSWORD_REQUIRED, "Password incorrect")
        return len(r.pages)
    except AgentError:
        raise
    except (PdfReadError, ValueError, KeyError, OSError, AttributeError, TypeError):
        raise AgentError(ErrorCode.CORRUPT_FILE, "PDF is corrupt or truncated")


def _pdf_has_active_content(data: bytes, password: Optional[str]) -> bool:
    """Inspect PDF action dictionaries instead of searching compressed bytes for token-like strings."""
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(data))
    if reader.is_encrypted and (not password or not reader.decrypt(password)):
        return False  # _pdf_pages returns the actionable password error.
    pending = [reader.trailer.get("/Root")]
    seen: set[tuple] = set()
    while pending:
        obj = pending.pop()
        ref = getattr(obj, "indirect_reference", None)
        if ref is not None:
            key = (ref.idnum, ref.generation)
            if key in seen:
                continue
            seen.add(key)
        try:
            obj = obj.get_object()
        except (AttributeError, TypeError):
            pass
        if isinstance(obj, dict):
            action = str(obj.get("/S", ""))
            if action in ("/JavaScript", "/Launch"):
                return True
            if action and ("/JS" in obj or "/JavaScript" in obj):
                return True
            if "/JavaScript" in obj and "/Names" in obj:
                return True
            pending.extend(obj.values())
        elif isinstance(obj, (list, tuple)):
            pending.extend(obj)
    return False


def _count_pages(data: bytes, mime: str, password: Optional[str]) -> Optional[int]:
    if mime == "application/pdf":
        return _pdf_pages(data, password)
    if mime.startswith("image/"):
        from PIL import Image
        with Image.open(io.BytesIO(data)) as image:
            return int(getattr(image, "n_frames", 1))
    if mime in (PPTX, XLSX, DOCX):
        try:
            zf = zipfile.ZipFile(io.BytesIO(data))
            names = zf.namelist()
            if mime == PPTX:
                return len([n for n in names if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)])
            if mime == XLSX:
                return len(re.findall(rb"<sheet\s", zf.read("xl/workbook.xml")))
            return None
        except (zipfile.BadZipFile, KeyError):
            raise AgentError(ErrorCode.CORRUPT_FILE, "Office file is corrupt")
    if mime in ("application/vnd.oasis.opendocument.text", "application/vnd.oasis.opendocument.presentation",
                "application/vnd.oasis.opendocument.spreadsheet"):
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                xml = zf.read("content.xml")
            if mime.endswith("spreadsheet"):
                return max(1, len(re.findall(rb"<table:table(?:\s|>)", xml)))
            if mime.endswith("presentation"):
                return max(1, len(re.findall(rb"<draw:page(?:\s|>)", xml)))
            return 1
        except (zipfile.BadZipFile, KeyError):
            raise AgentError(ErrorCode.CORRUPT_FILE, "OpenDocument file is corrupt")
    return 1 if mime == "text/csv" else None


def _check_image(data: bytes) -> None:
    from PIL import Image
    Image.MAX_IMAGE_PIXELS = config.get("limits.max_image_pixels")
    try:
        im = Image.open(io.BytesIO(data))
        w, h = im.size
        if w * h > Image.MAX_IMAGE_PIXELS:
            raise AgentError(ErrorCode.TOO_LARGE, "Image dimensions exceed limit", "IMAGE_BOMB")
        im.verify()
    except AgentError:
        raise
    except Image.DecompressionBombError:
        raise AgentError(ErrorCode.TOO_LARGE, "Image dimensions exceed limit", "IMAGE_BOMB")
    except Exception:  # noqa: BLE001
        raise AgentError(ErrorCode.CORRUPT_FILE, "Image is corrupt")


def run(inp: FileValidationInput) -> FileValidationOutput:
    user = auth.current_user()
    data = inp.data
    name = sanitize_filename(inp.filename)
    sha = sha256_hex(data)
    size = len(data)
    lim = config.get("limits")
    mime: Optional[str] = None

    def finish_reject(code, msg, reason=None):
        out = _reject(code, msg, reason, name, sha, mime, size)
        audit.append(event_type="file_rejected", object_type="file", object_id=sha,
                     outcome="denied", details={"code": code.value, "reason": reason, "size": size})
        if reason in SAFETY_CODES:
            notify.send("file_rejected", "high", "File rejected by safety checks",
                        f"user={user.user_id} reason={reason} sha256={sha[:12]}", dedupe_key=None)
        return out

    if size == 0:
        return finish_reject(ErrorCode.EMPTY_FILE, "File is empty")
    if size > lim["max_size_bytes"]:
        return finish_reject(ErrorCode.TOO_LARGE, "File exceeds size limit")

    ext = _ext(name)
    mime = sniff_mime(data, ext)
    if mime is None:
        return finish_reject(ErrorCode.UNSUPPORTED_FORMAT, "Unrecognised file content")
    if mime == "application/x-executable":
        return finish_reject(ErrorCode.UNSUPPORTED_FORMAT, "Executable content is not accepted", "EXECUTABLE")
    if mime in ("application/x-ole", "application/zip"):
        return finish_reject(ErrorCode.UNSUPPORTED_FORMAT, "Unsupported container format", "MACRO" if mime.endswith("ole") else None)
    if mime == "application/zip-corrupt":
        return finish_reject(ErrorCode.CORRUPT_FILE, "Archive is corrupt")
    mismatch_warnings = []
    if ext and ext not in EXT_FOR_MIME.get(mime, set()):
        # Strong binary signatures win over a misleading filename; weak text guesses do not.
        if mime in ("application/pdf", DOCX, PPTX, XLSX, "application/epub+zip",
                    "application/vnd.oasis.opendocument.text", "application/vnd.oasis.opendocument.presentation",
                    "application/vnd.oasis.opendocument.spreadsheet") or mime.startswith("image/"):
            mismatch_warnings.append(f"FILE_EXTENSION_MISMATCH: detected {mime} from file contents; filename ends in .{ext}")
        else:
            return finish_reject(ErrorCode.FORMAT_MISMATCH, "File extension does not match readable file content", "EXT_MISMATCH")

    try:
        if mime in (DOCX, PPTX, XLSX, "application/vnd.oasis.opendocument.text",
                    "application/vnd.oasis.opendocument.presentation",
                    "application/vnd.oasis.opendocument.spreadsheet", "application/epub+zip"):
            zf = zipfile.ZipFile(io.BytesIO(data))
            if any("vbaproject.bin" in n.lower() for n in zf.namelist()) or ext in ("docm", "xlsm", "pptm"):
                return finish_reject(ErrorCode.UNSUPPORTED_FORMAT, "Macro-enabled Office files are not accepted", "MACRO")
            if zip_safety(data):
                return finish_reject(ErrorCode.TOO_LARGE, "Archive expands beyond safe limits", "ZIP_BOMB")
        password = inp.options.password if inp.options else None
        if mime.startswith("image/"):
            _check_image(data)
        pages = _count_pages(data, mime, password)
        if mime == "application/pdf" and _pdf_has_active_content(data, password):
            return finish_reject(ErrorCode.UNSUPPORTED_FORMAT, "PDF contains active content", "PDF_ACTIVE_CONTENT")
        del password
    except AgentError as e:
        return finish_reject(e.code, e.message, e.details)

    if pages is not None and pages > lim["max_pages"]:
        return finish_reject(ErrorCode.TOO_LARGE, "Page count exceeds limit")

    dup = store.find_by_hash(sha)
    source_id = str(uuid.uuid4())
    store.put_file(source_id, data, {
        "source_id": source_id, "display_name": name,
        "filename": name, "sha256": sha, "detected_mime": mime, "size_bytes": size, "page_count": pages,
        "uploaded_by": user.user_id, "tenant_id": user.tenant_id, "origin": {"type": "upload"},
        "password_protected": bool(inp.options and inp.options.password), "ingestion_warnings": mismatch_warnings})
    if inp.options and inp.options.password:
        store.set_password(source_id, inp.options.password)
    try:
        audit.append(event_type="file_uploaded", object_type="source", object_id=source_id,
                     details={"sha256": sha, "mime": mime, "size": size, "pages": pages, "duplicate_of": dup})
    except AgentError:
        store.remove_file(source_id)
        raise
    return FileValidationOutput(source_id=source_id, sanitized_filename=name, sha256=sha, detected_mime=mime,
                                size_bytes=size, page_count=pages, status="accepted", duplicate_of=dup,
                                warnings=mismatch_warnings)
