"""Process-wide counters exposed at GET /metrics."""
import threading
from collections import defaultdict

_lock = threading.Lock()
_c: dict[str, int] = defaultdict(int)


def inc(name: str, n: int = 1) -> None:
    with _lock:
        _c[name] += n


def snapshot() -> dict:
    with _lock:
        d = dict(_c)
    for k in ("llm_calls", "ungrounded_rejections", "abstentions", "llm_retries"):
        d.setdefault(k, 0)
    d["retry_rate"] = (d["llm_retries"] / d["llm_calls"]) if d["llm_calls"] else 0.0
    return d


def reset() -> None:
    with _lock:
        _c.clear()
