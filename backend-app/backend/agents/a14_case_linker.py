"""Agent 14 - Case Linker (deterministic). Endpoints: POST /agents/case-linker and /agents/case-linker/decision.

relationship_type enum (published): same_party, supporting_document, supersedes, unknown.
explicit: links are created exactly as asked (linked_by = user id, human_verified=true, confidence 1.0, signals
["user_assigned"]). suggest: each new source is compared with the case's existing, non-rejected sources using signals;
suggestions are NEVER auto-confirmed (human_verified=false):
  shared_entity  - capitalised multi-word names with rapidfuzz token_set_ratio >= 90        strength = min(1, shared/3)
  shared_id      - identifiers (letters+digits >= 6 chars, or 6+ digit runs) in both, exact   strength = min(1, shared/2)
  date_proximity - any pair of dates within 30 days                                          strength = 1
  same_domain    - e-mail domains in common                                                  strength = 1
(text-embedding similarity is a documented optional signal, not enabled in this build).
confidence = sum(config.case_linker.weights[signal] * strength) capped at 1.0; suggestions below
config.case_linker.suggest_min_confidence are not created. link_id = sha256(case|source)[:16] so duplicates are idempotent.
Decision: confirm -> human_verified=true; reject -> status "rejected" kept for audit (also allowed after confirm).
"""
import hashlib
import re
from datetime import date, datetime, timezone
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict
from rapidfuzz import fuzz

from ..common import audit, auth, config
from ..common.errors import AgentError, ErrorCode
from ..common.store import store

_ENTITY = re.compile(r"\b([A-Z][a-zA-Z&.]+(?:\s+[A-Z][a-zA-Z&.]+){1,3})\b")
_ID = re.compile(r"\b(?=[A-Z0-9-]{6,}\b)(?=[A-Z0-9-]*\d)[A-Z0-9-]{6,}\b|\b\d{6,}\b")
_DATE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b|\b(\d{1,2})[/.](\d{1,2})[/.](\d{4})\b")
_EMAIL = re.compile(r"@([A-Za-z0-9.-]+\.[A-Za-z]{2,})")


class LinkerInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    case_id: str
    source_ids: list[str]
    mode: Literal["explicit", "suggest"]


class Link(BaseModel):
    model_config = ConfigDict(extra="forbid")
    link_id: str
    case_id: str
    source_id: str
    relationship_type: str
    relationship_confidence: float
    matching_signals: list[str]
    linked_by: str
    linked_at: str
    human_verified: bool


class LinkerOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    links: list[Link]


class DecisionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    link_id: str
    decision: Literal["confirm", "reject"]


class DecisionOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    link_id: str
    human_verified: bool


def link_id(case_id: str, source_id: str) -> str:
    return hashlib.sha256(f"{case_id}|{source_id}".encode()).hexdigest()[:16]


def source_text(source_id: str) -> str:
    doc = store.get("document", source_id)
    if not doc:
        return ""
    parts = []
    for p in doc["pages"]:
        for b in p["blocks"]:
            if b.get("text"):
                parts.append(b["text"])
            if b.get("data") and "cells" in b["data"]:
                parts += [c["raw_text"] for c in b["data"]["cells"]]
    return "\n".join(parts)


def _dates(text: str) -> list[date]:
    out = []
    for m in _DATE.findall(text):
        try:
            out.append(date(int(m[0]), int(m[1]), int(m[2])) if m[0] else date(int(m[5]), int(m[4]), int(m[3])))
        except ValueError:
            pass
    return out


def signals(a: str, b: str) -> list[tuple[str, str, float]]:
    out = []
    ea, eb = set(_ENTITY.findall(a)), set(_ENTITY.findall(b))
    shared = sorted({x for x in ea for y in eb if fuzz.token_set_ratio(x, y) >= 90})
    if shared:
        out.append(("shared_entity", shared[0], min(1.0, len(shared) / 3)))
    ids = sorted(set(_ID.findall(a)) & set(_ID.findall(b)))
    if ids:
        out.append(("shared_id", ids[0], min(1.0, len(ids) / 2)))
    da, db = _dates(a), _dates(b)
    if any(abs((x - y).days) <= 30 for x in da for y in db):
        out.append(("date_proximity", "within_30_days", 1.0))
    dom = sorted(set(_EMAIL.findall(a)) & set(_EMAIL.findall(b)))
    if dom:
        out.append(("same_domain", dom[0], 1.0))
    return out


def _case(case_id: str) -> dict:
    c = store.get("case", case_id)
    if c is None:
        raise AgentError(ErrorCode.NOT_FOUND, "Case not found")
    u = auth.current_user()
    if c.get("tenant_id", "default") != u.tenant_id and not u.has("admin"):
        raise AgentError(ErrorCode.FORBIDDEN, "Case not accessible")
    return c


def case_links(case_id: str, include_rejected: bool = False) -> list[dict]:
    return [l for l in store.list("link") if l["case_id"] == case_id and (include_rejected or l.get("status") != "rejected")]


def run(inp: LinkerInput) -> LinkerOutput:
    u = auth.current_user()
    _case(inp.case_id)
    for sid in inp.source_ids:
        source = store.get("source", sid)
        if source is None:
            raise AgentError(ErrorCode.NOT_FOUND, "Source not found")
        if source.get("tenant_id", "default") != u.tenant_id and not u.has("admin"):
            raise AgentError(ErrorCode.FORBIDDEN, "Source not accessible")
    now = datetime.now(timezone.utc).isoformat()
    existing = case_links(inp.case_id)
    out = []
    for sid in sorted(set(inp.source_ids)):
        lid = link_id(inp.case_id, sid)
        prev = store.get("link", lid)
        if inp.mode == "explicit":
            if prev and prev.get("status") != "rejected" and prev["human_verified"]:
                out.append(prev)
                continue
            rec = {"link_id": lid, "case_id": inp.case_id, "source_id": sid, "relationship_type": "unknown",
                   "relationship_confidence": 1.0, "matching_signals": ["user_assigned"], "linked_by": u.user_id,
                   "linked_at": now, "human_verified": True, "status": "active"}
            store.put("link", lid, rec)
            audit.append(event_type="link_confirmed", object_type="link", object_id=lid,
                         details={"mode": "explicit", "case_id": inp.case_id, "source_id": sid}, case_id=inp.case_id)
            out.append(rec)
            continue
        if prev and prev.get("status") != "rejected":
            out.append(prev)
            continue
        ta = source_text(sid)
        best_sig: list = []
        for ex in existing:
            if ex["source_id"] == sid:
                continue
            sg = signals(ta, source_text(ex["source_id"]))
            if sum(s[2] for s in sg) > sum(s[2] for s in best_sig):
                best_sig = sg
        w = config.get("case_linker.weights")
        conf = min(1.0, sum(w[n] * s for n, _, s in best_sig))
        if conf < config.get("case_linker.suggest_min_confidence", 0.3):
            continue
        names = {n for n, _, _ in best_sig}
        rtype = "same_party" if names & {"shared_entity", "shared_id"} else ("supporting_document" if names else "unknown")
        rec = {"link_id": lid, "case_id": inp.case_id, "source_id": sid, "relationship_type": rtype,
               "relationship_confidence": round(conf, 4), "matching_signals": [f"{n}={v}" for n, v, _ in best_sig],
               "linked_by": "system:suggest", "linked_at": now, "human_verified": False, "status": "suggested"}
        store.put("link", lid, rec)
        audit.append(event_type="link_suggested", object_type="link", object_id=lid,
                     details={"confidence": rec["relationship_confidence"], "signals": [n for n, _, _ in best_sig]}, case_id=inp.case_id)
        out.append(rec)
    return LinkerOutput(links=[Link(**{k: v for k, v in r.items() if k != "status"}) for r in out])


def decide(inp: DecisionInput) -> DecisionOutput:
    rec = store.get("link", inp.link_id)
    if rec is None:
        raise AgentError(ErrorCode.NOT_FOUND, "Link not found")
    _case(rec["case_id"])
    prev = {"human_verified": rec["human_verified"], "status": rec.get("status")}
    if inp.decision == "confirm":
        rec.update(human_verified=True, status="active")
        et = "link_confirmed"
    else:
        rec.update(human_verified=False, status="rejected")
        et = "link_rejected"
    store.put("link", inp.link_id, rec)
    audit.append(event_type=et, object_type="link", object_id=inp.link_id, case_id=rec["case_id"],
                 details={"previous": prev})
    return DecisionOutput(link_id=inp.link_id, human_verified=rec["human_verified"])
