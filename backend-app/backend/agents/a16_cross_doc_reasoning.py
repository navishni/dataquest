"""Agent 16 - Cross-Document Reasoning. Deterministic; all arithmetic is code (Decimal), no LLM.

Facts (agent 15) are grouped by (subject, metric). Facts from DIFFERENT sources are compared pairwise (sorted by source id,
so 3+ documents and one-vs-many are covered). A pair is comparable only if every check passes:
  same_currency (both known and equal), compatible_frequency (equal, both unset), compatible_period (both unset, or start/end
  within config.reasoning.period_tolerance_days), same_basis (equal), same_category (equal).
Failures go to not_comparable with a plain reason (never silently compared).
Comparison: abs_diff = |a-b|; pct_diff = abs_diff / max(|a|,|b|) * 100 (0 when both are zero - no division by zero).
A finding is created only when comparable and pct_diff > config.reasoning.tolerance_pct. Text is neutral
("Potential discrepancy: ... differs between ... and ..."); human_review_required is always true;
recommended_review_action = "Manual review recommended.". possible_explanations come from a rule table
(rounding, different period, different basis, possible typo/digit transposition, missing document), each tied to the check that
suggests it. severity: pct >= severity_thresholds.high_pct -> high, >= medium_pct -> medium, else low.
finding confidence = min(fact confidences) * 0.9 if either basis is "unknown" else min(...). finding_id = sha256(sorted fact ids + rule)[:16]
(idempotent). High-severity findings notify the admin (no values in the notification).
"""
import hashlib
from decimal import Decimal
from datetime import date
from typing import Optional

from pydantic import BaseModel, ConfigDict

from ..common import audit, auth, config, notify
from ..common.errors import AgentError, ErrorCode
from ..common.models import Check, EvidenceReference
from ..common.store import store

RULE = "VALUE_DIFFERENCE_V1"


class ReasoningInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    case_id: str


class Comparison(BaseModel):
    model_config = ConfigDict(extra="forbid")
    comparison_id: str
    subject: str
    metric: str
    fact_ids: list[str]
    values: list[str]
    abs_diff: str
    pct_diff: float
    checks: list[Check]
    exceeds_tolerance: bool


class NotComparable(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fact_ids: list[str]
    subject: str
    metric: str
    reason: str
    checks: list[Check]


class Explanation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    explanation: str
    suggested_by: str


class Finding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    finding_id: str
    case_id: str
    category: str = "discrepancy"
    status: str = "open"
    title: str
    statement: str
    severity: str
    confidence: float
    possible_explanations: list[Explanation]
    human_review_required: bool = True
    recommended_review_action: str = "Manual review recommended."
    evidence_references: list[EvidenceReference]
    fact_ids: list[str]


class ReasoningOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    comparisons: list[Comparison]
    not_comparable: list[NotComparable]
    findings: list[Finding]


def _days(a: Optional[str], b: Optional[str]) -> Optional[int]:
    if not a or not b:
        return None
    return abs((date.fromisoformat(a) - date.fromisoformat(b)).days)


def _checks(a: dict, b: dict) -> list[Check]:
    tol = config.get("reasoning.period_tolerance_days", 5)
    ch = []
    ca, cb = a.get("currency"), b.get("currency")
    ch.append(Check(name="same_currency", status="pass" if ca and cb and ca == cb else "fail" if (ca or cb) and ca != cb else "warn" if not ca and not cb else "fail",
                    detail=f"{ca or 'unset'} vs {cb or 'unset'}"))
    # both unset currency (e.g. dates, percentages) is allowed to compare but flagged
    if not ca and not cb:
        ch[-1] = Check(name="same_currency", status="pass", detail="no currency on either fact")
    ch.append(Check(name="compatible_frequency", status="pass" if a.get("frequency") == b.get("frequency") else "fail",
                    detail=f"{a.get('frequency') or 'unset'} vs {b.get('frequency') or 'unset'}"))
    ds, de = _days(a.get("period_start"), b.get("period_start")), _days(a.get("period_end"), b.get("period_end"))
    if (a.get("period_start") or b.get("period_start")):
        okp = ds is not None and de is not None and ds <= tol and de <= tol
    else:
        okp = True
    ch.append(Check(name="compatible_period", status="pass" if okp else "fail",
                    detail="periods match within tolerance" if okp else "periods differ or one is missing"))
    ch.append(Check(name="same_basis", status="pass" if a.get("basis") == b.get("basis") else "fail",
                    detail=f"{a.get('basis')} vs {b.get('basis')}"))
    ch.append(Check(name="same_category", status="pass" if a.get("category") == b.get("category") else "fail",
                    detail=f"{a.get('category') or 'unset'} vs {b.get('category') or 'unset'}"))
    return ch


def _explanations(a: dict, b: dict, pct: Decimal, checks: list[Check]) -> list[Explanation]:
    ex = []
    if pct < 1:
        ex.append(Explanation(explanation="Possible rounding difference", suggested_by="pct_diff_below_1"))
    if a.get("basis") == "unknown" or b.get("basis") == "unknown":
        ex.append(Explanation(explanation="Basis (gross/net) is not stated in at least one document", suggested_by="same_basis"))
    if not a.get("period_start") or not b.get("period_start"):
        ex.append(Explanation(explanation="Period is not stated in at least one document; a different period is possible", suggested_by="compatible_period"))
    da, db = (a.get("normalized_value") or "").replace(".", "").replace("-", ""), (b.get("normalized_value") or "").replace(".", "").replace("-", "")
    if da and sorted(da) == sorted(db) and da != db:
        ex.append(Explanation(explanation="Possible typo (digit transposition)", suggested_by="digit_comparison"))
    ex.append(Explanation(explanation="A supporting document may be missing", suggested_by="coverage"))
    return ex


def run(inp: ReasoningInput) -> ReasoningOutput:
    c = store.get("case", inp.case_id)
    if c is None:
        raise AgentError(ErrorCode.NOT_FOUND, "Case not found")
    u = auth.current_user()
    if c.get("tenant_id", "default") != u.tenant_id and not u.has("admin"):
        raise AgentError(ErrorCode.FORBIDDEN, "Case not accessible")
    fx = store.get("facts", inp.case_id)
    if fx is None:
        raise AgentError(ErrorCode.CONFLICT, "Run fact-normalizer first", {"missing": ["facts"]})
    groups: dict[tuple, list[dict]] = {}
    for f in fx["facts"]:
        if f["normalized_value"] is None:
            continue
        groups.setdefault((f["subject"].lower(), f["metric"]), []).append(f)
    tol = Decimal(str(config.get("reasoning.tolerance_pct", 0.5)))
    th = config.get("severity_thresholds")
    comps, nc, finds = [], [], []
    for (subj, metric), fs in sorted(groups.items()):
        fs = sorted(fs, key=lambda f: (f["source_id"], f["fact_id"]))
        for i in range(len(fs)):
            for j in range(i + 1, len(fs)):
                a, b = fs[i], fs[j]
                if a["source_id"] == b["source_id"]:
                    continue
                checks = _checks(a, b)
                failed = [k for k in checks if k.status == "fail"]
                if failed:
                    nc.append(NotComparable(fact_ids=[a["fact_id"], b["fact_id"]], subject=a["subject"], metric=metric,
                                            reason="Not comparable: " + "; ".join(f"{k.name} ({k.detail})" for k in failed), checks=checks))
                    continue
                va, vb = Decimal(a["normalized_value"]) if a.get("unit") != "date" else None, None
                if a.get("unit") == "date":
                    d = abs((date.fromisoformat(a["normalized_value"]) - date.fromisoformat(b["normalized_value"])).days)
                    absd, pct = Decimal(d), (Decimal(0) if d == 0 else Decimal(100))
                    vals = [a["normalized_value"], b["normalized_value"]]
                else:
                    va, vb = Decimal(a["normalized_value"]), Decimal(b["normalized_value"])
                    absd = abs(va - vb)
                    mx = max(abs(va), abs(vb))
                    pct = Decimal(0) if mx == 0 else absd / mx * 100
                    vals = [a["normalized_value"], b["normalized_value"]]
                cid = hashlib.sha256(f"{a['fact_id']}|{b['fact_id']}|cmp".encode()).hexdigest()[:16]
                exceeds = pct > tol
                comps.append(Comparison(comparison_id=cid, subject=a["subject"], metric=metric, fact_ids=[a["fact_id"], b["fact_id"]],
                                        values=vals, abs_diff=format(absd.normalize(), "f"), pct_diff=float(round(pct, 4)),
                                        checks=checks, exceeds_tolerance=exceeds))
                audit.append(event_type="comparison_run", object_type="comparison", object_id=cid, case_id=inp.case_id)
                if not exceeds:
                    continue
                fid = hashlib.sha256(("|".join(sorted([a["fact_id"], b["fact_id"]])) + RULE).encode()).hexdigest()[:16]
                na = (store.get("source", a["source_id"]) or {}).get("display_name", a["source_id"])
                nb = (store.get("source", b["source_id"]) or {}).get("display_name", b["source_id"])
                sev = "high" if pct >= Decimal(str(th["high_pct"])) else "medium" if pct >= Decimal(str(th["medium_pct"])) else "low"
                conf = min(a["confidence"], b["confidence"]) * (0.9 if "unknown" in (a["basis"], b["basis"]) else 1.0)
                find = Finding(finding_id=fid, case_id=inp.case_id,
                               status=(store.get("finding", fid) or {}).get("status", "open"),
                               title=f"Potential discrepancy: {metric} differs between {na} and {nb}",
                               statement=(f"Potential discrepancy: {metric} for {a['subject']} is {vals[0]} in {na} and {vals[1]} in {nb} "
                                          f"(difference {format(absd.normalize(), 'f')}, {round(pct, 2)}%). No final decision has been made."),
                               severity=sev, confidence=round(conf, 4), possible_explanations=_explanations(a, b, pct, checks),
                               evidence_references=[EvidenceReference(**e) for e in a["evidence"] + b["evidence"]],
                               fact_ids=[a["fact_id"], b["fact_id"]])
                finds.append(find)
                is_new = store.get("finding", fid) is None
                store.put("finding", fid, find.model_dump(mode="json"))
                if is_new:
                    audit.append(event_type="finding_created", object_type="finding", object_id=fid, case_id=inp.case_id,
                                 details={"severity": sev})
                    if sev == "high":
                        notify.send("finding_created", "high", "High-severity finding created",
                                    f"case={inp.case_id} finding={fid} user={u.user_id}", dedupe_key=f"finding:{fid}")
    out = ReasoningOutput(comparisons=comps, not_comparable=nc, findings=sorted(finds, key=lambda f: f.finding_id))
    store.put("reasoning", inp.case_id, out.model_dump(mode="json"))
    return out
