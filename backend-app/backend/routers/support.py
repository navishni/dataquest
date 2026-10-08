"""Shared request wrapper: auth, request id, envelope, request-level audit, optional 202 job mode (?async=true)."""
from typing import Any, Callable, Optional

from fastapi import Request
from fastapi.responses import JSONResponse

from ..common import audit, auth, config, jobs
from ..common.envelope import fail, handle, new_request_id, ok
from ..common.errors import AgentError, ErrorCode
from ..common.store import store


def _ua_family(ua: str) -> str:
    ua = (ua or "").lower()
    for k in ("firefox", "edg", "chrome", "safari", "curl", "python", "postman"):
        if k in ua:
            return {"edg": "edge"}.get(k, k)
    return "other"


def _ip(request: Request) -> str:
    h = request.client.host if request.client else ""
    from ..common.notify import mask_ip
    return mask_ip(h) or ""


def authenticate(request: Request) -> auth.User:
    h = request.headers.get("authorization", "")
    if not h.lower().startswith("bearer "):
        raise AgentError(ErrorCode.FORBIDDEN, "Authentication required")
    u = auth.verify_token(h[7:].strip())
    u.ip_masked, u.user_agent_family = _ip(request), _ua_family(request.headers.get("user-agent", ""))
    return u


def check_ownership(u: auth.User, body: Any) -> None:
    if u.has("admin"):
        return
    sid = getattr(body, "source_id", None)
    if isinstance(sid, str):
        m = store.get("source", sid)
        if m is None:
            raise AgentError(ErrorCode.NOT_FOUND, "Source not found")
        if m.get("tenant_id", config.get("tenant_id", "default")) != u.tenant_id:
            raise AgentError(ErrorCode.FORBIDDEN, "Source not accessible")


def call(request: Request, fn: Callable[[], Any], cap: Optional[str] = None, body: Any = None,
         public: bool = False, status: int = 200, event: Optional[str] = None) -> JSONResponse:
    rid = new_request_id()
    auth.set_request_id(rid)
    path = request.url.path
    user: Optional[auth.User] = None
    try:
        if not public:
            user = authenticate(request)
            auth.set_user(user)
            if cap and not user.has(cap):
                raise AgentError(ErrorCode.FORBIDDEN, f"Missing capability: {cap}")
            if body is not None:
                check_ownership(user, body)
    except AgentError as e:
        _audit_request(request, rid, user, path, "denied")
        return fail(e.code, e.message, rid, e.details)

    run_async = request.query_params.get("async", "").lower() in ("1", "true")
    if run_async and user is not None:
        u, r = user, rid

        def job():
            auth.set_user(u)
            auth.set_request_id(r)
            return fn()
        jid = jobs.submit(job, u.user_id)
        _audit_request(request, rid, user, path, "success")
        return ok({"job_id": jid}, rid, 202)

    resp = handle(rid, fn, status)
    outcome = "success" if resp.status_code < 400 else "denied" if resp.status_code in (401, 403) else "error"
    _audit_request(request, rid, user, path, outcome, resp.status_code)
    return resp


def _audit_request(request: Request, rid: str, user: Optional[auth.User], path: str, outcome: str, code: int = 0) -> None:
    try:
        audit.append(event_type="http_request", object_type="route", object_id=path[:200], outcome=outcome,
                     actor_id=user.user_id if user else "anonymous", actor_role=user.role if user else "none",
                     tenant_id=user.tenant_id if user else config.get("tenant_id", "default"), request_id=rid,
                     ip_masked=_ip(request), user_agent_family=_ua_family(request.headers.get("user-agent", "")),
                     details={"method": request.method, "status": code})
    except Exception:  # noqa: BLE001 - domain events are fail-closed; the request log must not mask the real response
        pass
