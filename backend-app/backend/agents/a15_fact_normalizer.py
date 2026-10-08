"""Agent 15 - Fact Normalizer.

Sources: all linked, non-rejected sources of the case (confirmed or suggested links; explicit links are verified) that have an
assembled document; missing documents -> CONFLICT. Candidates come from rule patterns on text lines ("Label: amount")
and table rows (label in column 0, numbers in the other columns, column header = metric qualifier). The LLM is not
required; llm_guard is only consulted (when a provider exists) to propose a subject for facts whose subject is not stated,
and its answer is accepted only if grounded (entity must appear in the context) - otherwise the subject stays
"unspecified" with an ambiguity note.

normalization_rule ids (published): NUM_PLAIN, NUM_THOUSANDS_COMMA, NUM_PARENS_NEGATIVE, NUM_MULT_K, NUM_MULT_LAKH,
NUM_MULT_CRORE, NUM_MULT_MILLION, PCT_STRIP, DATE_ISO, DATE_DMY, RANGE_ABSTAIN.
Numbers: commas removed (Indian 1,00,000 and western 100,000 both -> 100000), parentheses = negative, k/lakh/crore/million
multipliers applied; ranges ("10-12k") abstain (normalized_value null + note); "approx/about/~" noted. Currency: INR (Rs, INR, rupee sign), EUR,
GBP, USD (code only); a bare "$" is ambiguous -> currency null + note. Currency and frequency are NOT converted. basis comes from the label
(gross/net) else "unknown". Every fact has >=1 evidence reference with real block_id + excerpt; others are dropped.
confidence = evidence block confidence * (0.8 if any ambiguity note else 1.0).
"""
import hashlib
import re
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Optional

from pydantic import BaseModel, ConfigDict

from ..common import audit, auth, config, llm_guard
from ..common.errors import AgentError, ErrorCode
from ..common.models import EvidenceReference, Location
from ..common.store import store
from . import a14_case_linker as linker

RULES = ["NUM_PLAIN", "NUM_THOUSANDS_COMMA", "NUM_PARENS_NEGATIVE", "NUM_MULT_K", "NUM_MULT_LAKH", "NUM_MULT_CRORE",
         "NUM_MULT_MILLION", "PCT_STRIP", "DATE_ISO", "DATE_DMY", "RANGE_ABSTAIN"]
_MULT = {"k": ("NUM_MULT_K", 1000), "lakh": ("NUM_MULT_LAKH", 100000), "lakhs": ("NUM_MULT_LAKH", 100000),
         "crore": ("NUM_MULT_CRORE", 10**7), "crores": ("NUM_MULT_CRORE", 10**7), "million": ("NUM_MULT_MILLION", 10**6),
         "m": ("NUM_MULT_MILLION", 10**6)}
_CUR = {"₹": "INR", "rs": "INR", "rs.": "INR", "inr": "INR", "usd": "USD", "eur": "EUR", "€": "EUR", "gbp": "GBP", "£": "GBP"}
_LINE = re.compile(
    r"(?P<label>[A-Za-z][A-Za-z0-9 /&()\-]{2,60}?)\s*[:=\-–]\s*(?P<approx>approx\.?|about|~)?\s*"
    r"(?P<cur>₹|Rs\.?|INR|USD|EUR|GBP|\$|€|£)?\s*"
    r"(?P<num>\(?-?\d[\d,]*(?:\.\d+)?\)?)(?:\s*(?:-|to)\s*(?P<num2>\d[\d,]*(?:\.\d+)?))?\s*"
    r"(?P<mult>k|lakhs?|crores?|million|m)?\b\s*(?P<pct>%)?\s*(?P<freq>(?:/|per\s+)\s*(?:month|week|year|annum)|p\.a\.)?",
    re.I)
_DATELINE = re.compile(r"(?P<label>[A-Za-z][A-Za-z0-9 /&()\-]{2,60}?)\s*[:=]\s*(?P<d>\d{4}-\d{2}-\d{2}|\d{1,2}[/.]\d{1,2}[/.]\d{4})")
_SUBJECT = re.compile(r"(?im)^\s*(name|employee|applicant|company|account holder|customer|borrower)\s*[:\-]\s*(.{2,60})$")
_PERIOD = re.compile(r"(\d{4}-\d{2}-\d{2})\s*(?:to|-)\s*(\d{4}-\d{2}-\d{2})")
_FREQ_WORDS = [("monthly", "monthly"), ("weekly", "weekly"), ("annual", "annual"), ("yearly", "annual"), ("per annum", "annual"),
               ("per month", "monthly"), ("per week", "weekly")]
_CATS = [("income", ("salary", "income", "wage", "earning", "revenue", "pay")), ("tax", ("tax", "gst", "tds", "vat")),
         ("expense", ("rent", "expense", "cost", "emi", "premium")), ("balance", ("balance", "closing", "opening"))]


class FactInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    case_id: str


class Fact(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fact_id: str
    subject: str
    metric: str
    raw_text: str
    raw_value: str
    normalized_value: Optional[str] = None
    currency: Optional[str] = None
    unit: Optional[str] = None
    frequency: Optional[str] = None
    period_start: Optional[str] = None
    period_end: Optional[str] = None
    category: Optional[str] = None
    basis: Optional[str] = None
    normalization_rule: str
    confidence: float
    ambiguity_notes: Optional[list[str]] = None
    evidence: list[EvidenceReference]
    source_id: str


class FactOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    facts: list[Fact]


def to_decimal(raw: str, mult: Optional[str]) -> tuple[Optional[Decimal], str]:
    t = raw.strip()
    rule = "NUM_PLAIN"
    neg = t.startswith("(") and t.endswith(")") or t.startswith("-")
    if t.startswith("(") and t.endswith(")"):
        rule = "NUM_PARENS_NEGATIVE"
    t = t.strip("()-")
    if "," in t:
        rule = rule if rule != "NUM_PLAIN" else "NUM_THOUSANDS_COMMA"
    try:
        v = Decimal(t.replace(",", ""))
    except InvalidOperation:
        return None, rule
    if mult:
        r, f = _MULT[mult.lower()]
        v, rule = v * f, r
    return (-v if neg else v), rule


def _fmt(v: Decimal) -> str:
    s = format(v, "f")
    return s.rstrip("0").rstrip(".") if "." in s else s


def to_iso(d: str) -> tuple[Optional[str], str]:
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", d):
        try:
            date.fromisoformat(d)
            return d, "DATE_ISO"
        except ValueError:
            return None, "DATE_ISO"
    a, b, c = re.split(r"[/.]", d)
    try:
        return date(int(c), int(b), int(a)).isoformat(), "DATE_DMY"
    except ValueError:
        return None, "DATE_DMY"


def _category(label: str) -> Optional[str]:
    l = label.lower()
    for cat, words in _CATS:
        if any(w in l for w in words):
            return cat
    return None


def _basis(label: str) -> str:
    l = label.lower()
    return "gross" if "gross" in l else "net" if re.search(r"\bnet\b|take.?home", l) else "unknown"


def _mk(case_id, source_id, block, line, label, raw, nv, rule, cur, freq, period, notes, subject, qualifier):
    metric = (label.strip() + (f" ({qualifier})" if qualifier else "")).strip().lower()
    metric = re.sub(r"\s+", " ", metric)
    fid = hashlib.sha256(f"{case_id}|{source_id}|{block['block_id']}|{metric}|{raw}".encode()).hexdigest()[:16]
    loc = block["evidence"]["location"]
    conf = block["evidence"]["confidence"] * (0.8 if notes else 1.0)
    return Fact(fact_id=fid, subject=subject, metric=metric, raw_text=line.strip(), raw_value=raw,
                normalized_value=nv, currency=cur, unit="date" if rule.startswith("DATE") else None, frequency=freq,
                period_start=period[0] if period else None, period_end=period[1] if period else None,
                category=_category(label), basis=_basis(label), normalization_rule=rule, confidence=round(conf, 4),
                ambiguity_notes=notes or None, source_id=source_id,
                evidence=[EvidenceReference(source_id=source_id, block_id=block["block_id"], page_id=block["evidence"].get("page_id"),
                                            excerpt=line.strip()[:300], location=Location(**loc))])


def _from_line(case_id, source_id, block, line, subject, period, base_notes):
    out = []
    for m in _DATELINE.finditer(line):
        iso, rule = to_iso(m.group("d"))
        notes = list(base_notes) + ([] if iso else ["date is not a valid calendar date"])
        out.append(_mk(case_id, source_id, block, line, m.group("label"), m.group("d"), iso, rule, None, None, None, notes, subject, None))
    m = _LINE.search(line)
    if m and not _DATELINE.search(line):
        notes = list(base_notes)
        rawnum = m.group("num")
        mult, rule, nv = m.group("mult"), "NUM_PLAIN", None
        if m.group("num2"):
            notes.append("value given as a range; no single value chosen")
            rule = "RANGE_ABSTAIN"
            raw = f"{rawnum}-{m.group('num2')}"
        else:
            val, rule = to_decimal(rawnum, mult)
            raw = rawnum
            nv = _fmt(val) if val is not None else None
            if m.group("pct"):
                rule = "PCT_STRIP"
        if m.group("approx"):
            notes.append("value marked approximate")
        cur = None
        if m.group("cur"):
            key = m.group("cur").lower()
            if key == "$":
                notes.append("currency symbol '$' is ambiguous; currency left unset")
            else:
                cur = _CUR.get(key)
        freq = None
        fw = m.group("freq")
        if fw:
            freq = "monthly" if "month" in fw.lower() else "weekly" if "week" in fw.lower() else "annual"
        else:
            for w, f in _FREQ_WORDS:
                if w in m.group("label").lower():
                    freq = f
                    break
        if len(m.group("label").strip()) >= 3:
            f = _mk(case_id, source_id, block, line, m.group("label"), raw, nv, rule, cur, freq, period, notes, subject, None)
            f.unit = "percent" if m.group("pct") else None
            out.append(f)
    return out


def _from_table(case_id, source_id, block, subject, period, base_notes):
    t = block["data"]
    grid = {(c["row"], c["col"]): c for c in t["cells"]}
    hdr = {c["col"]: c["raw_text"] for c in t["cells"] if c["is_header"]}
    out = []
    for r in range(1 if hdr else 0, t["n_rows"]):
        lab = grid.get((r, 0))
        if not lab or not lab["raw_text"] or re.fullmatch(r"[\d,.() -]+", lab["raw_text"]):
            continue
        for c in range(1, t["n_cols"]):
            cell = grid.get((r, c))
            if not cell or not cell["raw_text"]:
                continue
            m = re.fullmatch(r"\s*(₹|Rs\.?|INR|USD|EUR|GBP|\$|€|£)?\s*(\(?-?\d[\d,]*(?:\.\d+)?\)?)\s*(k|lakhs?|crores?|million|m)?\s*", cell["raw_text"], re.I)
            if not m:
                continue
            notes = list(base_notes)
            val, rule = to_decimal(m.group(2), m.group(3))
            cur = None
            if m.group(1):
                if m.group(1) == "$":
                    notes.append("currency symbol '$' is ambiguous; currency left unset")
                else:
                    cur = _CUR.get(m.group(1).lower())
            line = f"{lab['raw_text']} | {hdr.get(c, '')} | {cell['raw_text']}"
            f = _mk(case_id, source_id, block, line, lab["raw_text"], cell["raw_text"],
                    _fmt(val) if val is not None else None, rule, cur, None, period, notes, subject, hdr.get(c))
            f.evidence[0].location = Location(**cell["location"])
            out.append(f)
    return out


def run(inp: FactInput) -> FactOutput:
    c = store.get("case", inp.case_id)
    if c is None:
        raise AgentError(ErrorCode.NOT_FOUND, "Case not found")
    u = auth.current_user()
    if c.get("tenant_id", "default") != u.tenant_id and not u.has("admin"):
        raise AgentError(ErrorCode.FORBIDDEN, "Case not accessible")
    links = linker.case_links(inp.case_id)
    sids = sorted({l["source_id"] for l in links})
    missing = [s for s in sids if store.get("document", s) is None]
    if missing:
        raise AgentError(ErrorCode.CONFLICT, "Sources without assembled documents", {"missing": missing})
    facts: list[Fact] = []
    for sid in sids:
        doc = store.get("document", sid)
        text = linker.source_text(sid)
        sm = _SUBJECT.search(text)
        subject = sm.group(2).strip() if sm else "unspecified"
        notes0 = [] if sm else ["subject not stated in the document"]
        pm = _PERIOD.search(text)
        period = (pm.group(1), pm.group(2)) if pm else None
        if not sm and llm_guard.provider is not None:
            class _S(BaseModel):
                subject: Optional[str] = None
            g = llm_guard.call("Name the person or organisation this document concerns, if explicitly stated.",
                               [{"block_id": b["block_id"], "text": b.get("text") or ""} for p in doc["pages"] for b in p["blocks"] if b.get("text")],
                               _S, entity_fields=["subject"])
            if g["ok"] and g["output"].get("subject"):
                subject, notes0 = g["output"]["subject"], ["subject proposed by model and verified in text"]
        for p in doc["pages"]:
            for b in p["blocks"]:
                if b["type"] == "table" and b.get("data"):
                    facts += _from_table(inp.case_id, sid, b, subject, period, notes0)
                elif b.get("text"):
                    for line in b["text"].split("\n"):
                        facts += _from_line(inp.case_id, sid, b, line, subject, period, notes0)
    uniq = {}
    for f in facts:
        if f.evidence and f.evidence[0].block_id:
            uniq[f.fact_id] = f
    out = FactOutput(facts=sorted(uniq.values(), key=lambda f: (f.subject, f.metric, f.source_id, f.fact_id)))
    store.put("facts", inp.case_id, out.model_dump(mode="json"))
    audit.append(event_type="facts_normalized", object_type="case", object_id=inp.case_id, case_id=inp.case_id,
                 details={"facts": len(out.facts), "sources": len(sids)})
    return out
