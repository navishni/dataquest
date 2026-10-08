"""Thin HTTP routers for the 24 agents. Business logic lives in backend/agents/aNN_*.py (pure functions)."""
from typing import Optional

from fastapi import APIRouter, File, Form, Query, Request, UploadFile
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict

from .. import pipeline
from ..agents import (a01_file_validation as a01, a02_format_router as a02, a03_native_text as a03, a04_ocr as a04,
                      a05_layout as a05, a06_reading_order as a06, a07_table as a07, a08_spreadsheet as a08,
                      a09_chart_figure as a09, a10_equation as a10, a11_json_assembly as a11,
                      a12_confidence_validation as a12, a13_virtual_merge as a13, a14_case_linker as a14,
                      a15_fact_normalizer as a15, a16_cross_doc_reasoning as a16, a17_action_draft as a17,
                      a18_human_approval as a18, a19_audit as a19, a20_export as a20, a21_consensus as a21,
                      a22_url_guard as a22, a23_access_control as a23, a24_chat_sql as a24)
from ..common import auth, config, frontend_contract, jobs
from ..common.envelope import fail, new_request_id
from ..common.errors import AgentError, ErrorCode
from .support import call

router = APIRouter()


def _simple(path: str, model, fn, cap: Optional[str] = None, status: int = 200):
    def endpoint(request: Request, body: model):  # type: ignore[valid-type]
        return call(request, lambda: fn(body), cap=cap, body=body, status=status)
    router.add_api_route(path, endpoint, methods=["POST"], name=path)


# ---- 01 (multipart) -------------------------------------------------------------------------
@router.post("/agents/file-validation")
def file_validation(request: Request, file: UploadFile = File(...), password: Optional[str] = Form(None)):
    limit = config.get("limits.max_size_bytes")

    def work():
        buf, total = bytearray(), 0
        while True:
            chunk = file.file.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            buf += chunk
            if total > limit:
                raise AgentError(ErrorCode.TOO_LARGE, "File exceeds size limit")
        out = a01.run(a01.FileValidationInput(filename=file.filename or "file", data=bytes(buf),
                                              options=a01.Options(password=password) if password else None))
        if out.status == "rejected":
            raise AgentError(ErrorCode(out.error.code), out.error.message, out.error.reason)
        return out
    return call(request, work, cap="upload")


for _p, _m, _f, _c in [
    ("/agents/format-router", a02.RouterInput, a02.run, "upload"),
    ("/agents/native-text", a03.NativeTextInput, a03.run, "upload"),
    ("/agents/ocr", a04.OcrInput, a04.run, "upload"),
    ("/agents/layout", a05.LayoutInput, a05.run, "upload"),
    ("/agents/reading-order", a06.ReadingOrderInput, a06.run, "upload"),
    ("/agents/table", a07.TableInput, a07.run, "upload"),
    ("/agents/spreadsheet", a08.SpreadsheetInput, a08.run, "upload"),
    ("/agents/chart-figure", a09.ChartFigureInput, a09.run, "upload"),
    ("/agents/equation", a10.EquationInput, a10.run, "upload"),
    ("/agents/json-assembly", a11.AssemblyInput, a11.run, "upload"),
    ("/agents/confidence-validation", a12.ConfidenceInput, a12.run, "analysis"),
    ("/agents/virtual-merge", a13.MergeInput, a13.run, "upload"),
    ("/agents/case-linker", a14.LinkerInput, a14.run, "analysis"),
    ("/agents/case-linker/decision", a14.DecisionInput, a14.decide, "analysis"),
    ("/agents/export", a20.ExportInput, a20.run, "export"),
    ("/agents/consensus", a21.ConsensusInput, a21.run, "upload"),
    ("/agents/url-ingest", a22.UrlInput, a22.run, "upload"),
    ("/agents/chat-sql", a24.ChatInput, a24.run, None),
    ("/agents/chat-sql/approve", a24.ApproveInput, a24.approve, None),
]:
    _simple(_p, _m, _f, _c)


@router.post("/agents/fact-normalizer")
def fact_normalizer(request: Request, body: a15.FactInput):
    def shape():
        result = a15.run(body)
        return {"facts": [frontend_contract.fact(f.model_dump(mode="json")) for f in result.facts]}
    return call(request, shape, cap="analysis", body=body)


@router.post("/agents/cross-doc-reasoning")
def cross_doc_reasoning(request: Request, body: a16.ReasoningInput):
    def shape():
        result = a16.run(body).model_dump(mode="json")
        comparisons = [frontend_contract.comparison(c) for c in result["comparisons"]]
        comp_by_facts = {tuple(sorted(c.get("fact_ids", []))): c for c in result["comparisons"]}
        findings = [frontend_contract.finding(f, comparison_row=comp_by_facts.get(tuple(sorted(f.get("fact_ids", [])))))
                    for f in result["findings"]]
        return {**result, "comparisons": comparisons, "findings": findings}
    return call(request, shape, cap="analysis", body=body)


@router.post("/agents/action-draft")
def action_draft(request: Request, body: a17.DraftInput):
    def shape():
        result = a17.run(body)
        return frontend_contract.action(result.model_dump(mode="json"))
    return call(request, shape, cap="draft_action", body=body)


@router.post("/agents/human-approval")
def human_approval(request: Request, body: a18.ApprovalInput):
    def shape():
        result = a18.run(body)
        actor = auth.current_user()
        raw_event = result.event.model_dump(mode="json")
        return {
            "action": frontend_contract.action(result.action.model_dump(mode="json"), actor),
            "event": frontend_contract.approval_event(raw_event, actor.role, body.notes),
            "signature": frontend_contract.signature(result.signature, actor.user_id, raw_event.get("timestamp")),
        }
    return call(request, shape, body=body)


@router.post("/pipeline/run")
def run_pipeline(request: Request, body: a02.RouterInput):
    return call(request, lambda: pipeline.run_pipeline(body.source_id), cap="upload", body=body)


# ---- GET endpoints -------------------------------------------------------------------------
@router.get("/agents/audit")
def audit_read(request: Request, from_: Optional[str] = Query(None, alias="from"), to: Optional[str] = None,
               actor: Optional[str] = None, event_type: Optional[str] = None, case_id: Optional[str] = None,
               object_id: Optional[str] = None, cursor: Optional[str] = None, limit: int = 100):
    q = a19.AuditQuery(from_=from_, to=to, actor=actor, event_type=event_type, case_id=case_id, object_id=object_id,
                       cursor=cursor, limit=limit)
    return call(request, lambda: a19.run(q), cap="audit")


@router.post("/agents/audit/verify")
def audit_verify(request: Request):
    return call(request, lambda: a19.verify_job(), cap="admin")


@router.get("/agents/access/schema")
def access_schema(request: Request):
    def shape():
        result = a23.schema()
        return {"tables": [{
            "resource_id": resource.resource,
            "schema": config.get("access.schema_name", "analytics"),
            "name": resource.resource,
            "locked": bool(resource.columns) and all(c.locked for c in resource.columns),
            "columns": [{"name": c.name, "locked": c.locked, "hidden_for_viewers": c.hidden_for_viewers}
                        for c in resource.columns],
        } for resource in result.resources]}
    return call(request, shape, cap="view")


@router.get("/agents/access/preview")
def access_preview(request: Request, resource_id: str, limit: int = 10):
    def shape():
        result = a23.preview(a23.PreviewInput(resource=resource_id, limit=limit))
        return {"columns": [{"name": name, "locked": name in result.locked_columns} for name in result.columns],
                "rows": [[None if name in result.locked_columns else row.get(name) for name in result.columns]
                         for row in result.rows]}
    return call(request, shape, cap="view")


class AccessRequestBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    resource_id: str
    columns: Optional[list[str]] = None
    reason: str
    requested_duration: Optional[str] = None


def _duration_hours(value: Optional[str]) -> int:
    if not value:
        return 24
    text = value.strip().lower()
    import re
    match = re.fullmatch(r"(\d+)\s*(h|hr|hrs|hour|hours|d|day|days)?", text)
    if not match:
        raise AgentError(ErrorCode.INVALID_INPUT, "Requested duration must be a number of hours or days")
    amount = int(match.group(1))
    hours = amount * (24 if match.group(2) in ("d", "day", "days") else 1)
    if hours < 1:
        raise AgentError(ErrorCode.INVALID_INPUT, "Requested duration must be positive")
    return hours


@router.post("/agents/access/request")
def access_request(request: Request, body: AccessRequestBody):
    def work():
        result = a23.request_access(a23.RequestInput(resource=body.resource_id, columns=body.columns or [],
                                                     reason=body.reason, duration_hours=_duration_hours(body.requested_duration)))
        return {"request_id": result.request_id, "status": result.status}
    return call(request, work, cap="request_access", body=body)


class ViewerPolicyBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    resource_id: str
    hidden_columns: list[str]


@router.put("/agents/access/policy")
def access_policy(request: Request, body: ViewerPolicyBody):
    return call(request, lambda: a23.update_viewer_policy(body.resource_id, body.hidden_columns),
                cap="manage_access", body=body)


@router.post("/agents/access/decision")
def access_decision(request: Request, body: a23.DecisionInput):
    def shape():
        result = a23.decide(body)
        signature = result.signature
        return {"request_id": result.request_id, "status": result.status,
                "signature": signature.get("value") if isinstance(signature, dict) else signature}
    return call(request, shape, body=body)


@router.get("/agents/access/requests")
def access_requests(request: Request):
    def shape():
        result = a23.list_requests()
        return {"requests": [{"request_id": r.request_id, "user_id": r.user_id, "resource_id": r.resource,
                              "columns": r.columns, "reason": r.reason, "status": r.status,
                              "requested_at": r.created_at, "valid_until": r.valid_until}
                             for r in result]}
    return call(request, shape, cap="view")


@router.get("/agents/export/history")
def export_history(request: Request):
    return call(request, a20.history, cap="export")


@router.get("/exports/{export_id}/download")
def export_download(request: Request, export_id: str, exp: int, sig: str):
    return _download(request, export_id, exp, sig, False)


@router.get("/exports/{export_id}/manifest")
def export_manifest(request: Request, export_id: str, exp: int, sig: str):
    return _download(request, export_id, exp, sig, True)


def _download(request: Request, export_id: str, exp: int, sig: str, manifest: bool):
    from .support import authenticate
    rid = new_request_id()
    try:
        u = authenticate(request)
        auth.set_user(u)
        auth.set_request_id(rid)
        data, mime = a20.download(export_id, exp, sig, manifest)
        return Response(content=data, media_type=mime, headers={"X-Request-Id": rid, "Content-Disposition": f'attachment; filename="export-{export_id[:8]}"'})
    except AgentError as e:
        return fail(e.code, e.message, rid)
