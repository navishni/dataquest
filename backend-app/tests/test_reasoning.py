import pytest

from backend import pipeline
from backend.agents import a01_file_validation as a01
from backend.agents import a14_case_linker as a14
from backend.agents import a15_fact_normalizer as a15
from backend.agents import a16_cross_doc_reasoning as a16
from backend.agents import a17_action_draft as a17
from backend.common import auth, llm_guard
from backend.common.errors import AgentError, ErrorCode
from backend.common.store import store
from tests.conftest import login
from tests.fixtures import make


def mk(text):
    sid = a01.run(a01.FileValidationInput(filename="a.pdf", data=make.text_pdf((text,)))).source_id
    pipeline.run_pipeline(sid)
    return sid


def mkcase(cid="c1", owner="alice"):
    store.put("case", cid, {"case_id": cid, "owner": owner, "name": "x"})
    return cid


def link(cid, ids):
    return a14.run(a14.LinkerInput(case_id=cid, source_ids=ids, mode="explicit"))


SAL = "Acme Traders Pvt Ltd\nPeriod: 2025-01-01\nMonthly salary: Rs. {}\nTax paid: 12,500"


# ---------- 14 ----------
def test_link_explicit_is_verified_and_idempotent(analyst):
    a, c = mk(SAL.format("1,00,000")), mkcase()
    o = link(c, [a])
    assert o.links[0].human_verified and o.links[0].relationship_confidence == 1.0 and o.links[0].matching_signals == ["user_assigned"]
    assert link(c, [a]).links[0].link_id == o.links[0].link_id
    assert len(a14.case_links(c)) == 1


def test_link_suggest_never_auto_confirmed_and_uses_signals(analyst):
    a, b, c = mk(SAL.format("1,00,000")), mk(SAL.format("80,000")), mkcase()
    link(c, [a])
    o = a14.run(a14.LinkerInput(case_id=c, source_ids=[b], mode="suggest"))
    assert o.links and not o.links[0].human_verified and 0 < o.links[0].relationship_confidence <= 1
    sig = [x.split("=")[0] for x in o.links[0].matching_signals]
    assert "shared_entity" in sig and "date_proximity" in sig


def test_link_suggest_below_threshold_not_created(analyst):
    a, b, c = mk(SAL.format("1")), mk("Completely unrelated gardening notes about tomatoes"), mkcase()
    link(c, [a])
    assert a14.run(a14.LinkerInput(case_id=c, source_ids=[b], mode="suggest")).links == []


def test_link_decision_confirm_reject(analyst):
    a, b, c = mk(SAL.format("1")), mk(SAL.format("2")), mkcase()
    link(c, [a])
    lid = a14.run(a14.LinkerInput(case_id=c, source_ids=[b], mode="suggest")).links[0].link_id
    assert a14.decide(a14.DecisionInput(link_id=lid, decision="confirm")).human_verified
    a14.decide(a14.DecisionInput(link_id=lid, decision="reject"))
    assert lid not in [l["link_id"] for l in a14.case_links(c)]
    assert lid in [l["link_id"] for l in a14.case_links(c, include_rejected=True)]


def test_link_errors(analyst):
    a = mk(SAL.format("1"))
    for kw, code in [(dict(case_id="nope", source_ids=[a], mode="explicit"), ErrorCode.NOT_FOUND)]:
        with pytest.raises(AgentError) as e:
            a14.run(a14.LinkerInput(**kw))
        assert e.value.code == code
    c = mkcase()
    with pytest.raises(AgentError) as e:
        link(c, ["ghost"])
    assert e.value.code == ErrorCode.NOT_FOUND
    with pytest.raises(AgentError) as e:
        a14.decide(a14.DecisionInput(link_id="ghost", decision="confirm"))
    assert e.value.code == ErrorCode.NOT_FOUND


def test_link_forbidden_for_other_users_case(analyst):
    a = mk(SAL.format("1"))
    mkcase("c9", owner="mallory")
    with pytest.raises(AgentError) as e:
        link("c9", [a])
    assert e.value.code == ErrorCode.FORBIDDEN


# ---------- 15 ----------
def test_to_decimal_rules():
    d = a15.to_decimal
    assert d("1,00,000", None)[0] == 100000 and d("100,000", None)[0] == 100000
    assert d("(500)", None)[0] == -500
    assert d("2", "lakh")[0] == 200000 and d("1.5", "crore")[0] == 15000000
    assert d("abc", None)[0] is None


def test_to_iso():
    assert a15.to_iso("2025-03-04")[0] == "2025-03-04" and a15.to_iso("04/03/2025")[0] == "2025-03-04"
    assert a15.to_iso("31/02/2025")[0] is None


def test_facts_extracted_with_evidence_and_inr(analyst):
    a, c = mk(SAL.format("1,00,000")), mkcase()
    link(c, [a])
    f = a15.run(a15.FactInput(case_id=c)).facts
    sal = next(x for x in f if x.metric == "monthly salary")
    assert sal.normalized_value == "100000" and sal.currency == "INR" and sal.frequency == "monthly"
    assert sal.evidence and sal.evidence[0].excerpt and all(0 <= x.confidence <= 1 for x in f)
    assert "unspecified" == sal.subject and sal.ambiguity_notes


def test_facts_bare_dollar_is_ambiguous(analyst):
    a, c = mk("Monthly salary: $ 5,000"), mkcase()
    link(c, [a])
    f = next(x for x in a15.run(a15.FactInput(case_id=c)).facts if x.metric == "monthly salary")
    assert f.currency is None and any("ambig" in n.lower() or "$" in n for n in f.ambiguity_notes)


def test_facts_missing_document_conflict_and_no_case(analyst):
    c = mkcase()
    sid = a01.run(a01.FileValidationInput(filename="a.pdf", data=make.text_pdf())).source_id
    link(c, [sid])
    with pytest.raises(AgentError) as e:
        a15.run(a15.FactInput(case_id=c))
    assert e.value.code == ErrorCode.CONFLICT
    with pytest.raises(AgentError) as e2:
        a15.run(a15.FactInput(case_id="nope"))
    assert e2.value.code == ErrorCode.NOT_FOUND


def test_facts_ungrounded_llm_subject_rejected(analyst):
    a, c = mk(SAL.format("1")), mkcase()
    link(c, [a])
    llm_guard.provider = lambda *x, **k: '{"subject": "Nonexistent Person"}'
    f = a15.run(a15.FactInput(case_id=c)).facts
    assert all(x.subject != "Nonexistent Person" for x in f)


# ---------- 16 ----------
def case_with(v1, v2, t1=SAL, t2=SAL):
    a, b, c = mk(t1.format(v1)), mk(t2.format(v2)), mkcase()
    link(c, [a, b])
    a15.run(a15.FactInput(case_id=c))
    return c


def test_reasoning_finds_discrepancy_with_code_arithmetic(analyst):
    c = case_with("1,00,000", "80,000")
    o = a16.run(a16.ReasoningInput(case_id=c))
    f = o.findings[0]
    assert "20000" in f.statement and "20.00%" in f.statement and f.human_review_required and f.severity == "high"
    assert len(f.evidence_references) == 2 and f.possible_explanations
    assert "fraud" not in f.statement.lower()


def test_reasoning_within_tolerance_no_finding(analyst):
    c = case_with("1,00,000", "1,00,000")
    o = a16.run(a16.ReasoningInput(case_id=c))
    assert o.findings == [] and o.comparisons


def test_reasoning_idempotent_finding_ids(analyst):
    c = case_with("1,00,000", "50,000")
    assert [f.finding_id for f in a16.run(a16.ReasoningInput(case_id=c)).findings] == \
           [f.finding_id for f in a16.run(a16.ReasoningInput(case_id=c)).findings]
    assert len(store.list("finding")) == 1


def test_reasoning_not_comparable_currency(analyst):
    c = case_with("1,00,000", "80,000", t2="Acme Traders Pvt Ltd\nMonthly salary: USD {}")
    o = a16.run(a16.ReasoningInput(case_id=c))
    assert o.findings == [] and any("currency" in n.reason.lower() for n in o.not_comparable)


def test_reasoning_zero_values_no_division_error(analyst):
    c = case_with("0", "0")
    assert a16.run(a16.ReasoningInput(case_id=c)).findings == []


def test_reasoning_dates_compared_as_days_not_percent(analyst):
    a, b, c = mk("Period: 2025-01-01"), mk("Period: 2026-09-09"), mkcase()
    link(c, [a, b])
    a15.run(a15.FactInput(case_id=c))
    o = a16.run(a16.ReasoningInput(case_id=c))
    assert o.comparisons[0].abs_diff.isdigit() and int(o.comparisons[0].abs_diff) > 600
    same = mkcase("c3")
    x, y = mk("Period: 2025-01-01"), mk("Period: 2025-01-01")
    link(same, [x, y])
    a15.run(a15.FactInput(case_id=same))
    assert a16.run(a16.ReasoningInput(case_id=same)).findings == []


def test_range_value_abstains(analyst):
    a, c = mk("Bonus: 10-12k"), mkcase()
    link(c, [a])
    f = [x for x in a15.run(a15.FactInput(case_id=c)).facts if x.metric == "bonus"]
    assert not f or (f[0].normalized_value is None and f[0].normalization_rule == "RANGE_ABSTAIN")


def test_reasoning_errors(analyst):
    with pytest.raises(AgentError) as e:
        a16.run(a16.ReasoningInput(case_id="nope"))
    assert e.value.code == ErrorCode.NOT_FOUND
    mkcase("c2", owner="mallory")
    with pytest.raises(AgentError) as e2:
        a16.run(a16.ReasoningInput(case_id="c2"))
    assert e2.value.code == ErrorCode.FORBIDDEN


# ---------- 17 ----------
def finding_case():
    c = case_with("1,00,000", "50,000")
    return c, a16.run(a16.ReasoningInput(case_id=c)).findings[0]


def test_draft_is_draft_labeled_idempotent(analyst):
    c, f = finding_case()
    d = a17.run(a17.DraftInput(case_id=c, finding_id=f.finding_id, action_type="request_clarification"))
    assert d.status == "draft" and d.requires_human_approval and "not_sent" in d.labels and d.generated_by == "ai"
    assert d.final_content_hash and d.supporting_evidence and "50000" in d.body
    assert a17.run(a17.DraftInput(case_id=c, finding_id=f.finding_id, action_type="request_clarification")).action_id == d.action_id


def test_draft_policy_checks_present(analyst):
    c, f = finding_case()
    d = a17.run(a17.DraftInput(case_id=c, finding_id=f.finding_id, action_type="flag_for_review"))
    names = {p.name: p.status for p in d.policy_checks}
    assert names["banned_terms"] == "pass" and names["evidence_present"] == "pass" and names["recipient_domain_allowlist"] == "skipped"


def test_draft_llm_polish_with_new_number_is_rejected(analyst):
    c, f = finding_case()
    llm_guard.provider = lambda *a, **k: '{"body": "Please explain the gap of 99999."}'
    d = a17.run(a17.DraftInput(case_id=c, finding_id=f.finding_id, action_type="request_clarification"))
    assert "99999" not in d.body


def test_draft_errors(analyst):
    c, f = finding_case()
    for kw, code in [(dict(case_id=c, action_type="send_money"), ErrorCode.INVALID_INPUT),
                     (dict(case_id="nope", action_type="flag_for_review"), ErrorCode.NOT_FOUND),
                     (dict(case_id=c, finding_id="ghost", action_type="flag_for_review"), ErrorCode.NOT_FOUND)]:
        with pytest.raises(AgentError) as e:
            a17.run(a17.DraftInput(**kw))
        assert e.value.code == code
    login("eve", "analyst")
    with pytest.raises(AgentError) as e:
        a17.run(a17.DraftInput(case_id=c, action_type="flag_for_review"))
    assert e.value.code == ErrorCode.FORBIDDEN
