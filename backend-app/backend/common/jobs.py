"""Job registry for long work: POST returns 202 {job_id}; poll GET /jobs/{id}."""
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

from .errors import AgentError, ErrorCode

_pool = ThreadPoolExecutor(max_workers=4)
_jobs: dict[str, dict] = {}
_lock = threading.Lock()


def submit(fn: Callable[[], Any], owner: str) -> str:
    jid = uuid.uuid4().hex
    with _lock:
        _jobs[jid] = {"job_id": jid, "status": "running", "result": None, "error": None, "owner": owner}

    def run():
        try:
            res = fn()
            if hasattr(res, "model_dump"):
                res = res.model_dump(mode="json")
            with _lock:
                _jobs[jid].update(status="done", result=res)
        except AgentError as e:
            with _lock:
                _jobs[jid].update(status="failed", error={"code": e.code.value, "message": e.message})
        except Exception:  # noqa: BLE001
            with _lock:
                _jobs[jid].update(status="failed", error={"code": "ENGINE_FAILED", "message": "Job failed"})

    _pool.submit(run)
    return jid


def get(jid: str, owner: str) -> dict:
    with _lock:
        j = _jobs.get(jid)
    if j is None or j["owner"] != owner:
        raise AgentError(ErrorCode.NOT_FOUND, "Job not found")
    return {k: v for k, v in j.items() if k != "owner"}


def reset() -> None:
    with _lock:
        _jobs.clear()
