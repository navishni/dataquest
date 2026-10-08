"""common/notify (Standard 3, owner Person A): admin push alerts via ntfy.

Env: NTFY_BASE_URL, NTFY_TOPIC_ADMIN, NTFY_TOKEN, NTFY_ENABLED ("true"/"1").
send() only enqueues into a transactional outbox (never raises, never blocks the user action).
flush() delivers with exponential backoff and a max-attempt cap; every outcome is audited
(notification_sent / notification_failed). Content rules: only ids, usernames, roles, event type,
timestamp, link; IPs masked; messages are scrubbed of anything resembling secrets.
Logins and access requests are never collapsed; other events collapse by dedupe_key with a count.
"""
import os
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Callable, Optional

from . import audit, config

NEVER_COLLAPSE = {"login_success", "access_requested"}
_IP = re.compile(r"\b(\d{1,3}\.\d{1,3}\.\d{1,3})\.\d{1,3}\b")
_SECRET = re.compile(r"(?i)(password|token|secret|bearer)\s*[:=]\s*\S+")

Transport = Callable[[str, dict, str], int]  # (url, headers, body) -> http status


def mask_ip(ip: Optional[str]) -> Optional[str]:
    return _IP.sub(r"\1.x", ip) if ip else ip


def scrub(text: str) -> str:
    return _SECRET.sub(r"\1=[removed]", _IP.sub(r"\1.x", text or ""))


def _default_transport(url: str, headers: dict, body: str) -> int:
    import httpx
    return httpx.post(url, content=body.encode("utf-8"), headers=headers, timeout=10).status_code


class Notifier:
    def __init__(self) -> None:
        self.outbox: list[dict] = []
        self.lock = threading.Lock()
        self.transport: Transport = _default_transport
        self.sleep = time.sleep

    def enabled(self) -> bool:
        return os.environ.get("NTFY_ENABLED", "").lower() in ("1", "true", "yes") and bool(
            os.environ.get("NTFY_BASE_URL")) and bool(os.environ.get("NTFY_TOPIC_ADMIN"))

    def send(self, event_type: str, severity: str, title: str, message: str,
             link: Optional[str] = None, dedupe_key: Optional[str] = None) -> Optional[str]:
        try:
            with self.lock:
                if dedupe_key and event_type not in NEVER_COLLAPSE:
                    for item in self.outbox:
                        if item["dedupe_key"] == dedupe_key and item["status"] == "pending":
                            item["count"] += 1
                            return item["outbox_id"]
                item = {"outbox_id": uuid.uuid4().hex, "event_type": event_type, "severity": severity,
                        "title": scrub(title), "message": scrub(message), "link": link,
                        "dedupe_key": dedupe_key, "count": 1, "attempts": 0, "status": "pending",
                        "created_at": datetime.now(timezone.utc).isoformat()}
                self.outbox.append(item)
                return item["outbox_id"]
        except Exception:  # noqa: BLE001 - notification failure must never block the action
            return None

    def _headers(self, item: dict) -> dict:
        prio = config.get("notify.severity_priority", {}).get(item["severity"], 3)
        h = {"Title": item["title"], "Priority": str(prio), "Tags": f"{item['severity']},{item['event_type']}"}
        if item["link"]:
            h["Click"] = item["link"]
        if os.environ.get("NTFY_TOKEN"):
            h["Authorization"] = "Bearer " + os.environ["NTFY_TOKEN"]
        return h

    def flush(self) -> dict:
        """Deliver pending items. Called by a background worker or on demand."""
        sent = failed = 0
        max_attempts = config.get("notify.max_attempts", 5)
        base = config.get("notify.backoff_base_seconds", 2)
        for item in [i for i in self.outbox if i["status"] == "pending"]:
            if not self.enabled():
                break
            body = item["message"] + (f" (x{item['count']})" if item["count"] > 1 else "")
            url = os.environ["NTFY_BASE_URL"].rstrip("/") + "/" + os.environ["NTFY_TOPIC_ADMIN"]
            while item["status"] == "pending":
                item["attempts"] += 1
                try:
                    code = self.transport(url, self._headers(item), body)
                except Exception:  # noqa: BLE001
                    code = 599
                if 200 <= code < 300:
                    item["status"] = "sent"
                    sent += 1
                    self._audit("notification_sent", item, "success")
                elif item["attempts"] >= max_attempts:
                    item["status"] = "failed"
                    failed += 1
                    self._audit("notification_failed", item, "error")
                else:
                    self.sleep(base ** item["attempts"])
        return {"sent": sent, "failed": failed}

    def _audit(self, et: str, item: dict, outcome: str) -> None:
        try:
            audit.append(event_type=et, object_type="notification", object_id=item["outbox_id"],
                         outcome=outcome, details={"notified_event": item["event_type"],
                                                   "attempts": item["attempts"]})
        except Exception:  # noqa: BLE001
            pass

    def start_worker(self, interval: float = 5.0) -> threading.Thread:
        def loop():
            while True:
                self.flush()
                time.sleep(interval)
        t = threading.Thread(target=loop, daemon=True)
        t.start()
        return t


notifier = Notifier()


def send(event_type: str, severity: str, title: str, message: str, link: Optional[str] = None,
         dedupe_key: Optional[str] = None) -> Optional[str]:
    return notifier.send(event_type, severity, title, message, link, dedupe_key)
