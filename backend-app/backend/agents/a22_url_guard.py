"""Agent 22 - URL Guard + Web Render.

Guard first, always (no fetch happens when blocked): scheme must be http/https; host resolved via DNS (injectable `resolver`)
and every address must be public (blocks private, loopback, link-local incl. 169.254.169.254 metadata, multicast, reserved,
unspecified, IPv4-mapped private); config.url_guard allow/deny lists (deny wins; a non-empty allow list is exclusive);
authorization_basis required when config.url_guard.require_authorization_basis; robots.txt fetched and honoured
(injectable `robots_fetcher`; unreachable robots.txt = allowed but robots_checked=false); per-domain rate limit
(config.url_guard.rate_limit_per_domain_per_minute, injectable clock).
Rendering uses an injectable `renderer` (default: Playwright headless, no cookies, JS on, downloads off, size/timeouts from
config). The renderer receives `guard` so EVERY redirect hop is re-checked. If Playwright is not installed the result is status
"failed" with a plain error (never a crash). Login walls are returned as failed/blocked; no login is ever attempted.
On success the page text is registered as a source (origin.type="url") and the screenshot is stored encrypted.
Audit: url_allowed / url_blocked; blocked attempts notify the admin.
"""
import hashlib
import ipaddress
import socket
import time
import urllib.robotparser
import uuid
from datetime import datetime, timezone
from typing import Callable, Optional
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict

from ..common import audit, auth, config, crypto, notify
from ..common.errors import AgentError, ErrorCode
from ..common.store import store

USER_AGENT = "ParseFusionBot/1.0"


class UrlInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str
    purpose: Optional[str] = None
    authorization_basis: Optional[str] = None


class Snapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    screenshot_url: str
    fetched_at: str


class UrlOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: str
    reason: Optional[str] = None
    robots_checked: bool = False
    source_id: Optional[str] = None
    snapshot: Optional[Snapshot] = None
    error: Optional[str] = None


def _default_resolver(host: str) -> list[str]:
    return sorted({ai[4][0] for ai in socket.getaddrinfo(host, None)})


def _default_robots(url: str) -> Optional[str]:
    import httpx
    try:
        r = httpx.get(url, timeout=5, follow_redirects=False, headers={"User-Agent": USER_AGENT})
        return r.text if r.status_code == 200 else ("" if r.status_code in (401, 403, 404) else None)
    except Exception:  # noqa: BLE001
        return None


resolver: Callable[[str], list[str]] = _default_resolver
robots_fetcher: Callable[[str], Optional[str]] = _default_robots
clock: Callable[[], float] = time.time
renderer: Optional[Callable] = None
_hits: dict[str, list[float]] = {}


def _ip_blocked(ip: str) -> bool:
    a = ipaddress.ip_address(ip)
    if isinstance(a, ipaddress.IPv6Address) and a.ipv4_mapped:
        a = a.ipv4_mapped
    return (a.is_private or a.is_loopback or a.is_link_local or a.is_multicast or a.is_reserved or a.is_unspecified
            or str(a) == "169.254.169.254")


def guard(url: str, authorization_basis: Optional[str] = None, check_robots: bool = True) -> tuple[bool, Optional[str], bool]:
    """Returns (allowed, reason, robots_checked). Used for the first request and every redirect hop."""
    cfg = config.get("url_guard")
    p = urlparse(url)
    if p.scheme not in ("http", "https"):
        return False, "Only http and https URLs are allowed", False
    host = (p.hostname or "").lower()
    if not host:
        return False, "URL has no host", False
    if cfg["require_authorization_basis"] and not authorization_basis:
        return False, "An authorization basis is required for web ingestion", False
    if any(host == d or host.endswith("." + d) for d in cfg["deny_domains"]):
        return False, "Domain is on the deny list", False
    if cfg["allow_domains"] and not any(host == d or host.endswith("." + d) for d in cfg["allow_domains"]):
        return False, "Domain is not on the allow list", False
    try:
        ips = [host] if _is_ip(host) else resolver(host)
    except Exception:  # noqa: BLE001
        return False, "Host could not be resolved", False
    if not ips:
        return False, "Host could not be resolved", False
    if any(_ip_blocked(ip) for ip in ips):
        return False, "Address is not publicly routable", False
    robots_checked = False
    if check_robots:
        txt = robots_fetcher(f"{p.scheme}://{p.netloc}/robots.txt")
        if txt is not None:
            rp = urllib.robotparser.RobotFileParser()
            rp.parse(txt.splitlines())
            robots_checked = True
            if not rp.can_fetch(USER_AGENT, url):
                return False, "Disallowed by robots.txt", True
    return True, None, robots_checked


def _is_ip(h: str) -> bool:
    try:
        ipaddress.ip_address(h)
        return True
    except ValueError:
        return False


def _rate_ok(host: str) -> bool:
    lim = config.get("url_guard.rate_limit_per_domain_per_minute", 10)
    now = clock()
    hits = [t for t in _hits.get(host, []) if now - t < 60]
    if len(hits) >= lim:
        _hits[host] = hits
        return False
    hits.append(now)
    _hits[host] = hits
    return True


def playwright_renderer(url: str, guard_fn: Callable[[str], bool], max_bytes: int, timeout_s: int) -> dict:  # pragma: no cover
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise AgentError(ErrorCode.ENGINE_FAILED, "Web renderer is not installed")
    with sync_playwright() as pw:
        br = pw.chromium.launch(headless=True)
        ctx = br.new_context(accept_downloads=False, java_script_enabled=True)
        page = ctx.new_page()

        def route(r):
            if not guard_fn(r.request.url):
                r.abort()
            else:
                r.continue_()
        page.route("**/*", route)
        page.goto(url, timeout=timeout_s * 1000)
        text = page.inner_text("body")[:max_bytes]
        shot = page.screenshot(full_page=True)
        final = page.url
        br.close()
    return {"text": text, "screenshot": shot, "final_url": final, "login_wall": False}


def run(inp: UrlInput) -> UrlOutput:
    u = auth.current_user()
    host = (urlparse(inp.url).hostname or "").lower()
    ok, reason, robots = guard(inp.url, inp.authorization_basis)
    if ok and not _rate_ok(host):
        ok, reason = False, "Rate limit exceeded for this domain"
    uid = hashlib.sha256(inp.url.encode()).hexdigest()[:16]
    if not ok:
        audit.append(event_type="url_blocked", object_type="url", object_id=uid, outcome="denied",
                     details={"host": host, "reason": reason})
        notify.send("url_blocked", "high", "Blocked URL ingestion attempt", f"user={u.user_id} url_id={uid}")
        return UrlOutput(status="blocked", reason=reason, robots_checked=robots)
    audit.append(event_type="url_allowed", object_type="url", object_id=uid, details={"host": host, "purpose": inp.purpose})
    rend = renderer or playwright_renderer
    try:
        res = rend(inp.url, lambda x: guard(x, inp.authorization_basis, check_robots=False)[0],
                   config.get("url_guard.max_response_bytes"), config.get("url_guard.timeout_seconds"))
    except AgentError as e:
        return UrlOutput(status="failed", robots_checked=robots, error=e.message)
    except Exception:  # noqa: BLE001
        return UrlOutput(status="failed", robots_checked=robots, error="Page could not be rendered")
    ok2, why, _ = guard(res.get("final_url", inp.url), inp.authorization_basis, check_robots=False)
    if not ok2:
        audit.append(event_type="url_blocked", object_type="url", object_id=uid, outcome="denied",
                     details={"host": host, "reason": "redirect: " + (why or "")})
        notify.send("url_blocked", "high", "Blocked URL redirect", f"user={u.user_id} url_id={uid}")
        return UrlOutput(status="blocked", reason="Redirect target not allowed: " + (why or ""), robots_checked=robots)
    if res.get("login_wall"):
        return UrlOutput(status="blocked", reason="Page requires login; no login is attempted", robots_checked=robots)
    sid = str(uuid.uuid4())
    html = ("<html><body><pre>" + res["text"].replace("<", "&lt;") + "</pre></body></html>").encode()
    store.put_file(sid, html, {"source_id": sid, "display_name": host or "web page", "filename": f"{host}.html",
                               "sha256": crypto.sha256_hex(html), "detected_mime": "text/html", "size_bytes": len(html),
                               "uploaded_by": u.user_id, "tenant_id": u.tenant_id,
                               "origin": {"type": "url", "url": inp.url}})
    snap_id = f"{sid}:snapshot"
    store.put("snapshot", snap_id, crypto.encrypt(res["screenshot"], snap_id))
    return UrlOutput(status="ingested", robots_checked=robots, source_id=sid,
                     snapshot=Snapshot(screenshot_url=f"/sources/{sid}/snapshot",
                                       fetched_at=datetime.now(timezone.utc).isoformat()))
