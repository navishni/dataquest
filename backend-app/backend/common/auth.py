"""Auth context. auth.current_user() -> {user_id, role, capabilities}.

Request-scoped via contextvars; the FastAPI dependency in main.py sets it from a bearer token
(JWT-style signed token using common/crypto HMAC; short expiry from config). Login/logout emit
audit events and ntfy notifications.
"""
import base64
import contextvars
import json
import time
from dataclasses import dataclass, field

from . import config, crypto
from .errors import AgentError, ErrorCode


@dataclass
class User:
    user_id: str
    role: str
    capabilities: list[str] = field(default_factory=list)
    tenant_id: str = "default"
    ip_masked: str | None = None
    user_agent_family: str | None = None

    def has(self, cap: str) -> bool:
        return cap in self.capabilities

    def as_dict(self) -> dict:
        return {"user_id": self.user_id, "display_name": self.user_id, "role": self.role,
                "capabilities": list(self.capabilities), "tenant_id": self.tenant_id}


_current: contextvars.ContextVar[User | None] = contextvars.ContextVar("pf_user", default=None)
_request_id: contextvars.ContextVar[str] = contextvars.ContextVar("pf_request_id", default="")


def make_user(user_id: str, role: str, **kw) -> User:
    caps = config.get(f"roles.{role}")
    if caps is None:
        raise AgentError(ErrorCode.FORBIDDEN, "Unknown role")
    return User(user_id=user_id, role=role, capabilities=list(caps),
                tenant_id=config.get("tenant_id", "default"), **kw)


def set_user(u: User | None):
    return _current.set(u)


def current_user() -> User:
    u = _current.get()
    if u is None:
        raise AgentError(ErrorCode.FORBIDDEN, "Authentication required")
    return u


def set_request_id(rid: str):
    return _request_id.set(rid)


def request_id() -> str:
    return _request_id.get()


def require(cap: str) -> User:
    u = current_user()
    if not u.has(cap):
        raise AgentError(ErrorCode.FORBIDDEN, f"Missing capability: {cap}")
    return u


# --- tokens (HMAC-signed, expiring). Replace with a real IdP/JWT lib in production. ---
def issue_token(user_id: str, role: str, ttl: int = 900, now: float | None = None) -> str:
    body = {"sub": user_id, "role": role, "exp": int((now or time.time()) + ttl)}
    raw = base64.urlsafe_b64encode(crypto.canonical_json(body)).decode()
    return f"{raw}.{crypto._hm(raw.encode())}"


def verify_token(token: str, now: float | None = None) -> User:
    import hmac
    try:
        raw, sig = token.split(".", 1)
        if not hmac.compare_digest(crypto._hm(raw.encode()), sig):
            raise ValueError
        body = json.loads(base64.urlsafe_b64decode(raw))
        if (now or time.time()) > body["exp"]:
            raise ValueError
        return make_user(body["sub"], body["role"])
    except (ValueError, KeyError, json.JSONDecodeError):
        raise AgentError(ErrorCode.FORBIDDEN, "Invalid or expired token")
