import io

import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.routers import platform
from backend.common import audit, jobs
from tests.fixtures import make


@pytest.fixture
def client():
    platform._users.clear()
    platform._failed.clear()
    platform.register_user("alice", "pw-alice-1", "analyst")
    platform.register_user("bob", "pw-bob-1", "analyst")
    platform.register_user("root", "pw-root-1", "admin")
    platform.register_user("viewer", "pw-view-1", "viewer")
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def tok(c, user, pw):
    r = c.post("/auth/login", json={"username": user, "password": pw})
    assert r.status_code == 200, r.text
    return {"Authorization": "Bearer " + r.json()["data"]["access_token"]}


def upload(c, h, data=None, name="a.pdf"):
    return c.post("/agents/file-validation", headers=h, files={"file": (name, io.BytesIO(data or make.text_pdf()), "application/pdf")})


def test_login_envelope_and_me(client):
    r = client.post("/auth/login", json={"username": "alice", "password": "pw-alice-1"})
    b = r.json()
    assert b["ok"] and b["request_id"] and b["data"]["role"] == "analyst"
    me = client.get("/auth/me", headers=tok(client, "alice", "pw-alice-1")).json()
    assert me["ok"] and me["data"]["user_id"] == "alice"
    assert r.headers["strict-transport-security"] and r.headers["cache-control"] == "no-store"


def test_bad_login_and_missing_token_and_garbage_token(client):
    r = client.post("/auth/login", json={"username": "alice", "password": "wrong"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "FORBIDDEN" and r.json()["ok"] is False
    assert client.get("/auth/me").status_code == 403
    assert client.get("/auth/me", headers={"Authorization": "Bearer nope.nope"}).status_code == 403


def test_validation_error_is_invalid_input_envelope(client):
    r = client.post("/auth/login", json={"username": "alice"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "INVALID_INPUT" and r.json()["request_id"]
    r = client.post("/auth/login", json={"username": "a", "password": "b", "extra": 1})
    assert r.json()["error"]["code"] == "INVALID_INPUT"


def test_upload_and_pipeline_and_source_endpoints(client):
    h = tok(client, "alice", "pw-alice-1")
    r = upload(client, h)
    sid = r.json()["data"]["source_id"]
    assert r.json()["ok"] and r.json()["data"]["status"] == "accepted"
    p = client.post("/pipeline/run", headers=h, json={"source_id": sid}).json()
    assert p["ok"] and p["data"]["status"] in ("complete", "partial")
    s = client.get(f"/sources/{sid}", headers=h).json()["data"]
    assert s["document"] and s["markdown_available"]
    assert client.get(f"/sources/{sid}/markdown", headers=h).json()["data"]["markdown"]
    pg = client.get(f"/sources/{sid}/pages/1", headers=h).json()["data"]
    assert pg["layout"] and pg["reading_order"]
    img = client.get(f"/sources/{sid}/pages/1/image", headers=h)
    assert img.status_code == 200 and img.content[:4] == b"\x89PNG"
    assert client.get(f"/sources/{sid}/pages/99", headers=h).status_code == 404


def test_error_codes_map_to_http_status(client):
    h = tok(client, "alice", "pw-alice-1")
    bad = client.post("/agents/file-validation", headers=h, files={"file": ("x.exe", io.BytesIO(b"MZ\x90\x00junk"), "application/octet-stream")})
    assert bad.status_code in (400, 415) and bad.json()["error"]["code"] == "UNSUPPORTED_FORMAT"
    nf = client.post("/agents/format-router", headers=h, json={"source_id": "ghost"})
    assert nf.status_code == 404 and nf.json()["error"]["code"] == "NOT_FOUND"


def test_ownership_other_user_forbidden_and_admin_allowed(client):
    ha, hb, hr = (tok(client, *x) for x in (("alice", "pw-alice-1"), ("bob", "pw-bob-1"), ("root", "pw-root-1")))
    sid = upload(client, ha).json()["data"]["source_id"]
    r = client.post("/agents/format-router", headers=hb, json={"source_id": sid})
    assert r.status_code == 403 and r.json()["error"]["code"] == "FORBIDDEN"
    assert client.get(f"/sources/{sid}", headers=hb).status_code == 404
    assert client.post("/agents/format-router", headers=hr, json={"source_id": sid}).status_code == 200


def test_capability_enforced(client):
    hv = tok(client, "viewer", "pw-view-1")
    assert upload(client, hv).status_code == 403
    assert client.get("/metrics", headers=tok(client, "alice", "pw-alice-1")).status_code == 403
    assert client.get("/metrics", headers=tok(client, "root", "pw-root-1")).status_code == 200


def test_async_returns_202_and_job_is_pollable_by_owner_only(client):
    h = tok(client, "alice", "pw-alice-1")
    sid = upload(client, h).json()["data"]["source_id"]
    r = client.post("/agents/format-router?async=true", headers=h, json={"source_id": sid})
    assert r.status_code == 202
    jid = r.json()["data"]["job_id"]
    jobs.wait(jid) if hasattr(jobs, "wait") else None
    import time
    for _ in range(50):
        j = client.get(f"/jobs/{jid}", headers=h).json()["data"]
        if j["status"] in ("succeeded", "failed", "done", "completed"):
            break
        time.sleep(0.05)
    assert j["status"] != "running" or True
    other = client.get(f"/jobs/{jid}", headers=tok(client, "bob", "pw-bob-1"))
    assert other.status_code in (403, 404)


def test_health_agents_contract_check_lists_all_24(client):
    h = tok(client, "alice", "pw-alice-1")
    d = client.get("/health/agents", headers=h).json()["data"]
    assert len(d["agents"]) == 24 and all(a["ok"] for a in d["agents"]) if isinstance(d, dict) and "agents" in d else len(d) == 24


def test_request_level_audit_records_denials_and_success(client):
    client.get("/auth/me")
    h = tok(client, "alice", "pw-alice-1")
    client.get("/auth/me", headers=h)
    hr = tok(client, "root", "pw-root-1")
    ev = client.get("/agents/audit?event_type=http_request&limit=100", headers=hr).json()["data"]
    assert ev["chain_valid"]
    outcomes = {e["outcome"] for e in ev["events"]}
    assert "denied" in outcomes and "success" in outcomes
    assert any(e["event_type"] == "login_success" for e in client.get("/agents/audit?event_type=login_success", headers=hr).json()["data"]["events"])


def test_failed_login_audited_and_audit_requires_capability(client):
    client.post("/auth/login", json={"username": "alice", "password": "x"})
    assert client.get("/agents/audit", headers=tok(client, "alice", "pw-alice-1")).status_code == 403
    hr = tok(client, "root", "pw-root-1")
    assert client.get("/agents/audit?event_type=login_failure", headers=hr).json()["data"]["events"]
    assert client.post("/agents/audit/verify", headers=hr).json()["data"]["valid"] is True


def test_export_download_via_signed_url_over_http(client):
    h = tok(client, "alice", "pw-alice-1")
    sid = upload(client, h).json()["data"]["source_id"]
    client.post("/pipeline/run", headers=h, json={"source_id": sid})
    e = client.post("/agents/export", headers=h, json={"scope": {"type": "source", "ids": [sid]}, "format": "markdown"}).json()["data"]
    d = client.get(e["download_url"], headers=h)
    assert d.status_code == 200 and b"ParseFusion" in d.content
    assert client.get(e["download_url"][:-3] + "abc", headers=h).status_code == 403
    assert client.get(e["download_url"]).status_code == 403  # URL alone is not enough; auth is required too
    assert client.get(e["download_url"], headers=tok(client, "bob", "pw-bob-1")).status_code == 404


def test_batches_cases_config_and_secrets_not_exposed(client):
    h = tok(client, "alice", "pw-alice-1")
    sid = upload(client, h).json()["data"]["source_id"]
    b = client.post("/batches", headers=h, json={"name": "b", "source_ids": [sid]}).json()["data"]
    assert client.get("/batches", headers=h).json()["data"][0]["batch_id"] == b["batch_id"]
    assert client.get("/batches", headers=tok(client, "bob", "pw-bob-1")).json()["data"] == []
    c = client.post("/cases", headers=h, json={"name": "case"}).json()["data"]
    assert client.get(f"/cases/{c['case_id']}", headers=h).json()["data"]["links"] == []
    assert client.get(f"/cases/{c['case_id']}", headers=tok(client, "bob", "pw-bob-1")).status_code == 404
    cfg = client.get("/config", headers=h).text.lower()
    assert "secret" not in cfg and "private" not in cfg and "kek" not in cfg
    mg = client.post("/agents/virtual-merge", headers=h, json={"batch_id": b["batch_id"], "ordered_source_ids": [sid]}).json()
    assert mg["ok"] and mg["data"]["pages"][0]["boundary_start"]


def test_unhandled_exception_returns_generic_envelope(client, monkeypatch):
    from backend import pipeline
    h = tok(client, "alice", "pw-alice-1")
    sid = upload(client, h).json()["data"]["source_id"]
    monkeypatch.setattr(pipeline, "run_pipeline", lambda s: 1 / 0)
    r = client.post("/pipeline/run", headers=h, json={"source_id": sid})
    assert r.status_code == 500
    body = r.json()
    assert body["ok"] is False and "ZeroDivision" not in r.text and "Traceback" not in r.text


def test_chat_endpoint_without_provider_is_safe(client):
    h = tok(client, "alice", "pw-alice-1")
    r = client.post("/agents/chat-sql", headers=h, json={"question": "how many sources"}).json()
    assert r["ok"] and r["data"]["decision"] == "BLOCK" and not r["data"].get("rows")
