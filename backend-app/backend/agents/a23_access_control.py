"""Agent 23 - Access Control (policy store). Default deny.

Resources/columns come from common/analytics.RESOURCES. A column is visible to a caller when (a) config.access.
default_columns_by_role gives the role "all", or "non_sensitive" and the column is not sensitive, or (b) the caller holds an
unexpired grant covering it. Grants expire at read time (a grant_expired audit event is written once). Rows are filtered in the
data layer (owner scoping) BEFORE the query; preview projects only visible columns server-side, locked columns are null in every
row and are never read. Endpoints: schema, preview, request, requests, decision (admin only; approver != requester; valid_until
must be in the future and within config.access.max_grant_hours; approval is signed with Ed25519). New requests notify the admin
(ids + link only, never data).
"""
import hashlib
import uuid
from datetime import datetime, timedelta, timezone
from typing import Callable, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from ..common import analytics, audit, auth, config, crypto, notify
from ..common.errors import AgentError, ErrorCode
from ..common.store import store

now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)


class ColumnInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    sensitive: bool
    locked: bool
    hidden_for_viewers: bool = False


class ResourceInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")
    resource: str
    columns: list[ColumnInfo]


class SchemaOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    resources: list[ResourceInfo]


class PreviewInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    resource: str
    limit: int = Field(default=10, ge=1)


class PreviewOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    resource: str
    columns: list[str]
    locked_columns: list[str]
    rows: list[dict]


class RequestInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    resource: str
    columns: list[str]
    reason: str
    duration_hours: int = Field(ge=1)


class AccessRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str
    user_id: str
    resource: str
    columns: list[str]
    reason: str
    duration_hours: int
    status: Literal["pending", "approved", "rejected"]
    created_at: str
    decided_by: Optional[str] = None
    decided_at: Optional[str] = None
    valid_until: Optional[str] = None
    notes: Optional[str] = None
    signature: Optional[dict] = None


class DecisionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str
    decision: Literal["approve", "reject"]
    valid_until: Optional[str] = None
    notes: Optional[str] = None


def _grants(user_id: str) -> list[dict]:
    t, out = now(), []
    for g in store.list("grant"):
        if g["user_id"] != user_id:
            continue
        payload = g.get("payload")
        signature = g.get("signature")
        if not isinstance(payload, dict) or not isinstance(signature, dict) or not crypto.verify(
                crypto.canonical_json(payload), signature):
            raise AgentError(ErrorCode.FORBIDDEN, "Access grant signature is invalid")
        if any(payload.get(k) != g.get(k) for k in ("user_id", "resource", "columns", "valid_until")):
            raise AgentError(ErrorCode.FORBIDDEN, "Access grant contents do not match its signature")
        if datetime.fromisoformat(g["valid_until"]) <= t:
            if not g.get("expiry_logged"):
                audit.append(event_type="grant_expired", object_type="grant", object_id=g["grant_id"],
                             details={"resource": g["resource"]})
                g["expiry_logged"] = True
                store.put("grant", g["grant_id"], g)
                notify.send("grant_expired", "normal", "Access grant expired", f"grant={g['grant_id']} user={user_id}")
            continue
        out.append(g)
    return out


def visible_columns(u: auth.User, resource: str) -> list[str]:
    if resource not in analytics.RESOURCES:
        raise AgentError(ErrorCode.NOT_FOUND, "Resource not found")
    cols = analytics.RESOURCES[resource]
    mode = config.get(f"access.default_columns_by_role.{u.role}", "none")
    vis = {c for c, sens in cols.items() if mode == "all" or (mode == "non_sensitive" and not sens)}
    if u.role == "viewer":
        policy = store.get("access_policy", resource) or {"hidden_columns": []}
        vis -= set(policy.get("hidden_columns", []))
    for g in _grants(u.user_id):
        if g["resource"] == resource:
            vis |= set(g["columns"]) & set(cols)
    return [c for c in cols if c in vis]


def schema() -> SchemaOutput:
    u = auth.current_user()
    out = []
    for r, cols in analytics.RESOURCES.items():
        vis = set(visible_columns(u, r))
        policy = store.get("access_policy", r) or {"hidden_columns": []}
        hidden = set(policy.get("hidden_columns", []))
        out.append(ResourceInfo(resource=r, columns=[ColumnInfo(name=c, sensitive=s, locked=c not in vis,
                                                                  hidden_for_viewers=c in hidden)
                                                      for c, s in cols.items()]))
    audit.append(event_type="access_schema_viewed", object_type="schema", object_id="all")
    return SchemaOutput(resources=out)


def preview(inp: PreviewInput) -> PreviewOutput:
    u = auth.require("view")
    vis = visible_columns(u, inp.resource)
    cap = config.get("limits.preview_max_rows", 100)
    if inp.limit > cap:
        raise AgentError(ErrorCode.INVALID_INPUT, f"limit exceeds maximum of {cap}")
    all_cols = list(analytics.RESOURCES[inp.resource])
    locked = [c for c in all_cols if c not in vis]
    rows = analytics.load_rows(inp.resource, u)[: inp.limit]
    out_rows = [{c: (r.get(c) if c in vis else None) for c in all_cols} for r in rows]  # projection: locked never copied
    if locked:
        audit.append(event_type="access_denied", object_type="resource", object_id=inp.resource, outcome="denied",
                     details={"locked_columns": locked, "context": "preview"})
    audit.append(event_type="access_preview", object_type="resource", object_id=inp.resource,
                 details={"rows": len(out_rows), "columns": vis})
    return PreviewOutput(resource=inp.resource, columns=all_cols, locked_columns=locked, rows=out_rows)


def request_access(inp: RequestInput) -> AccessRequest:
    u = auth.current_user()
    if not inp.reason.strip():
        raise AgentError(ErrorCode.INVALID_INPUT, "A reason is required")
    cols = analytics.RESOURCES.get(inp.resource)
    if cols is None:
        raise AgentError(ErrorCode.NOT_FOUND, "Resource not found")
    requested = list(dict.fromkeys(inp.columns)) if inp.columns else [c for c in cols if c not in visible_columns(u, inp.resource)]
    bad = [c for c in requested if c not in cols]
    if bad or not requested:
        raise AgentError(ErrorCode.INVALID_INPUT, "Unknown or empty columns", bad)
    already_visible = set(visible_columns(u, inp.resource))
    if set(requested) & already_visible:
        raise AgentError(ErrorCode.INVALID_INPUT, "Request only columns that are currently locked",
                         sorted(set(requested) & already_visible))
    if inp.duration_hours > config.get("access.max_grant_hours", 720):
        raise AgentError(ErrorCode.INVALID_INPUT, "Requested duration exceeds maximum")
    for r in store.list("access_request"):
        if r["user_id"] == u.user_id and r["resource"] == inp.resource and r["status"] == "pending" and set(requested) <= set(r["columns"]):
            raise AgentError(ErrorCode.CONFLICT, "A pending request already covers these columns", {"request_id": r["request_id"]})
    rid = uuid.uuid4().hex
    rec = AccessRequest(request_id=rid, user_id=u.user_id, resource=inp.resource, columns=sorted(set(requested)),
                        reason=inp.reason.strip(), duration_hours=inp.duration_hours, status="pending",
                        created_at=now().isoformat())
    store.put("access_request", rid, rec.model_dump(mode="json"))
    try:
        audit.append(event_type="access_requested", object_type="access_request", object_id=rid,
                     details={"resource": inp.resource, "columns": rec.columns})
    except Exception:
        store.delete("access_request", rid)
        raise
    notify.send("access_requested", "high", "New access request", f"request={rid} user={u.user_id} role={u.role}",
                link="/access")
    return rec


def list_requests() -> list[AccessRequest]:
    u = auth.current_user()
    rows = store.list("access_request")
    return [AccessRequest(**r) for r in rows if u.has("admin") or r["user_id"] == u.user_id]


def decide(inp: DecisionInput) -> AccessRequest:
    u = auth.require("admin")
    r = store.get("access_request", inp.request_id)
    if r is None:
        raise AgentError(ErrorCode.NOT_FOUND, "Request not found")
    if r["status"] != "pending":
        raise AgentError(ErrorCode.CONFLICT, "Request already decided")
    if r["user_id"] == u.user_id:
        raise AgentError(ErrorCode.FORBIDDEN, "Approvers cannot decide their own requests")
    t = now()
    old_request = dict(r)
    created_grant_id = None
    if inp.decision == "approve":
        if not inp.valid_until:
            raise AgentError(ErrorCode.INVALID_INPUT, "valid_until is required")
        try:
            vu = datetime.fromisoformat(inp.valid_until)
            if vu.tzinfo is None:
                vu = vu.replace(tzinfo=timezone.utc)
        except ValueError:
            raise AgentError(ErrorCode.INVALID_INPUT, "valid_until must be ISO-8601")
        if vu <= t:
            raise AgentError(ErrorCode.INVALID_INPUT, "valid_until must be in the future")
        if vu > t + timedelta(hours=config.get("access.max_grant_hours", 720)):
            raise AgentError(ErrorCode.INVALID_INPUT, "valid_until exceeds maximum grant duration")
        payload = {"request_id": r["request_id"], "user_id": r["user_id"], "resource": r["resource"],
                   "columns": r["columns"], "valid_until": vu.isoformat()}
        sig = crypto.sign(crypto.canonical_json(payload))
        gid = hashlib.sha256(r["request_id"].encode()).hexdigest()[:16]
        created_grant_id = gid
        store.put("grant", gid, {"grant_id": gid, "user_id": r["user_id"], "resource": r["resource"], "columns": r["columns"],
                                 "valid_until": vu.isoformat(), "signature": sig, "payload": payload})
        r.update(status="approved", valid_until=vu.isoformat(), signature=sig)
    else:
        r.update(status="rejected")
    r.update(decided_by=u.user_id, decided_at=t.isoformat(), notes=inp.notes)
    store.put("access_request", inp.request_id, r)
    try:
        audit.append(event_type="access_decided", object_type="access_request", object_id=inp.request_id,
                     details={"decision": inp.decision, "valid_until": r.get("valid_until")})
    except Exception:
        store.put("access_request", inp.request_id, old_request)
        if created_grant_id:
            store.delete("grant", created_grant_id)
        raise
    notify.send("access_decided", "normal", f"Access request {r['status']}", f"request={inp.request_id} by={u.user_id}")
    return AccessRequest(**r)


def update_viewer_policy(resource: str, hidden_columns: list[str]) -> dict:
    """Editor-managed visibility policy for the viewer role; admin remains unrestricted."""
    u = auth.require("manage_access")
    cols = analytics.RESOURCES.get(resource)
    if cols is None:
        raise AgentError(ErrorCode.NOT_FOUND, "Resource not found")
    hidden = sorted(set(hidden_columns))
    bad = [c for c in hidden if c not in cols]
    if bad:
        raise AgentError(ErrorCode.INVALID_INPUT, "Unknown columns", bad)
    old = store.get("access_policy", resource)
    policy = {"resource": resource, "hidden_columns": hidden, "updated_by": u.user_id,
              "updated_at": now().isoformat()}
    store.put("access_policy", resource, policy)
    try:
        audit.append(event_type="viewer_access_policy_updated", object_type="resource", object_id=resource,
                     details={"hidden_columns": hidden})
    except Exception:
        if old is None:
            store.delete("access_policy", resource)
        else:
            store.put("access_policy", resource, old)
        raise
    return policy
