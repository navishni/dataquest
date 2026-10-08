"""Platform endpoints (owner: Person C): /config, /auth/*, /batches, /sources, /cases, /jobs, /metrics, /health/agents."""
import io
import os
import threading
import uuid
from datetime import datetime, timezone
from typing import Literal, Optional

from fastapi import APIRouter, Request
from fastapi.responses import Response
from PIL import Image, ImageDraw
from pydantic import BaseModel, ConfigDict

from .. import registry
from ..common import audit, auth, config, crypto, frontend_contract, jobs, metrics, notify
from ..common.envelope import fail, new_request_id
from ..common.errors import AgentError, ErrorCode
from ..common.store import store
from .support import _ip, _ua_family, call

router = APIRouter()
_users: dict[str, dict] = {}
_failed: dict[str, int] = {}
_ulock = threading.Lock()


def register_user(username: str, password: str, role: str) -> None:
    if role not in ("admin", "editor", "viewer") or config.get(f"roles.{role}") is None:
        raise AgentError(ErrorCode.INVALID_INPUT, "Unknown role")
    with _ulock:
        existing = _users.get(username)
        if existing is not None and existing["role"] != role:
            raise AgentError(ErrorCode.CONFLICT, "Each login name can belong to only one role")
        _users[username] = {"hash": crypto.hash_password(password), "role": role}


def bootstrap_admin() -> None:
    for role in ("admin", "editor", "viewer"):
        prefix = f"PF_{role.upper()}"
        username, password = os.environ.get(f"{prefix}_USER"), os.environ.get(f"{prefix}_PASSWORD")
        if username and password:
            register_user(username, password, role)


class LoginBody(BaseModel):
    model_config = ConfigDict(extra="forbid", repr=False)
    username: str
    password: str
    # Optional for older API clients. The web login always sends a role and the
    # backend verifies it against the role assigned to the authenticated account.
    role: Optional[Literal["admin", "editor", "viewer"]] = None


@router.post("/auth/login")
def login(request: Request, body: LoginBody):
    def work():
        rec = _users.get(body.username)
        ip, ua = _ip(request), _ua_family(request.headers.get("user-agent", ""))
        if rec is None or not crypto.verify_password(body.password, rec["hash"]):
            with _ulock:
                _failed[body.username] = _failed.get(body.username, 0) + 1
                n = _failed[body.username]
            audit.append(event_type="login_failure", object_type="user", object_id=body.username[:80], outcome="denied",
                         actor_id=body.username[:80], actor_role="none", ip_masked=ip, user_agent_family=ua, details={"attempts": n})
            if n >= config.get("notify.failed_login_threshold", 5):
                notify.send("login_failure", "high", "Repeated failed logins", f"user={body.username[:40]} attempts={n} ip={ip}",
                            dedupe_key=f"login_fail:{body.username}")
            raise AgentError(ErrorCode.FORBIDDEN, "Invalid credentials")
        if body.role is not None and body.role != rec["role"]:
            audit.append(event_type="login_role_mismatch", object_type="user", object_id=body.username[:80], outcome="denied",
                         actor_id=body.username[:80], actor_role=rec["role"], ip_masked=ip, user_agent_family=ua,
                         details={"selected_role": body.role})
            raise AgentError(ErrorCode.FORBIDDEN, "The selected role does not match this account. Choose the role assigned to your username.")
        with _ulock:
            _failed.pop(body.username, None)
        tok = auth.issue_token(body.username, rec["role"])
        audit.append(event_type="login_success", object_type="user", object_id=body.username, actor_id=body.username,
                     actor_role=rec["role"], ip_masked=ip, user_agent_family=ua)
        notify.send("login_success", "normal", "User login", f"user={body.username} role={rec['role']} ip={ip} agent={ua}")
        return {"access_token": tok, "token_type": "bearer", "role": rec["role"], "expires_in": 900}
    return call(request, work, public=True)


@router.post("/auth/logout")
def logout(request: Request):
    return call(request, lambda: (audit.append(event_type="logout", object_type="user", object_id=auth.current_user().user_id),
                                  {"logged_out": True})[1])


@router.get("/auth/me")
def me(request: Request):
    return call(request, lambda: auth.current_user().as_dict())


@router.get("/config")
def get_config(request: Request):
    return call(request, config.public, public=True)


# ---- batches / sources / cases ------------------------------------------------------------------
class BatchBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Optional[str] = None
    source_ids: list[str] = []
    mode: Optional[str] = None
    output_formats: list[str] = []
    case_id: Optional[str] = None
    instruction: Optional[str] = None
    options: dict[str, bool] = {}


class CaseBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str


class FindingReviewBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["acknowledge", "dismiss"]
    note: Optional[str] = None


def _owned(u: auth.User, rec: dict, key: str = "owner") -> bool:
    return u.has("admin") or rec.get("tenant_id", config.get("tenant_id", "default")) == u.tenant_id


@router.post("/batches")
def create_batch(request: Request, body: BatchBody):
    def work():
        u = auth.current_user()
        if not body.source_ids:
            raise AgentError(ErrorCode.INVALID_INPUT, "A batch needs at least one source")
        if body.case_id:
            case = store.get("case", body.case_id)
            if case is None:
                raise AgentError(ErrorCode.NOT_FOUND, "Case not found")
            if not _owned(u, case):
                raise AgentError(ErrorCode.FORBIDDEN, "Case not accessible")
        for sid in body.source_ids:
            m = store.get("source", sid)
            if m is None:
                raise AgentError(ErrorCode.NOT_FOUND, "Source not found")
            if not _owned(u, m, "uploaded_by"):
                raise AgentError(ErrorCode.FORBIDDEN, "Source not accessible")
        bid = uuid.uuid4().hex
        sources = []
        for sid in body.source_ids:
            meta = store.get("source", sid) or {}
            sources.append({"source_id": sid, "filename": meta.get("display_name", sid), "status": "queued",
                            "stage": "file_validation", "progress_percent": 0, "warnings": [], "errors": []})
        rec = {"batch_id": bid, "owner": u.user_id, "tenant_id": u.tenant_id, "name": body.name or bid,
               "source_ids": list(body.source_ids), "sources": sources, "case_id": body.case_id,
               "mode": body.mode, "output_formats": list(body.output_formats), "instruction": body.instruction,
               "options": dict(body.options), "created_at": datetime.now(timezone.utc).isoformat()}
        store.put("batch", bid, rec)
        try:
            audit.append(event_type="batch_created", object_type="batch", object_id=bid,
                         details={"source_count": len(body.source_ids), "case_id": body.case_id})
        except Exception:
            store.delete("batch", bid)
            raise

        from .. import pipeline
        rid, user = auth.request_id(), u

        def run_sources():
            auth.set_user(user)
            auth.set_request_id(rid)
            for i, sid in enumerate(body.source_ids):
                batch = store.get("batch", bid)
                batch["sources"][i].update(status="processing", stage="extraction", progress_percent=10)
                store.put("batch", bid, batch)
                try:
                    result = pipeline.run_pipeline(sid)
                    errs = [{"code": x.get("code", "ENGINE_FAILED"), "message": x.get("message", "Processing step failed")}
                            for x in result.get("steps", []) if not x.get("ok")]
                    batch = store.get("batch", bid)
                    batch["sources"][i].update(status=result.get("status", "failed"), stage="complete",
                                               progress_percent=100, errors=errs)
                except Exception:
                    batch = store.get("batch", bid)
                    batch["sources"][i].update(status="failed", stage="complete", progress_percent=100,
                                               errors=[{"code": "ENGINE_FAILED", "message": "Processing failed"}])
                store.put("batch", bid, batch)
            return {"batch_id": bid}

        jid = jobs.submit(run_sources, u.user_id)
        rec["job_id"] = jid
        store.put("batch", bid, rec)
        return {"batch_id": bid, "job_id": jid}
    return call(request, work, cap="upload")


@router.get("/batches")
def list_batches(request: Request):
    return call(request, lambda: [_batch_output(b) for b in store.list("batch") if _owned(auth.current_user(), b)], cap="view")


def _batch_output(batch: dict) -> dict:
    return {k: batch.get(k) for k in ("batch_id", "name", "case_id", "created_at", "job_id", "sources")}


@router.get("/batches/{batch_id}")
def get_batch(request: Request, batch_id: str):
    def work():
        batch = store.get("batch", batch_id)
        if batch is None or not _owned(auth.current_user(), batch):
            raise AgentError(ErrorCode.NOT_FOUND, "Batch not found")
        return _batch_output(batch)
    return call(request, work, cap="view")


@router.post("/batches/{batch_id}/sources")
def add_to_batch(request: Request, batch_id: str, body: BatchBody):
    def work():
        u = auth.current_user()
        b = store.get("batch", batch_id)
        if b is None:
            raise AgentError(ErrorCode.NOT_FOUND, "Batch not found")
        if not _owned(u, b):
            raise AgentError(ErrorCode.FORBIDDEN, "Batch not accessible")
        for sid in body.source_ids:
            m = store.get("source", sid)
            if m is None:
                raise AgentError(ErrorCode.NOT_FOUND, "Source not found")
            if not _owned(u, m, "uploaded_by"):
                raise AgentError(ErrorCode.FORBIDDEN, "Source not accessible")
            if sid not in b["source_ids"]:
                b["source_ids"].append(sid)
        store.put("batch", batch_id, b)
        return b
    return call(request, work, cap="upload")


def _public_meta(m: dict) -> dict:
    keep = ("source_id", "display_name", "sha256", "detected_mime", "size_bytes", "page_count", "uploaded_by", "origin")
    return {k: m.get(k) for k in keep}


def _source(sid: str) -> dict:
    u = auth.current_user()
    m = store.get("source", sid)
    if m is None or not _owned(u, m, "uploaded_by"):
        raise AgentError(ErrorCode.NOT_FOUND, "Source not found")
    return m


def _page_unit(sid: str, page_number: int, can_read_content: bool) -> dict:
    """Project stored extraction output into the frontend contract and redact locked content."""
    from ..agents import a23_access_control as access

    router_data = store.get("router", sid) or {}
    unit = next((x for x in router_data.get("units", []) if x.get("page_number") == page_number), None)
    if unit is None:
        raise AgentError(ErrorCode.NOT_FOUND, "Page not found")
    pid = f"{sid}-p{page_number}"
    layout = store.get("layout", pid) or {}
    reading = store.get("reading_order", pid) or {}
    coverage = store.get("consensus", f"{sid}:{page_number}") or {}
    doc = store.get("document", sid) or {}
    stored_page = next((p for p in doc.get("pages", []) if p.get("page_number") == page_number), {})
    width, height = stored_page.get("width"), stored_page.get("height")
    if not width or not height:
        with Image.open(io.BytesIO(store.page_image(sid, page_number))) as image:
            width, height = image.size
    blocks = []
    for b in stored_page.get("blocks", []):
        evidence = b.get("evidence") or {}
        location = evidence.get("location") or {}
        data = dict(b.get("data") or {}) if can_read_content else {}
        cells = data.get("cells")
        if isinstance(cells, list):
            data["cells"] = [{**cell, "raw_text": cell.get("raw_text", "") if can_read_content else "",
                              "normalized": cell.get("normalized") if can_read_content else None,
                              "locked": not can_read_content}
                             for cell in cells]
        projected = {
            **data,
            "block_id": b.get("block_id"), "type": b.get("type", "unknown"), "source_id": sid, "page_id": pid,
            "unit_id": unit.get("unit_id"), "virtual_page_number": b.get("virtual_page_number"),
            "reading_order_index": b.get("reading_order_index", 0),
            "location": {"bbox": location.get("bbox"), "coordinate_system": "pixel_top_left",
                         "page_width": location.get("page_width") or width, "page_height": location.get("page_height") or height,
                         "bbox_unavailable_reason": location.get("bbox_unavailable_reason")},
            "confidence": evidence.get("confidence", 0), "extraction_method": evidence.get("extraction_method", "unknown"),
            "raw_text": b.get("text") if can_read_content else None,
            "needs_review": bool((b.get("extra") or {}).get("needs_review", False)),
            "warnings": b.get("warnings") or data.get("warnings", []),
            "locked": not can_read_content, "masked": not can_read_content,
        }
        if not can_read_content:
            projected["type"] = "locked_content"
        blocks.append(projected)
    coverage_data = coverage.get("coverage") or {}
    uncovered_regions = []
    for region in coverage_data.get("uncovered_regions") or []:
        if not isinstance(region, dict):
            continue
        uncovered_regions.append({
            **region,
            "coordinate_system": "pixel_top_left",
            "page_width": region.get("page_width") or width,
            "page_height": region.get("page_height") or height,
        })
    return {
        "page_id": pid, "source_id": sid, "unit_id": unit.get("unit_id"), "page_number": page_number,
        "width": width, "height": height, "rotation": unit.get("rotation", 0),
        "layout_class": layout.get("layout_class", unit.get("page_class", "other")),
        "reading_order_confidence": reading.get("reading_order_confidence", 0),
        "coverage_score": coverage_data.get("coverage_score"),
        "uncovered_regions": uncovered_regions,
        "image_url": f"/sources/{sid}/pages/{page_number}/image", "blocks": blocks,
    }


def _redacted_image(sid: str, page_number: int) -> bytes:
    """Hide the full raster whenever any extracted content on it is locked."""
    image = Image.open(io.BytesIO(store.page_image(sid, page_number))).convert("RGB")
    ImageDraw.Draw(image).rectangle((0, 0, image.width, image.height), fill="black")
    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


@router.get("/sources/{source_id}")
def get_source(request: Request, source_id: str):
    def work():
        m = _source(source_id)
        document = store.get("document", source_id) or {}
        from ..agents import a23_access_control as access
        can_read_content = "text" in access.visible_columns(auth.current_user(), "blocks")
        warnings = [{key: value for key, value in warning.items() if value is not None}
                    if isinstance(warning, dict) else warning for warning in document.get("warnings", [])]
        warnings.extend({"code": "FILE_EXTENSION_MISMATCH", "message": msg} for msg in (m.get("ingestion_warnings") or []))
        errors = [{"code": "ENGINE_FAILED", "message": x} if isinstance(x, str) else x
                  for x in document.get("errors", [])]
        origin = m.get("origin") or {"type": "upload"}
        return {"source_id": source_id, "filename": m.get("display_name", source_id),
                "kind": m.get("detected_mime", "unknown"),
                "status": document.get("status") or store.get("source_status", source_id) or "registered",
                "sha256": m.get("sha256", ""), "size_bytes": m.get("size_bytes", 0),
                "page_count": m.get("page_count") or len((store.get("router", source_id) or {}).get("units", [])),
                "origin": origin,
                "document_confidence": (store.get("confidence", source_id) or {}).get("document_confidence"),
                "warnings": warnings, "errors": errors, "pages": None,
                "content_locked": not can_read_content}
    return call(request, work, cap="view")


@router.get("/sources/{source_id}/markdown")
def get_markdown(request: Request, source_id: str):
    def work():
        _source(source_id)
        md = store.get("markdown", source_id)
        if md is None:
            raise AgentError(ErrorCode.NOT_FOUND, "Markdown not available")
        from ..agents import a23_access_control as access
        if "text" not in access.visible_columns(auth.current_user(), "blocks"):
            return {"markdown": "Some page content is hidden based on your access."}
        return {"markdown": md}
    return call(request, work, cap="view")


@router.get("/sources/{source_id}/pages/{n}")
def get_page(request: Request, source_id: str, n: int):
    def work():
        _source(source_id)
        from ..agents import a23_access_control as access
        can_read_content = "text" in access.visible_columns(auth.current_user(), "blocks")
        return _page_unit(source_id, n, can_read_content)
    return call(request, work, cap="view")


def _binary(request: Request, loader, mime: str):
    from .support import authenticate
    rid = new_request_id()
    try:
        u = authenticate(request)
        auth.set_user(u)
        auth.set_request_id(rid)
        data = loader()
        audit.append(event_type="sensitive_content_viewed", object_type="binary", object_id=request.url.path[:200],
                     actor_id=u.user_id, actor_role=u.role, tenant_id=u.tenant_id, request_id=rid,
                     ip_masked=u.ip_masked or "", user_agent_family=u.user_agent_family or "",
                     details={"content_type": mime})
        return Response(content=data, media_type=mime, headers={"X-Request-Id": rid, "Cache-Control": "no-store"})
    except AgentError as e:
        return fail(e.code, e.message, rid)


@router.get("/sources/{source_id}/pages/{n}/image")
def page_image(request: Request, source_id: str, n: int):
    def load():
        _source(source_id)
        user = auth.require("view")
        from ..agents import a23_access_control as access
        if "text" in access.visible_columns(user, "blocks"):
            return store.page_image(source_id, n)
        return _redacted_image(source_id, n)
    return _binary(request, load, "image/png")


@router.get("/sources/{source_id}/crops/{crop_id}")
def crop(request: Request, source_id: str, crop_id: str):
    def load():
        _source(source_id)
        user = auth.require("view")
        from ..agents import a23_access_control as access
        if "text" not in access.visible_columns(user, "blocks"):
            raise AgentError(ErrorCode.FORBIDDEN, "Cropped content is hidden based on your access")
        c = store.get("crop", f"{source_id}:{crop_id}")
        if c is None:
            raise AgentError(ErrorCode.NOT_FOUND, "Crop not found")
        return c
    return _binary(request, load, "image/png")


@router.post("/cases")
def create_case(request: Request, body: CaseBody):
    def work():
        u = auth.current_user()
        cid = uuid.uuid4().hex[:16]
        rec = {"case_id": cid, "owner": u.user_id, "tenant_id": u.tenant_id, "name": body.name,
               "created_at": datetime.now(timezone.utc).isoformat(), "status": "open"}
        store.put("case", cid, rec)
        audit.append(event_type="case_created", object_type="case", object_id=cid, case_id=cid)
        return rec
    return call(request, work, cap="view")


@router.get("/cases")
def list_cases(request: Request):
    return call(request, lambda: [c for c in store.list("case") if _owned(auth.current_user(), c)], cap="view")


@router.get("/cases/{case_id}")
def get_case(request: Request, case_id: str):
    def work():
        c = store.get("case", case_id)
        if c is None or not _owned(auth.current_user(), c):
            raise AgentError(ErrorCode.NOT_FOUND, "Case not found")
        u = auth.current_user()
        from ..agents.a14_case_linker import case_links
        from ..agents import a23_access_control as access
        links = case_links(case_id, include_rejected=True)
        source_ids = sorted({link["source_id"] for link in links})
        sources = [{"source_id": sid, "filename": (store.get("source", sid) or {}).get("display_name", sid)}
                   for sid in source_ids if store.get("source", sid) is not None]
        facts = (store.get("facts", case_id) or {}).get("facts", [])
        fact_columns = set(access.visible_columns(u, "facts"))
        can_read_text = "text" in access.visible_columns(u, "blocks")
        facts = [frontend_contract.fact(f, visible_columns=fact_columns, redact_excerpt=not can_read_text) for f in facts]
        reasoning = store.get("reasoning", case_id) or {"comparisons": [], "not_comparable": [], "findings": []}
        finding_columns = set(access.visible_columns(u, "findings"))
        finding_rows = [f for f in store.list("finding") if f["case_id"] == case_id]
        restricted_reasoning = any(name not in fact_columns for name in
                                   ("fact_id", "source_id", "raw_value", "normalized_value", "subject", "metric", "currency",
                                    "frequency", "category", "basis", "unit", "period_start", "period_end", "confidence"))
        comparisons = [] if restricted_reasoning else [frontend_contract.comparison(c) for c in reasoning.get("comparisons", [])]
        comparison_by_facts = {tuple(sorted(c.get("fact_ids", []))): c for c in reasoning.get("comparisons", [])}
        findings = [frontend_contract.finding(
            f,
            comparison_row=comparison_by_facts.get(tuple(sorted(f.get("fact_ids", [])))),
            redact_details=restricted_reasoning,
            redact_excerpt=(not can_read_text or "raw_text" not in fact_columns or "evidence" not in fact_columns),
            visible_columns=finding_columns,
        ) for f in finding_rows]
        not_comparable = [] if restricted_reasoning else reasoning.get("not_comparable", [])
        if not can_read_text:
            links = [{**link, "matching_signals": []} for link in links]
        actions = [frontend_contract.action(a, u) for a in store.list("action")
                   if a["case_id"] == case_id] if u.has("draft_action") else []
        return {"case_id": c["case_id"], "name": c["name"], "status": c.get("status"), "created_at": c.get("created_at"),
                "sources": sources, "links": links, "facts": facts,
                "comparisons": comparisons, "not_comparable": not_comparable, "findings": findings,
                "actions": actions, "timeline": []}
    return call(request, work, cap="view")


@router.post("/cases/{case_id}/findings/{finding_id}/review")
def review_finding(request: Request, case_id: str, finding_id: str, body: FindingReviewBody):
    def work():
        case = store.get("case", case_id)
        if case is None or not _owned(auth.current_user(), case):
            raise AgentError(ErrorCode.NOT_FOUND, "Case not found")
        finding = store.get("finding", finding_id)
        if finding is None or finding.get("case_id") != case_id:
            raise AgentError(ErrorCode.NOT_FOUND, "Finding not found")
        if body.decision == "dismiss" and not (body.note or "").strip():
            raise AgentError(ErrorCode.INVALID_INPUT, "A note is required when dismissing a finding")
        if finding.get("status", "open") in ("acknowledged", "dismissed"):
            raise AgentError(ErrorCode.CONFLICT, "Finding has already been reviewed")
        u = auth.current_user()
        updated = {**finding,
                   "status": "acknowledged" if body.decision == "acknowledge" else "dismissed",
                   "reviewed_by": u.user_id, "reviewed_at": datetime.now(timezone.utc).isoformat(),
                   "review_note": (body.note or "").strip() or None}
        store.put("finding", finding_id, updated)
        try:
            audit.append(event_type=f"finding_{body.decision}d" if body.decision == "acknowledge" else "finding_dismissed",
                         object_type="finding", object_id=finding_id, case_id=case_id,
                         details={"note_present": bool(body.note and body.note.strip())})
        except Exception:
            store.put("finding", finding_id, finding)
            raise
        return {"finding_id": finding_id, "status": updated["status"]}
    return call(request, work, cap="analysis", body=body)


# ---- jobs / metrics / health ------------------------------------------------------------------------
@router.get("/jobs/{job_id}")
def get_job(request: Request, job_id: str):
    return call(request, lambda: jobs.get(job_id, auth.current_user().user_id))


@router.get("/metrics")
def get_metrics(request: Request):
    return call(request, metrics.snapshot, cap="admin")


@router.get("/health/agents")
def health_agents(request: Request):
    return call(request, registry.contract_check)
