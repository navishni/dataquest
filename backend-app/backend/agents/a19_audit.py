"""Agent 19 - Audit read API + verification job. The write path is common/audit.py (audit.append).

GET /agents/audit requires the `audit` capability; the read itself is audited. Response: {events, chain_valid,
next_cursor?, checkpoint?} with the additive fields from the contract delta (seq, outcome, request_id, signature,
ip_masked). chain_valid = hashes + signatures recomputed over the returned range + linkage to the event before the range +
the persistent chain state (set false permanently by any failed full verification; never auto-repaired).
verify_job() recomputes the entire chain, checks gaps, signatures and checkpoints against the external anchor, writes an
audit_verified event, and on failure sends an URGENT ntfy alert.
"""
from typing import Optional

from pydantic import BaseModel, ConfigDict

from ..common import audit, auth, notify
from ..common.errors import AgentError, ErrorCode


class AuditQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    from_: Optional[str] = None
    to: Optional[str] = None
    actor: Optional[str] = None
    event_type: Optional[str] = None
    case_id: Optional[str] = None
    object_id: Optional[str] = None
    cursor: Optional[str] = None
    limit: int = 100


class AuditResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    events: list[dict]
    chain_valid: bool
    next_cursor: Optional[str] = None
    checkpoint: Optional[dict] = None


def run(q: AuditQuery) -> AuditResponse:
    u = auth.require("audit")
    if not 1 <= q.limit <= 500:
        raise AgentError(ErrorCode.INVALID_INPUT, "limit must be 1..500")
    try:
        cur = int(q.cursor) if q.cursor else None
    except ValueError:
        raise AgentError(ErrorCode.INVALID_INPUT, "Bad cursor")
    res = audit.log.query(q.from_, q.to, q.actor, q.event_type, q.case_id, q.object_id, cur, q.limit)
    audit.append(event_type="audit_read", object_type="audit", object_id="query",
                 details={"filters": {k: v for k, v in q.model_dump().items() if v is not None and k != "limit"}})
    return AuditResponse(**res)


def verify_job() -> dict:
    res = audit.log.verify()
    audit.append(event_type="audit_verified", object_type="audit", object_id="chain",
                 outcome="success" if res["valid"] else "error",
                 details={"valid": res["valid"], "problems": res["problems"][:20], "events": res["events_checked"]})
    if not res["valid"]:
        notify.send("audit_chain_invalid", "urgent", "Audit chain verification failed",
                    f"problems={len(res['problems'])} first={res['problems'][0]}", dedupe_key="audit_chain_invalid")
    return res
