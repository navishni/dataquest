"""In-memory store implementing the shared storage contract.

store.get_file(source_id) -> bytes        (decrypted on read; ciphertext at rest)
store.put_file(source_id, data, meta)     (platform/agent 01 only)
store.put(kind, id, obj) / store.get(kind, id) / store.list(kind)
store.page_image(source_id, page_number) -> PNG bytes (rendered at config DPI, cached encrypted)

Swap InMemoryStore for a DB/object-store implementation behind the same interface.
"""
import io
import threading
from typing import Any

from . import config, crypto
from .errors import AgentError, ErrorCode


class InMemoryStore:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._files: dict[str, bytes] = {}
        self._meta: dict[str, dict] = {}
        self._kv: dict[str, dict[str, Any]] = {}
        self._images: dict[tuple[str, int], bytes] = {}
        self._passwords: dict[str, str] = {}

    def reset(self) -> None:
        self.__init__()

    # files
    def put_file(self, source_id: str, data: bytes, meta: dict) -> None:
        with self._lock:
            if source_id in self._files:
                raise AgentError(ErrorCode.CONFLICT, "Original is immutable")
            self._files[source_id] = crypto.encrypt(data, source_id)
            self._meta[source_id] = dict(meta)
            self._kv.setdefault("source", {})[source_id] = dict(meta)

    def remove_file(self, source_id: str) -> None:
        """Rollback hook only (audit failed during upload). Not exposed through any API."""
        with self._lock:
            self._files.pop(source_id, None)
            self._meta.pop(source_id, None)
            self._kv.get("source", {}).pop(source_id, None)
            self._passwords.pop(source_id, None)

    def set_password(self, source_id: str, password: str) -> None:
        """Keep a file password in process memory only until extraction completes."""
        with self._lock:
            self._passwords[source_id] = password

    def get_password(self, source_id: str) -> str | None:
        with self._lock:
            return self._passwords.get(source_id)

    def clear_password(self, source_id: str) -> None:
        with self._lock:
            self._passwords.pop(source_id, None)

    def get_file(self, source_id: str) -> bytes:
        with self._lock:
            blob = self._files.get(source_id)
        if blob is None:
            raise AgentError(ErrorCode.NOT_FOUND, "Source not found")
        return crypto.decrypt(blob, source_id)

    def meta(self, source_id: str) -> dict:
        m = self._meta.get(source_id)
        if m is None:
            raise AgentError(ErrorCode.NOT_FOUND, "Source not found")
        return m

    def find_by_hash(self, sha256: str) -> str | None:
        with self._lock:
            for sid, m in sorted(self._meta.items()):
                if m.get("sha256") == sha256:
                    return sid
        return None

    # kv
    def put(self, kind: str, id: str, obj: Any) -> None:
        with self._lock:
            self._kv.setdefault(kind, {})[id] = obj

    def get(self, kind: str, id: str, default: Any = None) -> Any:
        with self._lock:
            return self._kv.get(kind, {}).get(id, default)

    def require(self, kind: str, id: str) -> Any:
        v = self.get(kind, id)
        if v is None:
            raise AgentError(ErrorCode.NOT_FOUND, f"{kind} not found")
        return v

    def list(self, kind: str) -> list[Any]:
        with self._lock:
            d = self._kv.get(kind, {})
            return [d[k] for k in sorted(d)]

    def delete(self, kind: str, id: str) -> None:
        with self._lock:
            self._kv.get(kind, {}).pop(id, None)

    # images
    def cache_page_image(self, source_id: str, page_number: int, png: bytes) -> None:
        with self._lock:
            self._images[(source_id, page_number)] = crypto.encrypt(png, f"{source_id}:{page_number}")

    def page_image(self, source_id: str, page_number: int) -> bytes:
        key = (source_id, page_number)
        with self._lock:
            cached = self._images.get(key)
        if cached is not None:
            return crypto.decrypt(cached, f"{source_id}:{page_number}")
        png = render_page(self.get_file(source_id), self.meta(source_id).get("detected_mime", ""),
                          page_number, password=self.get_password(source_id))
        with self._lock:
            self._images[key] = crypto.encrypt(png, f"{source_id}:{page_number}")
        return png


def render_page(data: bytes, mime: str, page_number: int, password: str | None = None) -> bytes:
    dpi = config.get("page_classification.render_dpi", 200)
    try:
        if mime == "application/pdf":
            import pypdfium2 as pdfium
            pdf = pdfium.PdfDocument(data, password=password)
            if page_number < 1 or page_number > len(pdf):
                raise AgentError(ErrorCode.NOT_FOUND, "Page not found")
            img = pdf[page_number - 1].render(scale=dpi / 72).to_pil()
        elif mime.startswith("image/"):
            from PIL import Image
            img = Image.open(io.BytesIO(data))
            if page_number < 1 or page_number > getattr(img, "n_frames", 1):
                raise AgentError(ErrorCode.NOT_FOUND, "Page not found")
            img.seek(page_number - 1)
            img = img.convert("RGB")
        else:
            raise AgentError(ErrorCode.UNSUPPORTED_FORMAT, "No page image for this format")
        out = io.BytesIO()
        img.convert("RGB").save(out, format="PNG")
        return out.getvalue()
    except AgentError:
        raise
    except Exception:  # noqa: BLE001
        raise AgentError(ErrorCode.ENGINE_FAILED, "Page render failed")


store = InMemoryStore()
