import pytest

from backend.common import audit, config, notify


@pytest.fixture
def ntfy(monkeypatch, analyst):
    monkeypatch.setenv("NTFY_ENABLED", "true")
    monkeypatch.setenv("NTFY_BASE_URL", "https://ntfy.example")
    monkeypatch.setenv("NTFY_TOPIC_ADMIN", "topic-xyz")
    monkeypatch.setenv("NTFY_TOKEN", "tok123")
    calls = []
    notify.notifier.sleep = lambda s: None
    state = {"codes": [200]}

    def transport(url, headers, body):
        calls.append((url, headers, body))
        return state["codes"].pop(0) if len(state["codes"]) > 1 else state["codes"][0]
    notify.notifier.transport = transport
    return calls, state


def test_headers_and_url(ntfy):
    calls, _ = ntfy
    notify.send("access_requested", "high", "New access request", "request=abc user=bob", link="/admin/x")
    assert notify.notifier.flush() == {"sent": 1, "failed": 0}
    url, h, body = calls[0]
    assert url == "https://ntfy.example/topic-xyz" and h["Authorization"] == "Bearer tok123"
    assert h["Priority"] == "4" and h["Click"] == "/admin/x" and "Title" in h and "Tags" in h


def test_retry_on_500_then_success(ntfy):
    calls, state = ntfy
    state["codes"] = [500, 500, 200]
    notify.send("e", "normal", "t", "m")
    assert notify.notifier.flush()["sent"] == 1 and len(calls) == 3


def test_max_attempts_then_failed_and_audited(ntfy):
    calls, state = ntfy
    state["codes"] = [500]
    notify.send("e", "normal", "t", "m")
    assert notify.notifier.flush()["failed"] == 1
    assert len(calls) == config.get("notify.max_attempts")
    assert audit.log.db.execute("SELECT count(*) FROM audit WHERE event_type='notification_failed'").fetchone()[0] == 1


def test_success_audited(ntfy):
    notify.send("e", "normal", "t", "m")
    notify.notifier.flush()
    assert audit.log.db.execute("SELECT count(*) FROM audit WHERE event_type='notification_sent'").fetchone()[0] == 1


def test_no_sensitive_content_and_ip_masked(ntfy):
    calls, _ = ntfy
    notify.send("login_success", "normal", "Login", "user=bob ip=203.0.113.77 password=hunter2 token: abc")
    notify.notifier.flush()
    body = calls[0][2]
    assert "hunter2" not in body and "203.0.113.77" not in body and "203.0.113.x" in body


def test_dedupe_collapses_but_logins_do_not(ntfy):
    notify.send("login_failure", "high", "fails", "m", dedupe_key="u1")
    notify.send("login_failure", "high", "fails", "m", dedupe_key="u1")
    assert len(notify.notifier.outbox) == 1 and notify.notifier.outbox[0]["count"] == 2
    notify.send("login_success", "normal", "in", "m", dedupe_key="u1")
    notify.send("login_success", "normal", "in", "m", dedupe_key="u1")
    assert len(notify.notifier.outbox) == 3


def test_outage_never_blocks_action(ntfy):
    def boom(*a):
        raise ConnectionError("down")
    notify.notifier.transport = boom
    assert notify.send("e", "normal", "t", "m") is not None
    assert notify.notifier.flush()["failed"] == 1


def test_disabled_does_nothing(monkeypatch, analyst):
    monkeypatch.setenv("NTFY_ENABLED", "false")
    notify.send("e", "normal", "t", "m")
    assert notify.notifier.flush() == {"sent": 0, "failed": 0}
