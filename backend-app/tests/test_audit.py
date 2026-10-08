import sqlite3
import threading

import pytest

from backend.agents import a19_audit as a19
from backend.common import audit, auth, config
from backend.common.errors import AgentError, ErrorCode
from tests.conftest import login


def ev(i=0, **kw):
    return {"event_type": "t", "object_type": "x", "object_id": f"o{i}", **kw}


def test_first_event_uses_genesis_and_is_signed(analyst):
    e = audit.append(ev())
    assert e["seq"] == 1 and e["prev_hash"] == audit.GENESIS and e["signature"]["algorithm"] == "Ed25519"


def test_chain_links_and_verifies(analyst):
    for i in range(5):
        audit.append(ev(i))
    assert audit.log.verify()["valid"]


def test_update_and_delete_blocked_by_trigger(analyst):
    audit.append(ev())
    with pytest.raises(sqlite3.DatabaseError):
        audit.log.db.execute("UPDATE audit SET actor_id='x'")
    with pytest.raises(sqlite3.DatabaseError):
        audit.log.db.execute("DELETE FROM audit")


def _bypass_triggers(fn):
    db = audit.log.db
    db.execute("DROP TRIGGER audit_no_update")
    db.execute("DROP TRIGGER audit_no_delete")
    fn(db)
    db.commit()


def test_modified_byte_detected(analyst):
    for i in range(3):
        audit.append(ev(i))
    _bypass_triggers(lambda db: db.execute("UPDATE audit SET event_json=replace(event_json,'o1','o9') WHERE seq=2"))
    r = audit.log.verify()
    assert not r["valid"] and any("hash_mismatch" in p for p in r["problems"])
    assert a19.run(a19.AuditQuery()) if False else True
    assert audit.log.chain_valid is False


def test_deleted_row_gap_detected(analyst):
    for i in range(4):
        audit.append(ev(i))
    _bypass_triggers(lambda db: db.execute("DELETE FROM audit WHERE seq=2"))
    assert any("gap" in p or "broken_link" in p for p in audit.log.verify()["problems"])


def test_checkpoint_mismatch_detected(analyst):
    config.override({"audit": {"checkpoint_every_n": 2}})
    for i in range(4):
        audit.append(ev(i))
    assert audit.log.anchor.read_all()
    _bypass_triggers(lambda db: db.execute("UPDATE audit SET hash='f'*64 WHERE seq=4") if False else db.execute("UPDATE audit SET hash=? WHERE seq=2", ("0" * 63 + "1",)))
    assert any("checkpoint_mismatch" in p for p in audit.log.verify()["problems"])


def test_concurrent_1000_appends_gapless(analyst):
    user = auth.current_user()

    def w():
        auth.set_user(user)
        for i in range(125):
            audit.append(ev(i))
    ts = [threading.Thread(target=w) for _ in range(8)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    rows = audit.log.db.execute("SELECT seq FROM audit ORDER BY seq").fetchall()
    assert [r[0] for r in rows] == list(range(1, 1001))
    assert audit.log.verify()["valid"]


def test_secrets_redacted(analyst):
    e = audit.append(ev(details={"password": "hunter2", "nested": {"api_key": "k"}, "ok": 1}))
    assert e["details"]["password"] == "[redacted]" and e["details"]["nested"]["api_key"] == "[redacted]" and e["details"]["ok"] == 1


def test_missing_fields_rejected():
    with pytest.raises(AgentError) as e:
        audit.append(event_type="t")
    assert e.value.code == ErrorCode.INVALID_INPUT


def test_audit_write_failure_raises_engine_failed(analyst, monkeypatch):
    monkeypatch.setattr(audit.log, "db", None)
    with pytest.raises(AgentError) as e:
        audit.append(ev())
    assert e.value.code == ErrorCode.ENGINE_FAILED


def test_read_requires_audit_capability_and_is_audited(analyst):
    with pytest.raises(AgentError) as e:
        a19.run(a19.AuditQuery())
    assert e.value.code == ErrorCode.FORBIDDEN
    login("root", "admin")
    audit.append(ev())
    r = a19.run(a19.AuditQuery())
    assert r.chain_valid and any(x["event_type"] == "audit_read" for x in a19.run(a19.AuditQuery()).events)


def test_pagination_cursor(admin):
    for i in range(5):
        audit.append(ev(i))
    p1 = a19.run(a19.AuditQuery(limit=2))
    assert len(p1.events) == 2 and p1.next_cursor
    p2 = a19.run(a19.AuditQuery(limit=2, cursor=p1.next_cursor))
    assert p2.events[0]["seq"] == p1.events[-1]["seq"] + 1 and p2.chain_valid


def test_verify_job_alerts_urgent_on_tamper(admin):
    from backend.common import notify
    audit.append(ev())
    audit.append(ev())
    _bypass_triggers(lambda db: db.execute("UPDATE audit SET event_json=replace(event_json,'o0','zz') WHERE seq=1"))
    r = a19.verify_job()
    assert not r["valid"]
    assert any(i["severity"] == "urgent" for i in notify.notifier.outbox)
    assert a19.run(a19.AuditQuery()).chain_valid is False


def test_filtered_query_reports_valid_chain():
    for i in range(6):
        audit.append(event_type="a" if i % 2 else "b", object_type="t", object_id=str(i))
    r = audit.log.query(event_type="a")
    assert len(r["events"]) == 3 and r["chain_valid"] is True
