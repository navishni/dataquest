"""Adapters for the frontend's canonical HTTP response shapes."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from . import auth, config
from .store import store


def evidence_reference(raw: dict, *, redact_excerpt: bool = False, redact_source: bool = False) -> dict:
    source_id = raw.get("source_id", "")
    source = store.get("source", source_id) or {}
    doc = store.get("document", source_id) or {}
    block = None
    page_number = None
    for page in doc.get("pages", []):
        for candidate in page.get("blocks", []):
            if candidate.get("block_id") == raw.get("block_id"):
                block = candidate
                page_number = candidate.get("page_number") or page.get("page_number")
                break
        if block:
            break
    if page_number is None:
        page_id = raw.get("page_id")
        page_number = next((p.get("page_number") for p in doc.get("pages", []) if p.get("page_id") == page_id), 0)
    location = raw.get("location") or {}
    bbox = location.get("bbox")
    if not isinstance(bbox, list) or len(bbox) != 4:
        bbox = None
    confidence = ((block or {}).get("evidence") or {}).get("confidence", 0)
    return {
        "source_id": "" if redact_source else source_id,
        "filename": source.get("display_name", source_id),
        "page_number": int(page_number or 0),
        "block_id": raw.get("block_id", ""),
        "text_excerpt": "" if redact_excerpt else raw.get("excerpt", raw.get("text_excerpt", "")),
        "bbox": bbox,
        "confidence": float(confidence or 0),
    }


def fact(raw: dict, *, visible_columns: set[str] | None = None, redact_excerpt: bool = False) -> dict:
    out = dict(raw)
    columns = visible_columns
    if columns is not None:
        mapping = {
            "subject": ("subject", "Hidden content"), "metric": ("metric", "Hidden content"),
            "raw_value": ("raw_value", ""), "normalized_value": ("normalized_value", None),
            "currency": ("currency", None), "frequency": ("frequency", None),
            "category": ("category", None), "basis": ("basis", None),
            "confidence": ("confidence", 0), "source_id": ("source_id", ""),
            "fact_id": ("fact_id", ""), "case_id": ("case_id", ""),
            "unit": ("unit", None), "period_start": ("period_start", None), "period_end": ("period_end", None),
            "normalization_rule": ("normalization_rule", "Hidden"),
            "ambiguity_notes": ("ambiguity_notes", None),
        }
        for column, (key, fallback) in mapping.items():
            if column not in columns:
                out[key] = fallback
        locked = {column for column in mapping if column not in columns}
        if "raw_value" not in columns:
            out["raw_text"] = ""
            redact_excerpt = True
        if "raw_text" not in columns or "evidence" not in columns:
            redact_excerpt = True
        if "evidence" not in columns:
            out["evidence"] = []
        if redact_excerpt:
            locked.update(("raw_text", "evidence"))
        out["locked_columns"] = sorted(locked)
    if "evidence" not in out:
        out["evidence"] = []
    if redact_excerpt:
        out["raw_text"] = ""
    if visible_columns is None or "evidence" in visible_columns:
        out["evidence"] = [evidence_reference(e, redact_excerpt=redact_excerpt,
                                               redact_source=(columns is not None and "source_id" not in columns))
                           for e in raw.get("evidence", [])]
    return out


def comparison(raw: dict) -> dict:
    checks = raw.get("checks", [])
    return {
        **raw,
        "comparable": not any(c.get("status") == "fail" for c in checks),
        "rule_id": "VALUE_DIFFERENCE_V1",
    }


def finding(raw: dict, *, comparison_row: dict | None = None, redact_details: bool = False,
            redact_excerpt: bool = False, visible_columns: set[str] | None = None) -> dict:
    comp = comparison_row or {}
    explanations = raw.get("possible_explanations", [])
    explanations = [e.get("explanation", "") if isinstance(e, dict) else str(e) for e in explanations]
    hidden = set()
    if visible_columns is not None:
        hidden = {name for name in ("finding_id", "title", "statement", "severity", "confidence", "category", "status",
                                    "difference_absolute", "difference_percentage", "possible_explanations",
                                    "recommended_review_action", "evidence_references") if name not in visible_columns}
    if redact_details:
        title = "Potential discrepancy"
        statement = "Potential discrepancy identified. Manual review recommended. No final decision has been made."
        absolute = percentage = None
        explanations = []
    else:
        title = raw.get("title", "Potential discrepancy")
        statement = raw.get("statement", "")
        try:
            absolute = float(comp.get("abs_diff")) if comp.get("abs_diff") is not None else None
        except (TypeError, ValueError):
            absolute = None
        percentage = comp.get("pct_diff")
    if "title" in hidden or "statement" in hidden:
        title = "Potential discrepancy"
        statement = "Potential discrepancy identified. Manual review recommended. No final decision has been made."
        absolute = percentage = None
        explanations = []
    out = {
        **raw,
        "category": raw.get("category", "discrepancy") if "category" not in hidden else "hidden",
        "status": raw.get("status", "open") if "status" not in hidden else "hidden",
        "title": title,
        "statement": statement,
        "difference_absolute": absolute if "difference_absolute" not in hidden else None,
        "difference_percentage": percentage if "difference_percentage" not in hidden else None,
        "possible_explanations": explanations if "possible_explanations" not in hidden else [],
        "recommended_review_action": raw.get("recommended_review_action", "Manual review recommended.")
        if "recommended_review_action" not in hidden else "Manual review recommended.",
        "evidence_references": ([evidence_reference(e, redact_excerpt=redact_excerpt or redact_details)
                                 for e in raw.get("evidence_references", [])]
                                if "evidence_references" not in hidden else []),
    }
    if "finding_id" in hidden:
        out["finding_id"] = ""
    if "severity" in hidden:
        out["severity"] = "hidden"
    if "confidence" in hidden:
        out["confidence"] = 0
    return out


def action(raw: dict, user: auth.User | None = None) -> dict:
    user = user or auth.current_user()
    now_text = datetime.now(timezone.utc).isoformat()
    events = sorted((e for e in store.list("action_event") if e.get("action_id") == raw.get("action_id")),
                    key=lambda e: e.get("timestamp", ""))
    approved = next((e for e in reversed(events) if e.get("decision") == "approve"), None)
    rejected = next((e for e in reversed(events) if e.get("decision") == "reject"), None)
    executed = next((e for e in reversed(events) if e.get("decision") == "execute"), None)
    checks = raw.get("policy_checks", [])
    risk = "high" if any(c.get("status") == "fail" for c in checks) else "medium" if any(c.get("status") == "warn" for c in checks) else "low"
    drafter = raw.get("drafted_by")
    allowed: list[str] = []
    if user.user_id == drafter or user.has("admin"):
        allowed.extend(["save_edit", "cancel"])
        if raw.get("status") == "draft":
            allowed.append("submit_review")
    if user.has("admin") and user.user_id != drafter and raw.get("status") == "in_review":
        allowed.extend(["approve", "reject"])
    if user.has("execute") and raw.get("status") == "approved":
        allowed.append("execute")
    return {
        "action_id": raw.get("action_id"), "case_id": raw.get("case_id"), "finding_id": raw.get("finding_id"),
        "action_type": raw.get("action_type"), "status": raw.get("status"), "risk_level": risk,
        "requires_human_approval": raw.get("requires_human_approval", True),
        "generated_by": raw.get("generated_by", "ai"), "created_at": raw.get("created_at", now_text),
        "updated_at": raw.get("updated_at", now_text),
        "draft_payload": {"subject": raw.get("subject", ""), "recipients": [], "body": raw.get("body", ""), "attachments": []},
        "supporting_evidence": [evidence_reference(e) for e in raw.get("supporting_evidence", [])],
        "policy_checks": checks,
        "approved_by": approved.get("actor") if approved else None,
        "approved_at": approved.get("timestamp") if approved else None,
        "rejected_by": rejected.get("actor") if rejected else None,
        "rejected_at": rejected.get("timestamp") if rejected else None,
        "rejection_reason": None, "executed_at": executed.get("timestamp") if executed else None,
        "execution_result": store.get("execution", raw.get("action_id")),
        "idempotency_key": raw.get("idempotency_key", ""), "final_content_hash": raw.get("final_content_hash"),
        "labels": raw.get("labels", []), "allowed_decisions": allowed,
    }


def approval_event(raw: dict, role: str, notes: str | None = None) -> dict:
    return {
        "event_id": f"{raw.get('action_id', '')}:{raw.get('new_status', '')}:{raw.get('timestamp', '')}",
        "actor_id": raw.get("actor", ""), "actor_role": role,
        "event_type": f"action_{raw.get('decision', '')}", "event_timestamp": raw.get("timestamp", ""),
        "previous_status": raw.get("previous_status", ""), "new_status": raw.get("new_status", ""),
        "notes": notes, "edited_fields": raw.get("edited_fields", []), "content_hash": raw.get("content_hash"),
    }


def signature(raw: dict | None, actor_id: str, signed_at: str | None = None) -> dict | None:
    if not raw:
        return None
    try:
        signed = datetime.fromisoformat(signed_at) if signed_at else datetime.now(timezone.utc)
        if signed.tzinfo is None:
            signed = signed.replace(tzinfo=timezone.utc)
    except ValueError:
        signed = datetime.now(timezone.utc)
    expires = (signed + timedelta(seconds=config.get("approval.signature_ttl_seconds", 604800))).isoformat()
    return {"signed_by": actor_id, "algorithm": raw.get("algorithm", "Ed25519"), "value": raw.get("value", ""),
            "expires_at": expires}
