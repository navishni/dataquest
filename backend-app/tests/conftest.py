import pytest

from backend.common import audit, auth, config, jobs, llm_guard, metrics, notify
from backend.common.store import store


@pytest.fixture(autouse=True)
def clean_state(monkeypatch):
    store.reset()
    audit.reset_for_tests()
    jobs.reset()
    metrics.reset()
    config.load(force=True)
    notify.notifier.outbox.clear()
    llm_guard.provider = None
    for k in ("NTFY_ENABLED", "NTFY_BASE_URL", "NTFY_TOPIC_ADMIN", "NTFY_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    auth.set_request_id("test-req")
    tok = auth.set_user(None)
    yield
    auth._current.reset(tok)


def login(user_id="alice", role="analyst"):
    auth.set_user(auth.make_user(user_id, role))
    return user_id


@pytest.fixture
def analyst():
    login("alice", "analyst")
    return "alice"


@pytest.fixture
def admin():
    login("root", "admin")
    return "root"
