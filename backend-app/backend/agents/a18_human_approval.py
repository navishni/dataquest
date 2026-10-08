"""Agent 18 - Human Approval (table-driven state machine).

TRANSITIONS (single source of truth): (state, decision) -> next state
  draft: submit->in_review, save_edit->draft, cancel->cancelled
  in_review: approve->approved, reject->rejected, save_edit->in_review, cancel->cancelled
  approved: execute->executed, save_edit->approved (if content changes: back to in_review, approval invalidated), cancel->cancelled
  rejected / cancelled / executed: terminal.
Invalid transition -> CONFLICT with allowed next decisions. Authorization comes from backend capabilities: approve/reject need
`approve` AND approver != drafter (separation of duties); execute needs `execute`; submit/save_edit/cancel are for the drafter or an admin.
Content (subject, body) edits recompute final_content_hash = SHA-256(canonical JSON) and clear approvals. Approval signs
canonical_json({action_id, content_hash, actor, timestamp}) with Ed25519 (kid included). Execute re-verifies signature, hash binding,
signature age (config.approval.signature_ttl_seconds) and the idempotency key (no double execution), then calls `executor`
(default: records a result, sends nothing). Concurrency: per-action lock + version counter. Every transition writes an event and an audit
record; if the audit write fails the transition is rolled back (fail closed) and ENGINE_FAILED is raised; ntfy alerts for submit/approve/reject/execute.
"""
import copy
import threading
from datetime import datetime, timezone
from typing import Callable, Literal, Optional

from pydantic import BaseModel, ConfigDict

from ..common import audit, auth, config, crypto, notify
from ..common.errors import AgentError, ErrorCode
from ..common.models import ProposedAction
from ..common.store import store

TRANSITIONS = {
    ("draft", "submit"): "in_review", ("draft", "save_edit"): "draft", ("draft", "cancel"): "cancelled",
    ("in_review", "approve"): "approved", ("in_review", "reject"): "rejected",
    ("in_review", "save_edit"): "in_review", ("in_review", "cancel"): "cancelled",
    ("approved", "execute"): "executed", ("approved", "save_edit"): "approved", ("approved", "cancel"): "cancelled",
}
EDITABLE = {"subject", "body"}
NOTIFY = {"submit": "submitted", "approve": "approved", "reject": "rejected", "execute": "executed"}
_locks: dict[str, threading.Lock] = {}
_guard = threading.Lock()
clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)


def default_executor(action: dict) -> dict:
    """Stub executor: records that execution happened; nothing is sent."""
    return {"executed": True, "sent": False, "note": "No delivery channel wired"}


executor: Callable[[dict], dict] = default_executor


class ApprovalInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action_id: str
    decision: Literal["submit", "submit_review", "approve", "reject", "execute", "cancel", "save_edit"]
    notes: Optional[str] = None
    edited_fields: Optional[dict[str, str]] = None
    rejection_reason: Optional[str] = None


class ApprovalEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action_id: str
    previous_status: str
    new_status: str
    actor: str
    timestamp: str
    content_hash: str
    edited_fields: list[str] = []
    decision: str


class ApprovalOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: ProposedAction
    event: ApprovalEvent
    signature: Optional[dict] = None


def _lock(aid: str) -> threading.Lock:
    with _guard:
        return _locks.setdefault(aid, threading.Lock())


def run(inp: ApprovalInput) -> ApprovalOutput:
    u = auth.current_user()
    decision = "submit" if inp.decision == "submit_review" else inp.decision
    with _lock(inp.action_id):
        raw = store.get("action", inp.action_id)
        if raw is None:
            raise AgentError(ErrorCode.NOT_FOUND, "Action not found")
        snapshot = copy.deepcopy(raw)
        act = copy.deepcopy(raw)
        prev = act["status"]
        nxt = TRANSITIONS.get((prev, decision))
        if nxt is None:
            allowed = sorted(("submit_review" if d == "submit" else d) for (s, d) in TRANSITIONS if s == prev)
            raise AgentError(ErrorCode.CONFLICT, f"Cannot {decision} from {prev}", {"allowed": allowed})
        drafter = act["drafted_by"]
        if decision in ("approve", "reject"):
            if not u.has("approve"):
                raise AgentError(ErrorCode.FORBIDDEN, "Missing capability: approve")
            if u.user_id == drafter:
                raise AgentError(ErrorCode.FORBIDDEN, "Approver must differ from drafter")
        elif decision == "execute":
            if not u.has("execute"):
                raise AgentError(ErrorCode.FORBIDDEN, "Missing capability: execute")
        elif u.user_id != drafter and not u.has("admin"):
            raise AgentError(ErrorCode.FORBIDDEN, "Only the drafter or an admin may do this")
        now = clock()
        sig = None
        edited: list[str] = []
        if decision == "save_edit":
            fields = inp.edited_fields or {}
            if not fields or set(fields) - EDITABLE:
                raise AgentError(ErrorCode.INVALID_INPUT, "edited_fields must be a non-empty subset of subject/body")
            for k, v in fields.items():
                if act[k] != v:
                    act[k] = v
                    edited.append(k)
            if edited:
                act["final_content_hash"] = crypto.hash_obj({"subject": act["subject"], "body": act["body"]})
                act["approvals"] = []
                if prev == "approved":
                    nxt = "in_review"
        if decision == "reject" and not inp.rejection_reason:
            raise AgentError(ErrorCode.INVALID_INPUT, "rejection_reason is required")
        if decision == "approve":
            payload = {"action_id": act["action_id"], "content_hash": act["final_content_hash"], "actor": u.user_id,
                       "timestamp": now.isoformat()}
            sig = crypto.sign(crypto.canonical_json(payload))
            act["approvals"] = [{"actor": u.user_id, "timestamp": payload["timestamp"], "content_hash": payload["content_hash"],
                                 "signature": sig, "payload": payload}]
        if decision == "execute":
            appr = act["approvals"][-1] if act["approvals"] else None
            if not appr or not crypto.verify(crypto.canonical_json(appr["payload"]), appr["signature"]):
                raise AgentError(ErrorCode.FORBIDDEN, "Approval signature invalid")
            if appr["content_hash"] != crypto.hash_obj({"subject": act["subject"], "body": act["body"]}) or \
                    appr["content_hash"] != act["final_content_hash"]:
                raise AgentError(ErrorCode.CONFLICT, "Content changed after approval")
            age = (now - datetime.fromisoformat(appr["timestamp"])).total_seconds()
            if age > config.get("approval.signature_ttl_seconds", 604800):
                raise AgentError(ErrorCode.FORBIDDEN, "Approval signature expired")
            if store.get("executed_key", act["idempotency_key"]):
                raise AgentError(ErrorCode.CONFLICT, "Action already executed")
        act["status"] = nxt
        act["updated_at"] = now.isoformat()
        act["version"] = act.get("version", 1) + 1
        event = ApprovalEvent(action_id=act["action_id"], previous_status=prev, new_status=nxt, actor=u.user_id,
                              timestamp=now.isoformat(), content_hash=act["final_content_hash"],
                              edited_fields=edited, decision=decision)
        result = None
        try:
            store.put("action", inp.action_id, act)
            if decision == "execute":
                result = executor(act)
                store.put("executed_key", act["idempotency_key"], {"action_id": act["action_id"], "result": result})
                store.put("execution", act["action_id"], result)
            audit.append(event_type=f"action_{decision}", object_type="action", object_id=act["action_id"],
                         case_id=act["case_id"], details={**event.model_dump(), "notes_present": bool(inp.notes)})
            if sig:
                audit.append(event_type="signature_created", object_type="action", object_id=act["action_id"],
                             case_id=act["case_id"], details={"kid": sig["kid"], "content_hash": act["final_content_hash"]})
            store.put("action_event", f"{act['action_id']}:{act['version']}", event.model_dump())
        except Exception as e:  # fail closed: roll back the transition
            store.put("action", inp.action_id, snapshot)
            if decision == "execute":
                store.delete("executed_key", snapshot["idempotency_key"])
                store.delete("execution", snapshot["action_id"])
            if isinstance(e, AgentError):
                raise AgentError(ErrorCode.ENGINE_FAILED, "Audit write failed; transition rolled back") if e.code == ErrorCode.ENGINE_FAILED else e
            raise AgentError(ErrorCode.ENGINE_FAILED, "Transition failed and was rolled back")
    if decision in NOTIFY:
        notify.send(f"action_{NOTIFY[decision]}", "high", f"Action {NOTIFY[decision]}",
                    f"action={act['action_id']} case={act['case_id']} by={u.user_id} role={u.role}",
                    dedupe_key=None)
    return ApprovalOutput(action=ProposedAction(**act), event=event, signature=sig)
