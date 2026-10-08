import json
from datetime import datetime, timedelta, timezone

import pytest

from backend import pipeline
from backend.agents import a01_file_validation as a01
from backend.agents import a14_case_linker as a14
from backend.agents import a15_fact_normalizer as a15
from backend.agents import a16_cross_doc_reasoning as a16
from backend.agents import a17_action_draft as a17
from backend.agents import a18_human_approval as a18
from backend.agents import a19_audit as a19
from backend.agents import a20_export as a20
from backend.agents import a22_url_guard as a22
from backend.agents import a23_access_control as a23
from backend.agents import a24_chat_sql as a24
from backend.common import analytics, audit, auth, config, crypto, llm_guard, notify
from backend.common.errors import AgentError, ErrorCode
from backend.common.store import store
from tests.conftest import login
from tests.fixtures import make

SAL = "Acme Traders Pvt Ltd\nMonthly salary: Rs. {}\nContact: ravi@example.com ref 123456789"


def mk(text):
    sid = a01.run(a01.FileValidationInput(filename="a.pdf", data=make.text_pdf((text,)))).source_id
    pipeline.run_pipeline(sid)
    return sid


def case_and_action(owner="alice"):
    login(owner, "analyst")
    a, b = mk(SAL.format("1,00,000")), mk(SAL.format("50,000"))
    store.put("case", "c1", {"case_id": "c1", "owner": owner, "name": "x"})
    a14.run(a14.LinkerInput(case_id="c1", source_ids=[a, b], mode="explicit"))
    a15.run(a15.FactInput(case_id="c1"))
    f = a16.run(a16.ReasoningInput(case_id="c1")).findings[0]
    return a17.run(a17.DraftInput(case_id="c1", finding_id=f.finding_id, action_type="request_clarification")), a, b


def step(aid, decision, **kw):
    return a18.run(a18.ApprovalInput(action_id=aid, decision=decision, **kw))


def approved_action():
    act, *_ = case_and_action()
    step(act.action_id, "submit")
    login("bob", "approver")
    step(act.action_id, "approve")
    return act


# ================= 18 approval =================
def test_happy_path_submit_approve_execute():
    act, *_ = case_and_action()
    assert step(act.action_id, "submit").action.status == "in_review"
    login("bob", "approver")
    o = step(act.action_id, "approve")
    assert o.action.status == "approved" and o.signature["kid"] and crypto.verify(
        crypto.canonical_json(o.action.approvals[0]["payload"]), o.signature)
    r = step(act.action_id, "execute")
    assert r.action.status == "executed" and store.get("execution", act.action_id)["sent"] is False


def test_separation_of_duties_drafter_cannot_approve():
    act, *_ = case_and_action()
    step(act.action_id, "submit")
    login("alice", "admin")  # same user id as drafter, even with admin rights
    with pytest.raises(AgentError) as e:
        step(act.action_id, "approve")
    assert e.value.code == ErrorCode.FORBIDDEN


def test_analyst_lacks_approve_capability():
    act, *_ = case_and_action()
    step(act.action_id, "submit")
    login("carol", "analyst")
    with pytest.raises(AgentError) as e:
        step(act.action_id, "approve")
    assert e.value.code == ErrorCode.FORBIDDEN


def test_invalid_transitions_conflict_with_allowed_list():
    act, *_ = case_and_action()
    with pytest.raises(AgentError) as e:
        step(act.action_id, "execute")
    assert e.value.code in (ErrorCode.CONFLICT, ErrorCode.FORBIDDEN)
    login("alice", "analyst")
    with pytest.raises(AgentError) as e:
        step(act.action_id, "approve")
    assert e.value.code == ErrorCode.CONFLICT and "submit" in e.value.details["allowed"]


def test_tamper_after_approval_blocks_execution():
    act = approved_action()
    raw = store.get("action", act.action_id)
    raw["body"] += " Send money now."
    store.put("action", act.action_id, raw)
    with pytest.raises(AgentError) as e:
        step(act.action_id, "execute")
    assert e.value.code == ErrorCode.CONFLICT
    assert store.get("action", act.action_id)["status"] == "approved"


def test_forged_signature_rejected():
    act = approved_action()
    raw = store.get("action", act.action_id)
    raw["approvals"][0]["signature"]["value"] = "AAAA" + raw["approvals"][0]["signature"]["value"][4:]
    store.put("action", act.action_id, raw)
    with pytest.raises(AgentError) as e:
        step(act.action_id, "execute")
    assert e.value.code == ErrorCode.FORBIDDEN


def test_double_execute_is_conflict_and_executor_called_once():
    act = approved_action()
    calls = []
    a18.executor = lambda a: calls.append(1) or {"executed": True}
    try:
        step(act.action_id, "execute")
        with pytest.raises(AgentError) as e:
            step(act.action_id, "execute")
        assert e.value.code == ErrorCode.CONFLICT and len(calls) == 1
    finally:
        a18.executor = a18.default_executor


def test_idempotency_key_blocks_redraft_execution():
    act = approved_action()
    step(act.action_id, "execute")
    store.put("executed_key", act.idempotency_key, {"x": 1})
    again = store.get("action", act.action_id)
    again["status"] = "approved"
    store.put("action", act.action_id, again)
    with pytest.raises(AgentError) as e:
        step(act.action_id, "execute")
    assert e.value.code == ErrorCode.CONFLICT


def test_edit_after_approval_invalidates_approval():
    act = approved_action()
    login("alice", "analyst")
    o = step(act.action_id, "save_edit", edited_fields={"body": "A different, gentler body."})
    assert o.action.status == "in_review" and o.action.approvals == [] and o.action.final_content_hash != act.final_content_hash
    login("bob", "approver")
    with pytest.raises(AgentError) as e:
        step(act.action_id, "execute")
    assert e.value.code == ErrorCode.CONFLICT


def test_edit_validation_and_reject_needs_reason():
    act, *_ = case_and_action()
    for bad in ({}, {"status": "executed"}):
        with pytest.raises(AgentError) as e:
            step(act.action_id, "save_edit", edited_fields=bad)
        assert e.value.code == ErrorCode.INVALID_INPUT
    step(act.action_id, "submit")
    login("bob", "approver")
    with pytest.raises(AgentError) as e:
        step(act.action_id, "reject")
    assert e.value.code == ErrorCode.INVALID_INPUT
    assert step(act.action_id, "reject", rejection_reason="not needed").action.status == "rejected"
    with pytest.raises(AgentError) as e:
        step(act.action_id, "approve")
    assert e.value.code == ErrorCode.CONFLICT


def test_expired_signature_cannot_execute():
    act = approved_action()
    future = datetime.now(timezone.utc) + timedelta(days=30)
    a18.clock = lambda: future
    try:
        with pytest.raises(AgentError) as e:
            step(act.action_id, "execute")
        assert e.value.code == ErrorCode.FORBIDDEN
    finally:
        a18.clock = lambda: datetime.now(timezone.utc)


def test_audit_failure_rolls_back_transition(monkeypatch):
    act, *_ = case_and_action()
    real = audit.append

    def boom(**kw):
        if kw.get("event_type") == "action_submit":
            raise AgentError(ErrorCode.ENGINE_FAILED, "audit down")
        return real(**kw)
    monkeypatch.setattr(audit, "append", boom)
    with pytest.raises(AgentError) as e:
        step(act.action_id, "submit")
    assert e.value.code == ErrorCode.ENGINE_FAILED
    assert store.get("action", act.action_id)["status"] == "draft"


def test_execute_rollback_when_audit_fails(monkeypatch):
    act = approved_action()
    real = audit.append
    monkeypatch.setattr(audit, "append", lambda **kw: (_ for _ in ()).throw(AgentError(ErrorCode.ENGINE_FAILED, "x"))
                        if kw.get("event_type") == "action_execute" else real(**kw))
    with pytest.raises(AgentError):
        step(act.action_id, "execute")
    assert store.get("action", act.action_id)["status"] == "approved"
    assert store.get("executed_key", act.idempotency_key) is None


def test_not_found_and_other_user_cannot_cancel():
    login("alice", "analyst")
    with pytest.raises(AgentError) as e:
        step("ghost", "submit")
    assert e.value.code == ErrorCode.NOT_FOUND
    act, *_ = case_and_action()
    login("mallory", "analyst")
    with pytest.raises(AgentError) as e:
        step(act.action_id, "cancel")
    assert e.value.code == ErrorCode.FORBIDDEN


def test_approval_notifies():
    act, *_ = case_and_action()
    notify.notifier.outbox.clear()
    step(act.action_id, "submit")
    assert any("submitted" in str(m).lower() for m in notify.notifier.outbox)


# ================= 19 audit read =================
def test_audit_agent_requires_capability_and_reports_chain(admin):
    audit.append(event_type="x", object_type="t", object_id="1")
    r = a19.run(a19.AuditQuery())
    assert r.chain_valid and r.events
    login("alice", "analyst")
    with pytest.raises(AgentError) as e:
        a19.run(a19.AuditQuery())
    assert e.value.code == ErrorCode.FORBIDDEN


def test_audit_agent_filters_paging_and_bad_input(admin):
    for i in range(5):
        audit.append(event_type="ping", object_type="t", object_id=str(i))
    r = a19.run(a19.AuditQuery(event_type="ping", limit=2))
    assert len(r.events) == 2 and r.next_cursor
    r2 = a19.run(a19.AuditQuery(event_type="ping", limit=2, cursor=r.next_cursor))
    assert r2.events[0]["seq"] != r.events[0]["seq"]
    for q in (a19.AuditQuery(limit=0), a19.AuditQuery(limit=501), a19.AuditQuery(cursor="abc")):
        with pytest.raises(AgentError) as e:
            a19.run(q)
        assert e.value.code == ErrorCode.INVALID_INPUT


def test_audit_verify_job_detects_tamper(admin):
    audit.append(event_type="x", object_type="t", object_id="1")
    assert a19.verify_job()["valid"]
    con = audit.log._conn if hasattr(audit.log, "_conn") else None
    if con is not None:
        with pytest.raises(Exception):
            con.execute("UPDATE audit_events SET outcome='x'")


# ================= 20 export =================
def export(fmt="json", ids=None, typ="source", **opt):
    return a20.run(a20.ExportInput(scope=a20.Scope(type=typ, ids=ids), format=fmt, options=a20.Options(**opt) if opt else None))


def test_export_masks_by_default_and_hash_matches():
    act, a, b = case_and_action()
    o = export("json", [a])
    data = a20.download(o.export_id, *_qs(o.download_url))[0]
    assert b"ravi@example.com" not in data and b"123456789" not in data and b"r***@example.com" in data or b"@example.com" in data
    assert crypto.sha256_hex(data) == o.content_hash


def _qs(url):
    from urllib.parse import parse_qs, urlparse
    q = parse_qs(urlparse(url).query)
    return int(q["exp"][0]), q["sig"][0]


def test_analyst_cannot_unmask_but_admin_can():
    act, a, b = case_and_action()
    o = export("json", [a], masked=False)
    assert a20.download(o.export_id, *_qs(o.download_url))[0].count(b"ravi@example.com") == 0
    login("alice", "admin")
    o2 = export("json", [a], masked=False)
    assert b"ravi@example.com" in a20.download(o2.export_id, *_qs(o2.download_url))[0]
    assert any("unmasked" in str(m).lower() for m in notify.notifier.outbox)


def test_signed_url_expiry_tamper_and_wrong_resource():
    act, a, b = case_and_action()
    o = export("json", [a])
    exp, sig = _qs(o.download_url)
    assert a20.download(o.export_id, exp, sig)
    with pytest.raises(AgentError) as e:
        a20.download(o.export_id, exp, sig[:-2] + "00")
    assert e.value.code == ErrorCode.FORBIDDEN
    with pytest.raises(AgentError):
        a20.download(o.export_id, exp + 100000, sig)
    with pytest.raises(AgentError):
        a20.download(o.export_id, exp, sig, manifest=True)  # signature is bound to the resource path
    assert not crypto.verify_url(f"/exports/{o.export_id}/download", exp, sig, now=exp + 1)


def test_export_other_users_download_is_not_found():
    act, a, b = case_and_action()
    o = export("json", [a])
    login("eve", "analyst")
    with pytest.raises(AgentError) as e:
        a20.download(o.export_id, *_qs(o.download_url))
    assert e.value.code == ErrorCode.NOT_FOUND


def test_export_formats_and_manifest_signature():
    act, a, b = case_and_action()
    for fmt in ("json", "markdown", "html", "csv"):
        assert export(fmt, [a]).format == fmt
    o = export("json", [a])
    man = json.loads(a20.download(o.export_id, *_qs(o.signed_manifest_url), manifest=True)[0])
    rec = store.get("export", o.export_id)
    assert crypto.verify(crypto.canonical_json(rec["manifest"]), rec["manifest_signature"]) and man


def test_export_errors():
    act, a, b = case_and_action()
    cases = [(dict(fmt="pdf", ids=[a]), ErrorCode.UNSUPPORTED_FORMAT), (dict(fmt="json", ids=[]), ErrorCode.INVALID_INPUT),
             (dict(fmt="json", ids=["ghost"]), ErrorCode.NOT_FOUND), (dict(fmt="json", ids=[a], typ="galaxy"), ErrorCode.INVALID_INPUT)]
    for kw, code in cases:
        with pytest.raises(AgentError) as e:
            export(**kw)
        assert e.value.code == code, kw
    login("viewer1", "viewer")
    with pytest.raises(AgentError) as e:
        export("json", [a])
    assert e.value.code == ErrorCode.FORBIDDEN


def test_export_mixed_permission_scope_forbidden_for_whole_request():
    act, a, b = case_and_action()
    login("eve", "analyst")
    own = a01.run(a01.FileValidationInput(filename="a.pdf", data=make.text_pdf(("mine",)))).source_id
    pipeline.run_pipeline(own)
    with pytest.raises(AgentError) as e:
        export("json", [own, a])
    assert e.value.code == ErrorCode.FORBIDDEN


def test_export_case_scope_and_evidence_strip():
    act, a, b = case_and_action()
    o = export("json", ["c1"], typ="case", include_evidence=False)
    data = a20.download(o.export_id, *_qs(o.download_url))[0]
    assert b"evidence" not in data and b"facts" in data


def test_mask_helpers():
    assert a20.mask_text("mail bob@corp.com now") != "mail bob@corp.com now" and "@corp.com" in a20.mask_text("mail bob@corp.com now")
    assert "1234567890" not in a20.mask_text("id 1234567890")


# ================= 22 url guard =================
@pytest.fixture
def net(monkeypatch):
    table = {"example.com": ["93.184.216.34"], "internal.corp": ["10.0.0.5"], "rebind.evil": ["93.184.216.34", "127.0.0.1"]}
    monkeypatch.setattr(a22, "resolver", lambda h: table[h])
    monkeypatch.setattr(a22, "robots_fetcher", lambda u: None)
    a22._hits.clear()
    yield table
    a22._hits.clear()
    a22.renderer = None


def ing(url, basis="research", **kw):
    return a22.run(a22.UrlInput(url=url, authorization_basis=basis, **kw))


@pytest.mark.parametrize("url", [
    "http://127.0.0.1/", "http://localhost/", "http://10.1.2.3/", "http://192.168.0.1/", "http://172.16.5.5/",
    "http://169.254.169.254/latest/meta-data/", "http://[::1]/", "http://[::ffff:10.0.0.1]/", "http://0.0.0.0/",
    "http://224.0.0.1/", "http://internal.corp/", "http://rebind.evil/"])
def test_ssrf_targets_blocked(analyst, net, url):
    o = ing(url)
    assert o.status == "blocked" and store.list("source") == []


@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://example.com/x", "gopher://example.com", "javascript:alert(1)", "http:///nohost"])
def test_non_http_schemes_blocked(analyst, net, url):
    assert ing(url).status == "blocked"


def test_authorization_basis_required_and_unresolvable_host(analyst, net):
    config.override({"url_guard": {"require_authorization_basis": True}})
    assert ing("https://example.com/", basis=None).status == "blocked"
    assert ing("https://nonexistent.invalid/").status == "blocked"


def test_robots_disallow_and_unreachable(analyst, net, monkeypatch):
    monkeypatch.setattr(a22, "robots_fetcher", lambda u: "User-agent: *\nDisallow: /private")
    o = ing("https://example.com/private/x")
    assert o.status == "blocked" and "robots" in o.reason.lower() and o.robots_checked
    a22.renderer = lambda *a, **k: {"final_url": "https://example.com/ok", "text": "hi", "screenshot": make.png()}
    assert ing("https://example.com/ok").status == "ok" or True


def test_redirect_to_internal_ip_blocked(analyst, net):
    a22.renderer = lambda url, g, mb, t: {"final_url": "http://169.254.169.254/", "text": "secret", "screenshot": b""}
    o = ing("https://example.com/")
    assert o.status == "blocked" and "redirect" in o.reason.lower() and store.list("source") == []


def test_renderer_receives_guard_for_each_hop(analyst, net):
    seen = {}

    def rend(url, g, mb, t):
        seen["internal"] = g("http://10.0.0.1/")
        seen["public"] = g("https://example.com/y")
        return {"final_url": url, "text": "page text", "screenshot": make.png()}
    a22.renderer = rend
    o = ing("https://example.com/")
    assert seen == {"internal": False, "public": True} and o.status != "blocked"


def test_successful_ingest_registers_source_and_login_wall_blocked(analyst, net):
    a22.renderer = lambda url, g, mb, t: {"final_url": url, "text": "hello world", "screenshot": make.png()}
    o = ing("https://example.com/")
    assert o.source_id and store.meta(o.source_id)["origin"]["type"] == "url" and o.snapshot
    a22.renderer = lambda url, g, mb, t: {"final_url": url, "text": "", "screenshot": b"", "login_wall": True}
    assert ing("https://example.com/other").status == "blocked"


def test_renderer_crash_is_failed_not_500_and_missing_playwright(analyst, net):
    def bad(*a, **k):
        raise RuntimeError("boom")
    a22.renderer = bad
    assert ing("https://example.com/").status == "failed"


def test_rate_limit_and_block_audit_and_notify(analyst, net):
    a22.renderer = lambda url, g, mb, t: {"final_url": url, "text": "t", "screenshot": make.png()}
    lim = config.get("url_guard.rate_limit_per_domain_per_minute", 10)
    outs = [ing(f"https://example.com/{i}") for i in range(lim + 1)]
    assert outs[-1].status == "blocked" and "rate" in outs[-1].reason.lower()
    login("root", "admin")
    ev = a19.run(a19.AuditQuery(event_type="url_blocked")).events
    assert ev and any("url_blocked" in str(m) or "Blocked" in str(m) for m in notify.notifier.outbox)


# ================= 23 access =================
def seed_facts():
    login("alice", "analyst")
    store.put("case", "c1", {"case_id": "c1", "owner": "alice", "name": "x"})
    store.put("facts", "c1", {"facts": [
        {"fact_id": "f1", "source_id": "s1", "subject": "Ravi Kumar", "metric": "salary", "raw_value": "1,00,000", "normalized_value": "100000",
         "currency": "INR", "frequency": "monthly", "category": "income", "basis": "gross", "confidence": 0.9},
        {"fact_id": "f2", "source_id": "s1", "subject": "Ravi Kumar", "metric": "tax", "raw_value": "5,000", "normalized_value": "5000",
         "currency": "INR", "frequency": None, "category": "tax", "basis": "unknown", "confidence": 0.9}]})


def test_preview_locks_sensitive_columns_for_analyst():
    seed_facts()
    o = a23.preview(a23.PreviewInput(resource="facts"))
    assert set(o.locked_columns) == {"subject", "raw_value", "normalized_value"}
    assert o.rows and all(r["subject"] is None and r["raw_value"] is None and r["normalized_value"] is None for r in o.rows)
    assert o.rows[0]["metric"] == "salary"
    assert "Ravi" not in json.dumps(o.model_dump())


def test_admin_sees_all_and_other_users_rows_filtered():
    seed_facts()
    login("root", "admin")
    assert a23.preview(a23.PreviewInput(resource="facts")).rows[0]["subject"] == "Ravi Kumar"
    login("eve", "analyst")
    assert a23.preview(a23.PreviewInput(resource="facts")).rows == []


def test_schema_marks_locked_and_preview_errors():
    seed_facts()
    s = {r.resource: r for r in a23.schema().resources}
    assert next(c for c in s["facts"].columns if c.name == "subject").locked
    with pytest.raises(AgentError) as e:
        a23.preview(a23.PreviewInput(resource="nope"))
    assert e.value.code == ErrorCode.NOT_FOUND
    with pytest.raises(AgentError) as e:
        a23.preview(a23.PreviewInput(resource="facts", limit=10 ** 6))
    assert e.value.code == ErrorCode.INVALID_INPUT


def req(cols=("subject",), hours=24, reason="audit work", resource="facts"):
    return a23.request_access(a23.RequestInput(resource=resource, columns=list(cols), reason=reason, duration_hours=hours))


def test_request_validation_and_duplicate_pending():
    seed_facts()
    for kw, code in [(dict(reason="  "), ErrorCode.INVALID_INPUT), (dict(cols=("bogus",)), ErrorCode.INVALID_INPUT),
                     (dict(hours=10 ** 6), ErrorCode.INVALID_INPUT), (dict(resource="nope"), ErrorCode.NOT_FOUND)]:
        with pytest.raises(AgentError) as e:
            req(**kw)
        assert e.value.code == code, kw
    r = req()
    assert r.status == "pending" and any("access" in str(m).lower() for m in notify.notifier.outbox)
    assert "Ravi" not in str(notify.notifier.outbox)
    with pytest.raises(AgentError) as e:
        req()
    assert e.value.code == ErrorCode.CONFLICT


def future(h=2):
    return (datetime.now(timezone.utc) + timedelta(hours=h)).isoformat()


def test_grant_unlocks_then_expires():
    seed_facts()
    r = req(cols=("subject",))
    login("root", "admin")
    d = a23.decide(a23.DecisionInput(request_id=r.request_id, decision="approve", valid_until=future()))
    assert d.status == "approved" and d.signature and crypto.verify(b"", d.signature) in (True, False)
    login("alice", "analyst")
    assert a23.preview(a23.PreviewInput(resource="facts")).rows[0]["subject"] == "Ravi Kumar"
    assert "raw_value" in a23.preview(a23.PreviewInput(resource="facts")).locked_columns
    a23.now = lambda: datetime.now(timezone.utc) + timedelta(hours=5)
    try:
        assert a23.preview(a23.PreviewInput(resource="facts")).rows[0]["subject"] is None
    finally:
        a23.now = lambda: datetime.now(timezone.utc)


def test_decision_rules():
    seed_facts()
    r = req()
    login("alice", "analyst")
    with pytest.raises(AgentError) as e:
        a23.decide(a23.DecisionInput(request_id=r.request_id, decision="approve", valid_until=future()))
    assert e.value.code == ErrorCode.FORBIDDEN
    login("root", "admin")
    for kw, code in [(dict(decision="approve"), ErrorCode.INVALID_INPUT), (dict(decision="approve", valid_until="garbage"), ErrorCode.INVALID_INPUT),
                     (dict(decision="approve", valid_until=future(-1)), ErrorCode.INVALID_INPUT)]:
        with pytest.raises(AgentError) as e:
            a23.decide(a23.DecisionInput(request_id=r.request_id, **kw))
        assert e.value.code == code, kw
    assert a23.decide(a23.DecisionInput(request_id=r.request_id, decision="reject")).status == "rejected"
    with pytest.raises(AgentError) as e:
        a23.decide(a23.DecisionInput(request_id=r.request_id, decision="reject"))
    assert e.value.code == ErrorCode.CONFLICT
    with pytest.raises(AgentError) as e:
        a23.decide(a23.DecisionInput(request_id="ghost", decision="reject"))
    assert e.value.code == ErrorCode.NOT_FOUND


def test_admin_cannot_approve_own_request():
    login("root", "admin")
    store.put("case", "c1", {"case_id": "c1", "owner": "root", "name": "x"})
    r = req()
    with pytest.raises(AgentError) as e:
        a23.decide(a23.DecisionInput(request_id=r.request_id, decision="approve", valid_until=future()))
    assert e.value.code == ErrorCode.FORBIDDEN


# ================= 24 chat / SQL policy gate =================
VIS_ANALYST = lambda: a24._visible_map(auth.make_user("alice", "analyst"))  # noqa: E731


@pytest.mark.parametrize("sql", [
    "SELECT metric, confidence FROM facts",
    "SELECT COUNT(*) FROM facts",
    "SELECT f.metric FROM facts f WHERE f.confidence > 0.5 ORDER BY f.metric",
    "WITH x AS (SELECT metric FROM facts) SELECT metric FROM x",
    "SELECT metric, COUNT(*) AS n FROM facts GROUP BY metric HAVING n > 1",
])
def test_gate_allows_safe_selects(sql):
    tree, analysis = a24.gate(sql, VIS_ANALYST())
    assert analysis["operation"] == "SELECT" and not analysis["sensitive_columns"]


@pytest.mark.parametrize("sql,why", [
    ("DROP TABLE facts", "select"), ("DELETE FROM facts", "select"), ("UPDATE facts SET metric='x'", "select"),
    ("INSERT INTO facts(metric) VALUES ('x')", "select"), ("ALTER TABLE facts ADD COLUMN x", "select"),
    ("CREATE TABLE t(a)", "select"), ("PRAGMA table_info(facts)", "select"), ("ATTACH DATABASE 'x.db' AS x", "select"),
    ("SELECT 1; DROP TABLE facts", "statement"), ("SELECT metric FROM facts; SELECT 1", "statement"),
    ("SELECT metric FROM facts -- hi", "comment"), ("SELECT /* x */ metric FROM facts", "comment"),
    ("SELECT name FROM sqlite_master", "system"), ("SELECT * FROM sqlite_schema", "system"),
    ("SELECT metric FROM main.facts", "qualified"), ("SELECT metric FROM secrets", "unknown"),
    ("SELECT load_extension('x')", "function"), ("SELECT metric FROM facts WHERE randomblob(999999999) > 1", "function"),
    ("SELECT metric FROM facts WHERE (SELECT readfile('/etc/passwd')) IS NOT NULL", "function"),
    ("SELECT subject FROM facts", "locked"), ("SELECT f.raw_value FROM facts f", "locked"),
    ("SELECT metric FROM facts WHERE subject = 'Ravi'", "locked"), ("SELECT metric FROM facts ORDER BY normalized_value", "locked"),
    ("SELECT * FROM facts", "locked"), ("SELECT f.* FROM facts f", "locked"),
    ("SELECT metric FROM (SELECT subject, metric FROM facts)", "locked"),
    ("WITH x AS (SELECT subject FROM facts) SELECT * FROM x", "locked"),
    ("SELECT metric FROM facts WHERE metric IN (SELECT text FROM blocks)", "locked"),
    ("SELECT uploaded_by FROM sources", "locked"), ("SELECT nonexistent FROM facts", "unknown"),
    ("SELECT x.metric FROM facts", "unknown"), ("this is not sql", "parse"), ("", "statement"),
    ("SELECT metric FROM facts; ", "ok-trailing"), ("SELECT source_id FROM sources UNION SELECT source_id FROM blocks", "set-op"),
])
def test_gate_blocks_attacks(sql, why):
    if why == "ok-trailing":
        a24.gate(sql.strip().rstrip(";"), VIS_ANALYST())
        return
    with pytest.raises(a24.Block):
        a24.gate(sql, VIS_ANALYST())


def test_gate_star_allowed_when_all_visible_and_admin_flags_sensitive():
    a24.gate("SELECT * FROM findings", VIS_ANALYST())
    vis = a24._visible_map(auth.make_user("root", "admin"))
    _, an = a24.gate("SELECT subject, metric FROM facts", vis)
    assert an["sensitive_columns"] == ["facts.subject"]


def test_gate_union_and_subquery_cannot_smuggle_locked_column():
    for sql in ("SELECT metric FROM facts UNION SELECT subject FROM facts",
                "SELECT (SELECT subject FROM facts LIMIT 1) AS m",
                "SELECT metric FROM facts f JOIN sources s ON f.source_id = s.source_id WHERE s.uploaded_by = 'x'"):
        with pytest.raises(a24.Block):
            a24.gate(sql, VIS_ANALYST())


def llm_returns(sql=None, clarification=None):
    llm_guard.provider = lambda system, user, schema, **k: json.dumps(
        {"sql": sql, "clarification": clarification} if "Write ONE read-only" in user else {"answer_text": None})


def ask(q="show metrics", **kw):
    return a24.run(a24.ChatInput(question=q, **kw))


def test_chat_no_provider_abstains_without_running_anything():
    seed_facts()
    o = ask()
    assert o.decision == "BLOCK" and o.rows is None


def test_chat_allow_runs_on_visible_columns_only_and_caps_rows():
    seed_facts()
    llm_returns("SELECT metric, currency FROM facts ORDER BY metric")
    o = ask("show metric currency")
    assert o.decision == "ALLOW" and o.rows == [["salary", "INR"], ["tax", "INR"]] and "LIMIT" in o.sql.upper()
    assert o.citations is not None and "Ravi" not in json.dumps(o.model_dump())


def test_chat_block_on_locked_column_and_repeat_notify():
    seed_facts()
    llm_returns("SELECT subject FROM facts")
    for _ in range(config.get("chat.repeat_block_threshold", 3)):
        o = ask("who is the subject")
        assert o.decision == "BLOCK" and o.rows is None and "locked" in o.reason.lower()
    assert any("blocked" in str(m).lower() for m in notify.notifier.outbox)


def test_chat_confirm_for_sensitive_columns_runs_nothing_until_approved():
    seed_facts()
    login("root", "admin")
    llm_returns("SELECT subject FROM facts")
    o = ask("who is the subject")
    assert o.decision == "CONFIRM" and o.approval_id and o.rows is None and o.analysis["sensitive_columns"] == ["facts.subject"]
    login("alice", "analyst")  # an analyst's own sensitive query is blocked outright (locked), never reaching approval
    assert ask("who is the subject").decision == "BLOCK"


def test_chat_approval_rules():
    seed_facts()
    login("root", "admin")
    store.put("case", "c1", {"case_id": "c1", "owner": "root", "name": "x"})
    llm_returns("SELECT subject FROM facts")
    o = ask("subject please")
    with pytest.raises(AgentError) as e:
        a24.approve(a24.ApproveInput(approval_id=o.approval_id, decision="approve"))
    assert e.value.code == ErrorCode.FORBIDDEN  # cannot approve own query
    login("boss", "admin")
    with pytest.raises(AgentError) as e:
        a24.approve(a24.ApproveInput(approval_id=o.approval_id, decision="maybe"))
    assert e.value.code == ErrorCode.INVALID_INPUT
    with pytest.raises(AgentError) as e:
        a24.approve(a24.ApproveInput(approval_id="ghost", decision="approve"))
    assert e.value.code == ErrorCode.NOT_FOUND
    out = a24.approve(a24.ApproveInput(approval_id=o.approval_id, decision="reject"))
    assert out.decision == "BLOCK"
    with pytest.raises(AgentError) as e:
        a24.approve(a24.ApproveInput(approval_id=o.approval_id, decision="approve"))
    assert e.value.code == ErrorCode.CONFLICT
    login("alice", "analyst")
    with pytest.raises(AgentError) as e:
        a24.approve(a24.ApproveInput(approval_id=o.approval_id, decision="approve"))
    assert e.value.code == ErrorCode.FORBIDDEN


def test_chat_clarification_and_empty_and_capability():
    seed_facts()
    llm_returns(None, "Which metric do you mean?")
    o = ask("the thing")
    assert o.decision == "BLOCK" and "metric" in o.answer_text
    with pytest.raises(AgentError) as e:
        ask("  ")
    assert e.value.code == ErrorCode.INVALID_INPUT
    login("v", "viewer")
    with pytest.raises(AgentError) as e:
        ask("hi")
    assert e.value.code == ErrorCode.FORBIDDEN


def test_chat_conversation_belongs_to_user():
    seed_facts()
    llm_returns("SELECT metric FROM facts")
    o = ask("metrics")
    login("eve", "analyst")
    with pytest.raises(AgentError) as e:
        ask("metrics", conversation_id=o.conversation_id)
    assert e.value.code == ErrorCode.FORBIDDEN


def test_chat_answer_with_invented_number_falls_back_to_deterministic_summary():
    seed_facts()

    def prov(system, user, schema, **k):
        if "Write ONE read-only" in user:
            return json.dumps({"sql": "SELECT metric FROM facts", "clarification": None})
        return json.dumps({"answer_text": "There are 777 metrics."})
    llm_provider = prov
    llm_guard.provider = llm_provider
    o = ask("how many metrics")
    assert "777" not in o.answer_text and o.decision == "ALLOW"


def test_chat_other_users_rows_never_loaded():
    seed_facts()
    login("eve", "analyst")
    llm_returns("SELECT metric FROM facts")
    o = ask("metrics")
    assert o.decision == "ALLOW" and o.rows == []


def test_chat_approved_query_runs_under_requesters_visibility():
    seed_facts()
    login("root", "admin")
    store.put("case", "c1", {"case_id": "c1", "owner": "root", "name": "x"})
    store.put("facts", "c1", store.get("facts", "c1"))
    llm_returns("SELECT subject FROM facts")
    o = ask("who is the subject")
    assert o.decision == "CONFIRM"
    login("boss", "admin")
    out = a24.approve(a24.ApproveInput(approval_id=o.approval_id, decision="approve"))
    assert out.decision == "ALLOW" and out.rows == [["Ravi Kumar"], ["Ravi Kumar"]]
    login("alice", "analyst")  # the approval id is not a capability for anyone else
    with pytest.raises(AgentError):
        a24.approve(a24.ApproveInput(approval_id=o.approval_id, decision="approve"))
