"""Agent 12 - Confidence & Validation. Deterministic.

Per block: components = engine (evidence.confidence), agreement (unanimous 1.0 / majority 0.75 / split 0.4, only for
consensus-backed text blocks), layout (region confidence), validation (1 - failed/total checks, only when checks ran).
confidence = sum(w_i * v_i) / sum(w_i) over the AVAILABLE components, weights from config.confidence.weights;
confidence_breakdown lists each available component's raw value.
Validators (each {name,status,detail}): arithmetic (table badges / total rows), date_validity (+ range ordering),
currency_format (malformed separators), checksum (IBAN mod-97, Luhn for 13-19 digit numbers labelled card),
cross_block_consistency (same "label: number" with different values across blocks), unit_sanity (magnitude/percent bounds).
needs_review = confidence < bands.medium OR any check failed.
document_confidence = 0.7 * area_weighted_mean(block confidences) + 0.3 * min(confidence of blocks with a failed
check) when any block has a failed check, else area_weighted_mean. Blocks without positive area weigh 1 px.
Badges: table-scope badges are re-emitted from the table; document-scope "Totals verified"/"Totals mismatch" when any
table had a computable total; page-scope "Page needs review" when a block on the page needs review.
"""
import re
from datetime import date
from typing import Optional

from pydantic import BaseModel, ConfigDict

from ..common import audit, config
from ..common.errors import AgentError, ErrorCode
from ..common.models import Badge, Check
from ..common.store import store

_AGREE = {"unanimous": 1.0, "majority": 0.75, "split": 0.4}
_DATE_ISO = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_DATE_DMY = re.compile(r"\b(\d{1,2})[/.](\d{1,2})[/.](\d{4})\b")
_BAD_NUM = re.compile(r"\b\d{1,3}(,\d{1,2})(,\d{3})*(?!\d)|\d+,,\d+|\d+\.\d+\.\d+(?:\.\d+)*\b")
_LABEL_NUM = re.compile(r"([A-Za-z][A-Za-z ]{2,30}):\s*([\d,]+(?:\.\d+)?)")
_IBAN = re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b")
_CARD = re.compile(r"(?i)card[^\d]{0,15}(\d[\d -]{11,22}\d)")


class ConfidenceInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_id: str


class BlockConfidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    block_id: str
    confidence: float
    confidence_breakdown: dict[str, float]
    checks: list[Check]
    needs_review: bool


class ConfidenceOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document_confidence: float
    blocks: list[BlockConfidence]
    badges: list[Badge]


def _iban_ok(s: str) -> bool:
    r = s[4:] + s[:4]
    n = "".join(str(int(c, 36)) for c in r)
    return int(n) % 97 == 1


def _luhn(num: str) -> bool:
    d = [int(c) for c in num if c.isdigit()]
    s = sum(x if i % 2 == 0 else (x * 2 - 9 if x * 2 > 9 else x * 2) for i, x in enumerate(reversed(d)))
    return s % 10 == 0


def _valid_date(y: int, m: int, d: int) -> bool:
    try:
        date(y, m, d)
        return True
    except ValueError:
        return False


def _text_of(b: dict) -> str:
    if b.get("text"):
        return b["text"]
    if b.get("data") and "cells" in b["data"]:
        return "\n".join(c["raw_text"] for c in b["data"]["cells"])
    return ""


def validate_text(text: str) -> list[Check]:
    checks: list[Check] = []
    dates = [(int(a), int(b), int(c)) for a, b, c in _DATE_ISO.findall(text)] + \
            [(int(c), int(b), int(a)) for a, b, c in _DATE_DMY.findall(text)]
    if dates:
        bad = [d for d in dates if not _valid_date(*d)]
        checks.append(Check(name="date_validity", status="fail" if bad else "pass",
                            detail=f"{len(bad)} invalid of {len(dates)}" if bad else None))
        valid = [date(*d) for d in dates if _valid_date(*d)]
        if re.search(r"(?i)\b(from|between)\b.*\b(to|and)\b", text) and len(valid) >= 2:
            checks.append(Check(name="date_ordering", status="pass" if valid[0] <= valid[1] else "fail",
                                detail=None if valid[0] <= valid[1] else "range end precedes start"))
    if _BAD_NUM.search(text):
        checks.append(Check(name="currency_format", status="warn", detail="Malformed digit grouping"))
    for m in _IBAN.findall(text):
        checks.append(Check(name="checksum_iban", status="pass" if _iban_ok(m) else "fail"))
    for m in _CARD.findall(text):
        digits = re.sub(r"\D", "", m)
        if 13 <= len(digits) <= 19:
            checks.append(Check(name="checksum_luhn", status="pass" if _luhn(digits) else "fail"))
    big = config.get("rules.unit_sanity_max", 1e9)
    nums = [float(n.replace(",", "")) for n in re.findall(r"(?<![\w.])\d[\d,]*(?:\.\d+)?(?![\w])", text) if n.replace(",", "").replace(".", "").isdigit()]
    if nums:
        pct_bad = [p for p in re.findall(r"(\d+(?:\.\d+)?)%", text) if float(p) > 1000]
        checks.append(Check(name="unit_sanity", status="warn" if (max(nums) > big or pct_bad) else "pass",
                            detail="Magnitude outside expected bounds" if (max(nums) > big or pct_bad) else None))
    return checks


def run(inp: ConfidenceInput) -> ConfidenceOutput:
    doc = store.get("document", inp.source_id)
    if doc is None:
        raise AgentError(ErrorCode.CONFLICT, "Run json-assembly first", {"missing": ["document"]})
    w = config.get("confidence.weights")
    band = config.get("bands.medium", 0.6)
    cons = {}
    for p in doc["pages"]:
        for c in (store.get("consensus", f"{inp.source_id}:{p['page_number']}") or {"blocks": []})["blocks"]:
            cons[c["block_id"]] = c
    labelled: dict[str, set] = {}
    for p in doc["pages"]:
        for b in p["blocks"]:
            for lab, val in _LABEL_NUM.findall(_text_of(b)):
                labelled.setdefault(lab.strip().lower(), set()).add(val.replace(",", ""))
    results, badges, area_w, failed_conf = [], [], [], []
    tot_pass = tot_mis = 0
    for p in doc["pages"]:
        page_review = False
        for b in p["blocks"]:
            checks = validate_text(_text_of(b))
            if b["type"] == "table" and b.get("data"):
                for bd in b["data"].get("badges", []):
                    checks.append(Check(name="arithmetic", status="pass" if bd["status"] == "pass" else "fail", detail=bd.get("detail")))
                    badges.append(Badge(scope_id=b["block_id"], label=bd["label"], status=bd["status"], detail=bd.get("detail")))
                    tot_pass += bd["status"] == "pass"
                    tot_mis += bd["status"] != "pass"
            for lab, val in _LABEL_NUM.findall(_text_of(b)):
                if len(labelled.get(lab.strip().lower(), ())) > 1:
                    checks.append(Check(name="cross_block_consistency", status="warn", detail=f"'{lab.strip()}' appears with differing values"))
            comp = {"engine": b["evidence"]["confidence"]}
            c = cons.get(b["block_id"])
            ag = (b.get("extra") or {}).get("agreement")
            if ag:
                comp["agreement"] = min(_AGREE.get(a, 0.5) for a in ag)
            elif c:
                comp["agreement"] = _AGREE.get(c["agreement"], 0.5)
            lay = next((r for r in (store.get("layout", p["page_id"]) or {"regions": []})["regions"] if r["region_id"] == b["block_id"]), None)
            if lay:
                comp["layout"] = lay["confidence"]
            if checks:
                comp["validation"] = 1 - sum(ck.status == "fail" for ck in checks) / len(checks)
            conf = sum(w[k] * v for k, v in comp.items()) / sum(w[k] for k in comp)
            fail = any(ck.status == "fail" for ck in checks)
            nr = conf < band or fail or bool((b.get("extra") or {}).get("needs_review"))
            page_review |= nr
            bb = b["evidence"]["location"].get("bbox") or [0, 0, 1, 1]
            area_w.append(max((bb[2] - bb[0]) * (bb[3] - bb[1]), 1.0))
            if fail:
                failed_conf.append(conf)
            results.append(BlockConfidence(block_id=b["block_id"], confidence=round(conf, 4),
                                           confidence_breakdown={k: round(v, 4) for k, v in comp.items()},
                                           checks=checks, needs_review=nr))
        if page_review:
            badges.append(Badge(scope_id=p["page_id"], label="Page needs review", status="warn"))
    if results:
        mean = sum(r.confidence * a for r, a in zip(results, area_w)) / sum(area_w)
        doc_conf = 0.7 * mean + 0.3 * min(failed_conf) if failed_conf else mean
    else:
        doc_conf = 0.0
    if tot_pass or tot_mis:
        badges.append(Badge(scope_id=inp.source_id, label="Totals mismatch" if tot_mis else "Totals verified",
                            status="mismatch" if tot_mis else "pass", detail=f"{tot_pass} passed, {tot_mis} mismatched"))
    out = ConfidenceOutput(document_confidence=round(doc_conf, 4), blocks=results, badges=badges)
    store.put("confidence", inp.source_id, out.model_dump(mode="json"))
    audit.append(event_type="validated", object_type="source", object_id=inp.source_id,
                 details={"document_confidence": out.document_confidence, "needs_review": sum(r.needs_review for r in results)})
    return out
