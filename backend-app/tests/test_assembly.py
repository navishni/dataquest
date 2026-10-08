import pytest

from backend import pipeline
from backend.agents import a01_file_validation as a01
from backend.agents import a02_format_router as a02
from backend.agents import a11_json_assembly as a11
from backend.agents import a12_confidence_validation as a12
from backend.agents import a13_virtual_merge as a13
from backend.agents import a21_consensus as a21
from backend.common import auth
from backend.common.errors import AgentError, ErrorCode
from backend.common.store import store
from tests.fixtures import make


def up(data, name="a.pdf"):
    return a01.run(a01.FileValidationInput(filename=name, data=data)).source_id


def full(data, name="a.pdf"):
    sid = up(data, name)
    return sid, pipeline.run_pipeline(sid)


# ---------- pipeline / 11 / 12 ----------
def test_pipeline_text_pdf_end_to_end(analyst):
    sid, r = full(make.text_pdf(("Invoice number 4821 dated 2025-03-04",)))
    assert r["status"] in ("complete", "partial")
    doc = store.get("document", sid)
    assert doc and doc["pages"] and "4821" in store.get("markdown", sid)
    assert store.get("confidence", sid) is not None


def test_pipeline_table_pdf(analyst):
    sid, r = full(make.table_pdf([["Item", "Amt"], ["a", "100"], ["b", "200"], ["Total", "300"]]))
    doc = store.get("document", sid)
    types = [b["type"] for p in doc["pages"] for b in p["blocks"]]
    assert "table" in types
    assert "| Item | Amt |" in store.get("markdown", sid)
    conf = store.get("confidence", sid)
    assert any(b["status"] == "pass" for b in conf["badges"])


def test_pipeline_scanned_pdf_uses_ocr(analyst):
    sid, r = full(make.scanned_pdf("RECEIPT TOTAL 990"))
    md = store.get("markdown", sid) or ""
    assert "RECEIPT" in md.upper()


def test_assembly_conflict_lists_missing(analyst):
    sid = up(make.text_pdf())
    with pytest.raises(AgentError) as e:
        a11.run(a11.AssemblyInput(source_id=sid))
    assert e.value.code == ErrorCode.CONFLICT and "router" in e.value.details["missing"]
    a02.run(a02.RouterInput(source_id=sid))
    with pytest.raises(AgentError) as e2:
        a11.run(a11.AssemblyInput(source_id=sid))
    assert e2.value.code == ErrorCode.CONFLICT and e2.value.details["missing"]


def test_assembly_content_hash_stable_and_unique_ids(analyst):
    sid, _ = full(make.text_pdf(("hello stable world",)))
    d1 = a11.run(a11.AssemblyInput(source_id=sid))
    d2 = a11.run(a11.AssemblyInput(source_id=sid))
    assert d1.content_hash == d2.content_hash
    ids = [b.block_id for p in d1.pages for b in p.blocks]
    assert len(ids) == len(set(ids))


def test_confidence_conflict_without_document(analyst):
    sid = up(make.text_pdf())
    with pytest.raises(AgentError) as e:
        a12.run(a12.ConfidenceInput(source_id=sid))
    assert e.value.code == ErrorCode.CONFLICT


def test_confidence_range_and_arithmetic_mismatch(analyst):
    sid, _ = full(make.table_pdf([["Item", "Amt"], ["a", "100"], ["b", "200"], ["Total", "999"]]))
    c = a12.run(a12.ConfidenceInput(source_id=sid))
    assert 0 <= c.document_confidence <= 1 and all(0 <= b.confidence <= 1 for b in c.blocks)
    assert any(b.status == "mismatch" for b in c.badges)
    assert any(b.needs_review for b in c.blocks)


def test_validators_unit():
    assert a12._iban_ok("GB82WEST12345698765432") and not a12._iban_ok("GB82WEST12345698765433")
    assert a12._BAD_NUM.search("1,23,4567")


# ---------- 21 consensus ----------
def test_consensus_native_only_single_candidate(analyst):
    sid, _ = full(make.text_pdf(("consensus single source text",)))
    o = a21.run(a21.ConsensusInput(source_id=sid))
    assert o.blocks and all(b.agreement in ("unanimous", "majority", "split") for b in o.blocks)
    assert o.coverage and 0 <= o.coverage[0].coverage_score <= 1


def test_consensus_requires_router(analyst):
    sid = up(make.text_pdf())
    with pytest.raises(AgentError) as e:
        a21.run(a21.ConsensusInput(source_id=sid))
    assert e.value.code == ErrorCode.CONFLICT


def test_consensus_deterministic(analyst):
    sid, _ = full(make.scanned_pdf("DETERMINISTIC TEXT"))
    assert a21.run(a21.ConsensusInput(source_id=sid)) == a21.run(a21.ConsensusInput(source_id=sid))


def test_consensus_norm():
    assert a21.norm("ｆｕｌｌ   width") == "full width"


# ---------- 13 virtual merge ----------
def mkbatch(ids):
    store.put("batch", "b1", {"batch_id": "b1", "owner": auth.current_user().user_id, "source_ids": ids})


def test_merge_numbers_pages_and_boundaries_idempotent(analyst):
    a, b = up(make.text_pdf(("one",))), up(make.text_pdf(("two two",)))
    mkbatch([a, b])
    o = a13.run(a13.MergeInput(batch_id="b1", ordered_source_ids=[b, a]))
    assert [p.virtual_page_number for p in o.pages] == list(range(1, len(o.pages) + 1))
    assert o.pages[0].source_id == b and o.pages[0].boundary_start
    assert o.virtual_document_id == a13.run(a13.MergeInput(batch_id="b1", ordered_source_ids=[b, a])).virtual_document_id
    assert o.virtual_document_id != a13.run(a13.MergeInput(batch_id="b1", ordered_source_ids=[a, b])).virtual_document_id


def test_merge_errors(analyst):
    a = up(make.text_pdf(("one",)))
    mkbatch([a])
    cases = [
        (dict(batch_id="nope", ordered_source_ids=[a]), ErrorCode.NOT_FOUND),
        (dict(batch_id="b1", ordered_source_ids=[]), ErrorCode.INVALID_INPUT),
        (dict(batch_id="b1", ordered_source_ids=[a, a]), ErrorCode.INVALID_INPUT),
        (dict(batch_id="b1", ordered_source_ids=["ghost"]), ErrorCode.NOT_FOUND),
    ]
    for kw, code in cases:
        with pytest.raises(AgentError) as e:
            a13.run(a13.MergeInput(**kw))
        assert e.value.code == code, kw


def test_merge_forbidden_for_other_user(analyst):
    a = up(make.text_pdf(("one",)))
    store.put("batch", "b2", {"batch_id": "b2", "owner": "someone-else", "source_ids": [a]})
    with pytest.raises(AgentError) as e:
        a13.run(a13.MergeInput(batch_id="b2", ordered_source_ids=[a]))
    assert e.value.code == ErrorCode.FORBIDDEN
