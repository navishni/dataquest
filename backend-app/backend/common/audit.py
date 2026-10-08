"""audit.append(event): immutable audit log (Standard 4, owner Person E).

Layers: (1) append-only SQLite table with BEFORE UPDATE/DELETE triggers that RAISE (in Postgres:
revoke UPDATE/DELETE/TRUNCATE from the app role and keep the same trigger); (2) hash chain
hash = SHA256(prev_hash || canonical_json(event_without_hash_and_signature)); (3) Ed25519 signature per
event with kid; (4) signed checkpoints every N events written to an external WORM anchor
(`WormAnchor` interface; default is an in-process append-only list, swap for S3 Object Lock);
(5) single writer lock => gapless seq, no forks; (6) no edit/delete API, corrections are new events
with correction_of.  Fail closed: append raises ENGINE_FAILED so callers roll back.
"""
import os
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any, Optional
import uuid

from . import auth, config, crypto
from .errors import AgentError, ErrorCode

GENESIS = "0" * 64
MANDATORY = ["actor_id", "actor_role", "tenant_id", "request_id", "event_type", "object_type",
             "object_id", "outcome"]
SECRET_KEYS = ("password", "passwd", "secret", "token", "authorization", "api_key", "cookie")


class WormAnchor:
    """External write-once log. Only append and read; production: S3 Object Lock / WORM host."""

    def __init__(self) -> None:
        self._entries: list[dict] = []

    def write(self, checkpoint: dict) -> None:
        self._entries.append(dict(checkpoint))

    def read_all(self) -> list[dict]:
        return [dict(e) for e in self._entries]


def redact(details: Any) -> Any:
    if isinstance(details, dict):
        return {k: ("[redacted]" if any(s in k.lower() for s in SECRET_KEYS) else redact(v))
                for k, v in details.items()}
    if isinstance(details, list):
        return [redact(v) for v in details]
    return details


class AuditLog:
    def __init__(self, db_path: str = ":memory:", anchor: Optional[WormAnchor] = None) -> None:
        self.db = sqlite3.connect(db_path, check_same_thread=False)
        self.anchor = anchor or WormAnchor()
        self.lock = threading.Lock()
        self.chain_valid = True
        self._init_db()

    def _init_db(self) -> None:
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS audit (
            seq INTEGER PRIMARY KEY AUTOINCREMENT,
            event_json TEXT NOT NULL, prev_hash TEXT NOT NULL, hash TEXT NOT NULL, signature TEXT NOT NULL,
            event_type TEXT, actor_id TEXT, object_id TEXT, case_id TEXT, ts TEXT);
        CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit
            BEGIN SELECT RAISE(ABORT, 'audit is append-only'); END;
        CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit
            BEGIN SELECT RAISE(ABORT, 'audit is append-only'); END;
        """)
        self.db.commit()

    # --- write path -------------------------------------------------------------
    def append(self, event: dict) -> dict:
        try:
            ev = self._complete(event)
            with self.lock:
                row = self.db.execute("SELECT seq, hash FROM audit ORDER BY seq DESC LIMIT 1").fetchone()
                seq, prev = (row[0] + 1, row[1]) if row else (1, GENESIS)
                ev["seq"] = seq
                ev["prev_hash"] = prev
                h = crypto.sha256_hex(prev.encode() + crypto.canonical_json(ev))
                sig = crypto.sign(h.encode())
                import json
                self.db.execute(
                    "INSERT INTO audit(seq,event_json,prev_hash,hash,signature,event_type,actor_id,object_id,case_id,ts)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (seq, crypto.canonical_json(ev).decode(), prev, h, json.dumps(sig), ev["event_type"],
                     ev["actor_id"], ev["object_id"], ev.get("case_id"), ev["timestamp"]))
                self.db.commit()
                n = config.get("audit.checkpoint_every_n", 100)
                if n and seq % n == 0:
                    self._checkpoint(seq, h)
            out = dict(ev)
            out.update(hash=h, signature=sig)
            return out
        except AgentError:
            raise
        except Exception:  # noqa: BLE001
            raise AgentError(ErrorCode.ENGINE_FAILED, "Audit write failed")

    def _complete(self, event: dict) -> dict:
        ev = dict(event)
        u = None
        try:
            u = auth.current_user()
        except AgentError:
            pass
        ev.setdefault("actor_id", u.user_id if u else "system")
        ev.setdefault("actor_role", u.role if u else "system")
        ev.setdefault("tenant_id", u.tenant_id if u else config.get("tenant_id", "default"))
        ev.setdefault("request_id", auth.request_id() or "internal")
        ev.setdefault("outcome", "success")
        if u:
            ev.setdefault("ip_masked", u.ip_masked)
            ev.setdefault("user_agent_family", u.user_agent_family)
        missing = [k for k in MANDATORY if not ev.get(k)]
        if missing:
            raise AgentError(ErrorCode.INVALID_INPUT, f"Audit event missing: {missing}")
        if ev["outcome"] not in ("success", "denied", "error"):
            raise AgentError(ErrorCode.INVALID_INPUT, "Bad outcome")
        ev["details"] = redact(ev.get("details") or {})
        ev["timestamp"] = datetime.now(timezone.utc).isoformat()  # server clock only
        ev["event_id"] = uuid.uuid4().hex
        for k in ("seq", "hash", "signature", "prev_hash"):
            ev.pop(k, None)
        return ev

    def _checkpoint(self, seq: int, h: str) -> dict:
        payload = crypto.canonical_json({"seq": seq, "hash": h})
        cp = {"seq": seq, "hash": h, "signed_at": datetime.now(timezone.utc).isoformat(),
              "signature": crypto.sign(payload)}
        self.anchor.write(cp)
        return cp

    def write_checkpoint(self) -> Optional[dict]:
        with self.lock:
            row = self.db.execute("SELECT seq, hash FROM audit ORDER BY seq DESC LIMIT 1").fetchone()
            return self._checkpoint(row[0], row[1]) if row else None

    # --- read path -----------------------------------------------------------------
    def _rows(self, where: str = "", args: tuple = (), limit: Optional[int] = None) -> list[tuple]:
        q = "SELECT seq,event_json,prev_hash,hash,signature FROM audit " + where + " ORDER BY seq"
        if limit:
            q += f" LIMIT {int(limit)}"
        return self.db.execute(q, args).fetchall()

    def verify(self, start_seq: int = 1, end_seq: Optional[int] = None) -> dict:
        """Recompute hashes, signatures, gaps, linkage and check checkpoints against the anchor."""
        import json
        problems: list[str] = []
        rows = self._rows()
        by_seq = {r[0]: r for r in rows}
        expected = 1
        prev = GENESIS
        for seq, ej, ph, h, sg in rows:
            if seq != expected:
                problems.append(f"gap_before_seq_{seq}")
                expected = seq
            if ph != prev:
                problems.append(f"broken_link_at_{seq}")
            ev = json.loads(ej)
            if crypto.sha256_hex(ph.encode() + crypto.canonical_json(ev)) != h:
                problems.append(f"hash_mismatch_at_{seq}")
            if not crypto.verify(h.encode(), json.loads(sg)):
                problems.append(f"bad_signature_at_{seq}")
            prev, expected = h, expected + 1
        for cp in self.anchor.read_all():
            r = by_seq.get(cp["seq"])
            if r is None or r[3] != cp["hash"]:
                problems.append(f"checkpoint_mismatch_at_{cp['seq']}")
            if not crypto.verify(crypto.canonical_json({"seq": cp["seq"], "hash": cp["hash"]}), cp["signature"]):
                problems.append(f"checkpoint_signature_bad_at_{cp['seq']}")
        if rows and self.anchor.read_all():
            last_anchor = max(c["seq"] for c in self.anchor.read_all())
            if rows[-1][0] < last_anchor:
                problems.append("truncated_after_checkpoint")
        valid = not problems
        if not valid:
            self.chain_valid = False
        return {"valid": valid, "problems": problems, "events_checked": len(rows)}

    def query(self, frm: str | None = None, to: str | None = None, actor: str | None = None,
              event_type: str | None = None, case_id: str | None = None, object_id: str | None = None,
              cursor: int | None = None, limit: int = 100) -> dict:
        import json
        cl, args = [], []
        for col, val, op in (("ts", frm, ">="), ("ts", to, "<="), ("actor_id", actor, "="),
                             ("event_type", event_type, "="), ("case_id", case_id, "="),
                             ("object_id", object_id, "="), ("seq", cursor, ">")):
            if val is not None:
                cl.append(f"{col} {op} ?")
                args.append(val)
        rows = self._rows(("WHERE " + " AND ".join(cl)) if cl else "", tuple(args), limit + 1)
        page = rows[:limit]
        events = []
        for seq, ej, ph, h, sg in page:
            e = json.loads(ej)
            e.update(hash=h, signature=json.loads(sg))
            events.append(e)
        # range verification: hash, signature and linkage to the event before the range
        valid = self.chain_valid
        for seq, ej, ph, h, sg in page:
            ev = json.loads(ej)
            if crypto.sha256_hex(ph.encode() + crypto.canonical_json(ev)) != h or \
                    not crypto.verify(h.encode(), json.loads(sg)):
                valid = False
            # linkage: every returned event must point at the stored hash of its predecessor (works for filtered,
            # non-contiguous result sets too)
            if seq > 1:
                before = self.db.execute("SELECT hash FROM audit WHERE seq=?", (seq - 1,)).fetchone()
                if before is None or before[0] != ph:
                    valid = False
        cps = self.anchor.read_all()
        out = {"events": events, "chain_valid": valid,
               "next_cursor": str(page[-1][0]) if len(rows) > limit else None}
        if cps:
            c = cps[-1]
            out["checkpoint"] = {"seq": c["seq"], "hash": c["hash"], "signed_at": c["signed_at"]}
        return out


log = AuditLog(os.environ.get("PF_AUDIT_DB", ":memory:"))


def append(event: dict | None = None, **kw) -> dict:
    return log.append({**(event or {}), **kw})


def reset_for_tests() -> None:
    global log
    log = AuditLog()
