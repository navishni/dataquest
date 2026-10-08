"""Queryable resources (tables) and their columns, used by agent 23 (access) and agent 24 (chat-sql).

RESOURCES declares every table, column and whether the column is sensitive (needs an explicit grant unless the role default
is "all"). loaders(user) return ONLY the rows the caller may see (row-level filtering is applied here, i.e. in the data
layer before any query). Nothing here contains sample data: rows are derived from what the pipeline stored.
"""
import json

from . import auth, config
from .store import store

RESOURCES: dict[str, dict[str, bool]] = {  # resource -> {column: sensitive}
    "sources": {"source_id": False, "display_name": False, "detected_mime": False, "size_bytes": False,
                "page_count": False, "uploaded_by": True},
    "blocks": {"block_id": False, "source_id": False, "page_number": False, "block_type": False,
               "text": True, "confidence": False},
    "facts": {"fact_id": False, "case_id": False, "source_id": False, "subject": True, "metric": False,
              "raw_value": True, "normalized_value": True, "currency": False, "frequency": False,
              "category": False, "basis": False, "confidence": False, "raw_text": True,
              "unit": False, "period_start": True, "period_end": True, "normalization_rule": False,
              "ambiguity_notes": True, "evidence": True},
    "findings": {"finding_id": False, "case_id": False, "title": True, "statement": True,
                 "severity": False, "confidence": False, "category": False, "status": False,
                 "difference_absolute": True, "difference_percentage": True, "possible_explanations": True,
                 "recommended_review_action": False, "evidence_references": True},
}


def _owns_source(u: auth.User, sid: str) -> bool:
    m = store.get("source", sid)
    return bool(m) and (u.has("admin") or m.get("tenant_id", config.get("tenant_id", "default")) == u.tenant_id)


def _owns_case(u: auth.User, cid: str) -> bool:
    c = store.get("case", cid)
    return bool(c) and (u.has("admin") or c.get("tenant_id", config.get("tenant_id", "default")) == u.tenant_id)


def load_rows(resource: str, u: auth.User) -> list[dict]:
    rows: list[dict] = []
    if resource == "sources":
        for m in store.list("source"):
            if _owns_source(u, m["source_id"]):
                rows.append({k: m.get(k) for k in RESOURCES["sources"]})
    elif resource == "blocks":
        for d in store.list("document"):
            if not _owns_source(u, d["source_id"]):
                continue
            for p in d["pages"]:
                for b in p["blocks"]:
                    rows.append({"block_id": b["block_id"], "source_id": d["source_id"], "page_number": p["page_number"],
                                 "block_type": b["type"], "text": b.get("text"), "confidence": b["evidence"]["confidence"]})
    elif resource == "facts":
        for cid in sorted({c["case_id"] for c in store.list("case")}):
            if not _owns_case(u, cid):
                continue
            for f in (store.get("facts", cid) or {"facts": []})["facts"]:
                rows.append({"fact_id": f["fact_id"], "case_id": cid, "source_id": f["source_id"], "subject": f["subject"],
                             "metric": f["metric"], "raw_value": f["raw_value"], "normalized_value": f["normalized_value"],
                             "currency": f["currency"], "frequency": f["frequency"], "category": f["category"],
                             "basis": f["basis"], "confidence": f["confidence"], "raw_text": f.get("raw_text"),
                             "unit": f.get("unit"), "period_start": f.get("period_start"), "period_end": f.get("period_end"),
                             "normalization_rule": f.get("normalization_rule"),
                             "ambiguity_notes": "; ".join(f.get("ambiguity_notes") or []),
                             "evidence": json.dumps(f.get("evidence", []), ensure_ascii=False)})
    elif resource == "findings":
        for f in store.list("finding"):
            if _owns_case(u, f["case_id"]):
                rows.append({
                    "finding_id": f.get("finding_id"), "case_id": f.get("case_id"), "title": f.get("title"),
                    "statement": f.get("statement"), "severity": f.get("severity"), "confidence": f.get("confidence"),
                    "category": f.get("category", "discrepancy"), "status": f.get("status", "open"),
                    "difference_absolute": f.get("difference_absolute"),
                    "difference_percentage": f.get("difference_percentage"),
                    "possible_explanations": json.dumps(f.get("possible_explanations", []), ensure_ascii=False),
                    "recommended_review_action": f.get("recommended_review_action"),
                    "evidence_references": json.dumps(f.get("evidence_references", []), ensure_ascii=False),
                })
    return rows
