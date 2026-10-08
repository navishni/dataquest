"""Agent 17 - Action Draft. Never sends anything; no network egress.

action_type must be in config.action_types else INVALID_INPUT. The subject/body come from a per-type template filled
with the finding statement (already neutral and computed by code). An optional LLM polish runs ONLY through llm_guard with
number/entity/quote grounding against the evidence excerpts and finding statement; if the guard rejects or abstains the
template text is kept. status="draft", requires_human_approval=true, generated_by="ai", labels
["ai_generated","not_sent"]. idempotency_key = sha256(case|finding|type|content); action_id derives from it, so re-drafting the
same content returns the same action. final_content_hash = SHA-256(canonical JSON {subject, body}).
policy_checks: banned_terms (config.action_policy.banned_terms), pii_scan (e-mail/phone/12-digit id patterns in the body),
recipient_domain_allowlist (skipped: no recipient is stored on the draft; evaluated at execution), evidence_present, length.
Only the ProposedAction.status values are shared with agent 18 - none of its internals are imported.
"""
import re
from typing import Optional

from pydantic import BaseModel, ConfigDict

from ..common import audit, auth, config, crypto, llm_guard
from ..common.errors import AgentError, ErrorCode
from ..common.models import Check, EvidenceReference, ProposedAction
from ..common.store import store

TEMPLATES = {
    "request_clarification": ("Request for clarification: {title}",
                              "Dear recipient,\n\nDuring a review of the submitted documents, the following potential discrepancy was noted.\n\n{statement}\n\n"
                              "We would be grateful if you could clarify. This message is a draft and no final decision has been made.\n"),
    "request_missing_document": ("Request for supporting document: {title}",
                                 "Dear recipient,\n\nTo complete our review, a supporting document may be needed.\n\n{statement}\n\n"
                                 "Please share any relevant document. No final decision has been made.\n"),
    "flag_for_review": ("Internal note for manual review: {title}",
                        "A potential discrepancy has been flagged for manual review.\n\n{statement}\n\nManual review recommended. No final decision has been made.\n"),
}
_PII = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+|\b\d{10}\b|\b\d{12}\b")


class DraftInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    case_id: str
    finding_id: Optional[str] = None
    action_type: str


class _Polish(BaseModel):
    body: Optional[str] = None


def run(inp: DraftInput) -> ProposedAction:
    u = auth.current_user()
    if inp.action_type not in config.get("action_types", []):
        raise AgentError(ErrorCode.INVALID_INPUT, "Unknown action_type", {"allowed": config.get("action_types")})
    case = store.get("case", inp.case_id)
    if case is None:
        raise AgentError(ErrorCode.NOT_FOUND, "Case not found")
    if case.get("tenant_id", "default") != u.tenant_id and not u.has("admin"):
        raise AgentError(ErrorCode.FORBIDDEN, "Case not accessible")
    finding = None
    if inp.finding_id:
        finding = store.get("finding", inp.finding_id)
        if finding is None or finding["case_id"] != inp.case_id:
            raise AgentError(ErrorCode.NOT_FOUND, "Finding not found")
    subj_t, body_t = TEMPLATES.get(inp.action_type, TEMPLATES["flag_for_review"])
    title = finding["title"] if finding else f"case {inp.case_id}"
    statement = finding["statement"] if finding else "No specific finding was selected; manual review recommended."
    subject, body = subj_t.format(title=title), body_t.format(statement=statement)
    evidence = [EvidenceReference(**e) for e in finding["evidence_references"]] if finding else []
    polished = False
    if llm_guard.provider is not None and finding:
        ctx = [{"block_id": e.block_id, "text": e.excerpt} for e in evidence] + [{"block_id": "finding", "text": statement}]
        g = llm_guard.call("Rewrite the draft message in a neutral, polite tone. Do not add facts, figures or decisions. "
                           "DRAFT:\n" + body, ctx + [{"block_id": "draft", "text": body}], _Polish, policy="reject_all")
        if g["ok"] and g["output"] and g["output"].get("body"):
            body, polished = g["output"]["body"], True
    pol = config.get("action_policy")
    checks = []
    banned = [t for t in pol["banned_terms"] if re.search(rf"\b{re.escape(t)}\b", subject + " " + body, re.I)]
    checks.append(Check(name="banned_terms", status="fail" if banned else "pass", detail=f"found: {banned}" if banned else None))
    checks.append(Check(name="pii_scan", status="warn" if _PII.search(body) else "pass"))
    checks.append(Check(name="recipient_domain_allowlist", status="skipped", detail="No recipient on draft; checked at execution"))
    checks.append(Check(name="evidence_present", status="pass" if evidence else "warn",
                        detail=None if evidence else "No finding evidence attached"))
    checks.append(Check(name="length", status="pass" if len(body) <= pol["max_body_chars"] else "fail"))
    idem = crypto.hash_obj({"case": inp.case_id, "finding": inp.finding_id, "type": inp.action_type, "content": [subject, body]})
    action_id = idem[:16]
    existing = store.get("action", action_id)
    if existing:
        return ProposedAction(**existing)
    act = ProposedAction(action_id=action_id, case_id=inp.case_id, finding_id=inp.finding_id, action_type=inp.action_type,
                         subject=subject, body=body, status="draft", requires_human_approval=True, generated_by="ai",
                         labels=["ai_generated", "not_sent"], idempotency_key=idem,
                         final_content_hash=crypto.hash_obj({"subject": subject, "body": body}),
                         supporting_evidence=evidence, policy_checks=checks, drafted_by=u.user_id)
    store.put("action", action_id, act.model_dump(mode="json"))
    audit.append(event_type="action_drafted", object_type="action", object_id=action_id, case_id=inp.case_id,
                 details={"type": inp.action_type, "content_hash": act.final_content_hash, "llm_polished": polished})
    return act
