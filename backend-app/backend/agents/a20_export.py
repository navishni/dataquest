"""Agent 20 - Export. Renderers are plugins: RENDERERS[format] = render(scope_data, options) -> bytes.

Implemented formats: json, markdown, csv, html (config.formats gates what is offered; add xlsx/pdf/docx by registering a renderer).
Scopes: source (assembled document), case (facts + findings), table (a stored table block). Access: `export` capability and ownership
of every id (mixed-permission scope -> FORBIDDEN for the whole request). Masking happens server-side BEFORE rendering: emails, long digit
runs and (for case scope) subject/raw values; masked is forced true unless the caller has `unmask`. include_evidence=false strips evidence.
content_hash = SHA-256 of output bytes; manifest = canonical JSON {inputs, versions, options, hash, created_at} signed with Ed25519; download
URLs are HMAC-signed with an expiry (config.export.url_ttl_seconds). Output bytes are stored AES-GCM encrypted. Unmasked exports notify the admin.
"""
import csv
import html
import io
import json
import re
import uuid
from datetime import datetime, timezone
from typing import Callable, Optional

from pydantic import BaseModel, ConfigDict

from ..common import audit, auth, config, crypto, notify
from ..common.errors import AgentError, ErrorCode
from ..common.store import store

_EMAIL = re.compile(r"([\w.+-])[\w.+-]*@([\w-]+\.[\w.]+)")
_DIGITS = re.compile(r"\d{6,}")


class Scope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: str
    ids: list[str]


class Options(BaseModel):
    model_config = ConfigDict(extra="forbid")
    masked: Optional[bool] = None
    include_evidence: bool = True


class ExportInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scope: Scope
    format: str
    options: Optional[Options] = None


class ExportOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    export_id: str
    format: str
    download_url: str
    content_hash: str
    signed_manifest_url: Optional[str] = None
    created_at: str


def mask_text(s):
    if not isinstance(s, str):
        return s
    s = _EMAIL.sub(lambda m: f"{m.group(1)}***@{m.group(2)}", s)
    return _DIGITS.sub(lambda m: "•" * (len(m.group(0)) - 2) + m.group(0)[-2:], s)


def mask_obj(o, keys=("text", "raw_text", "raw_value", "normalized_value", "subject", "excerpt", "body")):
    if isinstance(o, dict):
        return {k: (mask_text(v) if k in keys and isinstance(v, str) else mask_obj(v, keys)) for k, v in o.items()}
    if isinstance(o, list):
        return [mask_obj(v, keys) for v in o]
    return o


def strip_evidence(o):
    if isinstance(o, dict):
        return {k: strip_evidence(v) for k, v in o.items() if k not in ("evidence", "evidence_references", "location")}
    if isinstance(o, list):
        return [strip_evidence(v) for v in o]
    return o


def _r_json(data: dict, opts: dict) -> bytes:
    return json.dumps(data, sort_keys=True, indent=2, ensure_ascii=False).encode()


def _r_markdown(data: dict, opts: dict) -> bytes:
    out = []
    for item in data["items"]:
        if item["kind"] == "source":
            out.append(store.get("markdown", item["id"]) and (mask_text(store.get("markdown", item["id"])) if opts["masked"] else store.get("markdown", item["id"])) or "")
        elif item["kind"] == "case":
            out.append(f"# Case {item['id']}\n\n## Facts\n" + "\n".join(f"- {f['metric']}: {f['raw_value']} ({f['subject']})" for f in item["data"]["facts"])
                       + "\n\n## Findings\n" + "\n".join(f"- {f['title']} [{f['severity']}]" for f in item["data"]["findings"]))
        else:
            t = item["data"]
            grid = {(c["row"], c["col"]): c["raw_text"] for c in t["cells"]}
            rows = [[grid.get((r, c), "") for c in range(t["n_cols"])] for r in range(t["n_rows"])]
            out.append("\n".join(["| " + " | ".join(rows[0]) + " |", "|" + "---|" * t["n_cols"]] + ["| " + " | ".join(r) + " |" for r in rows[1:]]))
    return "\n\n".join(out).encode()


def _r_csv(data: dict, opts: dict) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf)
    for item in data["items"]:
        if item["kind"] == "source":
            w.writerow(["source_id", "page", "block_id", "type", "text", "confidence"])
            for p in item["data"]["pages"]:
                for b in p["blocks"]:
                    w.writerow([item["id"], p["page_number"], b["block_id"], b["type"], (b.get("text") or "").replace("\n", " "),
                                (b.get("evidence") or {}).get("confidence", "")])
        elif item["kind"] == "case":
            w.writerow(["case_id", "fact_id", "subject", "metric", "raw_value", "normalized_value", "currency"])
            for f in item["data"]["facts"]:
                w.writerow([item["id"], f["fact_id"], f["subject"], f["metric"], f["raw_value"], f["normalized_value"], f["currency"]])
        else:
            for r in range(item["data"]["n_rows"]):
                w.writerow([c["raw_text"] for c in sorted((c for c in item["data"]["cells"] if c["row"] == r), key=lambda c: c["col"])])
    return buf.getvalue().encode()


def _r_html(data: dict, opts: dict) -> bytes:
    body = "".join(f"<h2>{html.escape(i['kind'])} {html.escape(i['id'])}</h2><pre>{html.escape(json.dumps(i['data'], indent=1, sort_keys=True, ensure_ascii=False))}</pre>" for i in data["items"])
    return f"<!doctype html><meta charset='utf-8'><title>ParseFusion export</title>{body}".encode()


RENDERERS: dict[str, Callable[[dict, dict], bytes]] = {"json": _r_json, "markdown": _r_markdown, "csv": _r_csv, "html": _r_html}


def _gather(scope: Scope, u: auth.User) -> list[dict]:
    items = []
    for i in scope.ids:
        if scope.type == "source":
            m = store.get("source", i)
            if m is None:
                raise AgentError(ErrorCode.NOT_FOUND, "Source not found")
            if m.get("uploaded_by") != u.user_id and not u.has("admin"):
                raise AgentError(ErrorCode.FORBIDDEN, "Not permitted to export one or more items")
            d = store.get("document", i)
            if d is None:
                raise AgentError(ErrorCode.CONFLICT, "Source has no assembled document", {"missing": [i]})
            items.append({"kind": "source", "id": i, "data": d})
        elif scope.type == "case":
            c = store.get("case", i)
            if c is None:
                raise AgentError(ErrorCode.NOT_FOUND, "Case not found")
            if c["owner"] != u.user_id and not u.has("admin"):
                raise AgentError(ErrorCode.FORBIDDEN, "Not permitted to export one or more items")
            items.append({"kind": "case", "id": i, "data": {"facts": (store.get("facts", i) or {"facts": []})["facts"],
                                                          "findings": [f for f in store.list("finding") if f["case_id"] == i]}})
        else:
            t = store.get("table", i)
            if t is None:
                raise AgentError(ErrorCode.NOT_FOUND, "Table not found")
            sm = store.get("source", t["evidence"]["source_id"]) or {}
            if sm.get("uploaded_by") != u.user_id and not u.has("admin"):
                raise AgentError(ErrorCode.FORBIDDEN, "Not permitted to export one or more items")
            items.append({"kind": "table", "id": i, "data": t})
    return items


def run(inp: ExportInput) -> ExportOutput:
    u = auth.require("export")
    cfg_formats = config.get("formats", [])
    if inp.format not in cfg_formats or inp.format not in RENDERERS:
        raise AgentError(ErrorCode.UNSUPPORTED_FORMAT, "Export format not available", {"available": [f for f in cfg_formats if f in RENDERERS]})
    if inp.scope.type not in config.get("export_scopes", []):
        raise AgentError(ErrorCode.INVALID_INPUT, "Unknown scope type")
    if not inp.scope.ids:
        raise AgentError(ErrorCode.INVALID_INPUT, "Scope is empty")
    o = inp.options or Options()
    masked = True if (o.masked is None or not u.has("unmask")) else o.masked
    items = _gather(inp.scope, u)
    if masked:
        for it in items:
            it["data"] = mask_obj(it["data"])
    if not o.include_evidence:
        for it in items:
            it["data"] = strip_evidence(it["data"])
    opts = {"masked": masked, "include_evidence": o.include_evidence}
    payload = RENDERERS[inp.format]({"items": items}, opts)
    eid = uuid.uuid4().hex
    created = datetime.now(timezone.utc).isoformat()
    chash = crypto.sha256_hex(payload)
    manifest = {"export_id": eid, "inputs": [{"kind": i["kind"], "id": i["id"], "content_hash": crypto.hash_obj(i["data"])} for i in items],
                "versions": {"format": inp.format, "renderer": "v1"}, "options": opts, "content_hash": chash, "created_at": created}
    msig = crypto.sign(crypto.canonical_json(manifest))
    ttl = config.get("export.url_ttl_seconds", 300)
    rec = {"export_id": eid, "owner": u.user_id, "format": inp.format, "scope": inp.scope.model_dump(), "content_hash": chash,
           "created_at": created, "manifest": manifest, "manifest_signature": msig, "masked": masked}
    store.put("export_blob", eid, crypto.encrypt(payload, eid))
    store.put("export", eid, rec)
    audit.append(event_type="export_created", object_type="export", object_id=eid,
                 details={"format": inp.format, "scope": inp.scope.type, "count": len(items), "masked": masked, "hash": chash})
    if not masked:
        notify.send("export_unmasked", "high", "Unmasked export created", f"export={eid} user={u.user_id} role={u.role}")
    return ExportOutput(export_id=eid, format=inp.format, download_url=crypto.sign_url(f"/exports/{eid}/download", ttl),
                        content_hash=chash, signed_manifest_url=crypto.sign_url(f"/exports/{eid}/manifest", ttl), created_at=created)


def history() -> list[dict]:
    u = auth.current_user()
    out = [{k: r[k] for k in ("export_id", "format", "content_hash", "created_at", "masked")} for r in store.list("export")
           if r["owner"] == u.user_id or u.has("admin")]
    audit.append(event_type="export_history_viewed", object_type="export", object_id="history")
    return sorted(out, key=lambda r: r["created_at"], reverse=True)


def download(export_id: str, exp: int, sig: str, manifest: bool = False) -> tuple[bytes, str]:
    u = auth.current_user()
    res = f"/exports/{export_id}/{'manifest' if manifest else 'download'}"
    if not crypto.verify_url(res, exp, sig):
        raise AgentError(ErrorCode.FORBIDDEN, "Link invalid or expired")
    rec = store.get("export", export_id)
    if rec is None or (rec["owner"] != u.user_id and not u.has("admin")):
        raise AgentError(ErrorCode.NOT_FOUND, "Export not found")
    audit.append(event_type="export_downloaded", object_type="export", object_id=export_id, details={"manifest": manifest})
    if manifest:
        return json.dumps({"manifest": rec["manifest"], "signature": rec["manifest_signature"]}, sort_keys=True).encode(), "application/json"
    mime = {"json": "application/json", "markdown": "text/markdown", "csv": "text/csv", "html": "text/html"}.get(rec["format"], "application/octet-stream")
    return crypto.decrypt(store.get("export_blob", export_id), export_id), mime
