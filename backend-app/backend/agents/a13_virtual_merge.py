"""Agent 13 - Virtual Merge. Pure mapping; no pixels are merged.

Validates that the batch exists and the caller owns it (or is admin), every id exists and belongs to the batch, and ids
are unique. Pages are numbered 1..N across the given order; boundary_start is true on the first page of each source.
Page counts come from the router result, else the source's page_count, else 1. virtual_document_id =
sha256(canonical_json({batch, ordered ids}))[:16] so the same ordered list always returns the same id (idempotent).
The mapping is persisted and a side index (kind "virtual_index") records virtual_page_number per original page, so
original documents are never mutated.
"""
from pydantic import BaseModel, ConfigDict

from ..common import audit, auth, crypto
from ..common.errors import AgentError, ErrorCode
from ..common.store import store
from .a03_native_text import page_id


class MergeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    batch_id: str
    ordered_source_ids: list[str]


class VirtualPage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    virtual_page_number: int
    source_id: str
    page_number: int
    page_id: str
    boundary_start: bool


class MergeOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    virtual_document_id: str
    pages: list[VirtualPage]


def run(inp: MergeInput) -> MergeOutput:
    u = auth.current_user()
    batch = store.get("batch", inp.batch_id)
    if batch is None:
        raise AgentError(ErrorCode.NOT_FOUND, "Batch not found")
    if batch["owner"] != u.user_id and not u.has("admin"):
        raise AgentError(ErrorCode.FORBIDDEN, "Batch not accessible")
    if not inp.ordered_source_ids:
        raise AgentError(ErrorCode.INVALID_INPUT, "No sources given")
    if len(set(inp.ordered_source_ids)) != len(inp.ordered_source_ids):
        raise AgentError(ErrorCode.INVALID_INPUT, "Duplicate source ids")
    pages, vn = [], 0
    for sid in inp.ordered_source_ids:
        meta = store.get("source", sid)
        if meta is None:
            raise AgentError(ErrorCode.NOT_FOUND, "Source not found")
        if sid not in batch["source_ids"]:
            raise AgentError(ErrorCode.FORBIDDEN, "Source does not belong to batch")
        router = store.get("router", sid)
        n = len(router["units"]) if router else (meta.get("page_count") or 1)
        for p in range(1, n + 1):
            vn += 1
            pages.append(VirtualPage(virtual_page_number=vn, source_id=sid, page_number=p,
                                     page_id=page_id(sid, p), boundary_start=(p == 1)))
    vid = crypto.hash_obj({"batch": inp.batch_id, "ordered": inp.ordered_source_ids})[:16]
    out = MergeOutput(virtual_document_id=vid, pages=pages)
    store.put("virtual_doc", vid, out.model_dump(mode="json"))
    for p in pages:
        store.put("virtual_index", f"{p.source_id}:{p.page_number}",
                  {"virtual_document_id": vid, "virtual_page_number": p.virtual_page_number})
    audit.append(event_type="virtual_merged", object_type="virtual_document", object_id=vid,
                 details={"sources": len(inp.ordered_source_ids), "pages": len(pages)})
    return out
