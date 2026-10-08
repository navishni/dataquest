# ParseFusion Backend

Document-intelligence backend: 24 agents (deterministic, or LLM-guarded), each a pure `run(Input) -> Output` function in
`backend/agents/aNN_*.py` plus a thin FastAPI route in `backend/routers/agents.py`. Shared code is in `backend/common/`.

Design rules that hold everywhere: the model proposes and code verifies; no LLM provider configured means the system
**abstains** rather than guesses; every domain action is audited (fail closed); secrets only come from the environment;
every threshold lives in `backend/config.json`.

## Setup

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
sudo apt-get install -y tesseract-ocr          # OCR (agent 04); without it scanned pages return ENGINE_FAILED
cp .env.example .env                           # then fill in keys (see below)
python -m pytest tests -q                      # 276 tests
PF_ADMIN_USER=admin PF_ADMIN_PASSWORD='change-me-long' uvicorn backend.main:app --port 8000
```

Interactive API docs: `http://localhost:8000/docs`. Terminate TLS in front of the app (HSTS is set by the app).

### Environment

| Variable | Purpose |
|---|---|
| `PF_KEK_B64` | 32-byte base64 key-encryption key. If unset, a random one is generated per process (dev only: stored blobs are lost on restart). |
| `PF_SIGNING_KEY_B64` | Ed25519 private key (32 raw bytes, base64) for audit/approval/export signatures. Same dev behaviour. |
| `PF_HMAC_KEY_B64` | HMAC key for signed download URLs and access tokens. |
| `PF_AUDIT_DB` | SQLite path for the audit log (default in-memory). Use a real path in any non-test run. |
| `PF_ADMIN_USER`, `PF_ADMIN_PASSWORD` | Bootstraps the admin account at startup. |
| `PF_EDITOR_USER`, `PF_EDITOR_PASSWORD` | Bootstraps the editor account. The editor can manage viewer column visibility. |
| `PF_VIEWER_USER`, `PF_VIEWER_PASSWORD` | Bootstraps the viewer account. The viewer can request access to locked columns. |
| `NTFY_ENABLED`, `NTFY_BASE_URL`, `NTFY_TOPIC_ADMIN`, `NTFY_TOKEN` | Optional ntfy notifications (outbox + backoff + dedupe). |
| `PARSEFUSION_CONFIG` | Path to an alternative config JSON. |

Generate keys: `python -c "import os,base64;print(base64.b64encode(os.urandom(32)).decode())"`.

### Plugging in a model

`backend/common/llm_guard.py` is the only LLM gateway. Set a provider once at startup:

```python
from backend.common import llm_guard
llm_guard.provider = lambda system, user, schema, *, temperature, seed, max_tokens: "<raw JSON string>"
```

The gateway enforces a strict JSON schema, one retry, then abstains. Numbers, dates, quotes and entities in the output must
exist in the supplied evidence (Indian `1,00,000` and `100000` compare equal) or the field is dropped.
Used by: 09 (chart series/insight), 15 (subject only), 17 (optional polish), 24 (SQL proposal and answer text).

## Response envelope

Success `{ "ok": true, "data": ..., "request_id": "..." }`, failure `{ "ok": false, "error": {"code","message","details?"}, "request_id" }`.

| Code | HTTP | Code | HTTP |
|---|---|---|---|
| INVALID_INPUT | 400 | NOT_FOUND | 404 |
| UNSUPPORTED_FORMAT | 415 | FORMAT_MISMATCH | 415 |
| EMPTY_FILE | 400 | PASSWORD_REQUIRED | 422 |
| CORRUPT_FILE | 422 | TOO_LARGE | 413 |
| FORBIDDEN | 403 | ENGINE_FAILED | 500 |
| TIMEOUT | 504 | CONFLICT | 409 |

Add `?async=true` to any authenticated POST to get `202 {"job_id"}` and poll `GET /jobs/{id}` (owner only).
Unhandled exceptions return a generic `ENGINE_FAILED` envelope with no stack trace.

## Auth and roles

`POST /auth/login` returns a 15-minute HMAC bearer token. Set separate credentials for all three roles in `.env`:
admin, editor, and viewer. Roles map to capabilities in `config.json`; the frontend checks only the capabilities returned
by `/auth/me`. Editors can hide columns from viewers. Viewers see locked values as null and can request access; only an
admin can approve or reject a request. Approval creates a signed, expiring grant that is applied by every access-controlled
read. Accounts are provisioned from environment variables at startup; there are no default passwords.
Users in the same tenant can see shared cases and sources, while the access policy controls which fields they receive.
Every request writes an `http_request` audit event (success, denied or error).

## Routes

Platform: `POST /auth/login|logout`, `GET /auth/me`, `GET /config`, `GET|POST /batches`, `POST /batches/{id}/sources`,
`GET /sources/{id}` (+ `/markdown`, `/pages/{n}`, `/pages/{n}/image`, `/crops/{crop_id}`), `GET|POST /cases`, `GET /cases/{id}`,
`GET /jobs/{id}`, `GET /metrics` (admin), `GET /health/agents`, `POST /pipeline/run`,
`GET /exports/{id}/download|manifest?exp=&sig=` (needs the signed URL **and** a bearer token).

Agents (all `POST` unless noted): `/agents/file-validation` (multipart), `format-router`, `native-text`, `ocr`, `layout`,
`reading-order`, `table`, `spreadsheet`, `chart-figure`, `equation`, `json-assembly`, `confidence-validation`, `virtual-merge`,
`case-linker`, `case-linker/decision`, `fact-normalizer`, `cross-doc-reasoning`, `action-draft`, `human-approval`,
`GET audit`, `audit/verify`, `export`, `GET export/history`, `consensus`, `url-ingest`,
`GET access/schema|preview|requests`, `POST access/request|decision`, `PUT access/policy`, `chat-sql`, `chat-sql/approve`.

Enums: route ids `pdf_native pdf_scanned pdf_mixed pdf_blank image docx pptx xlsx csv eml html`;
page_class `native_text scanned mixed blank image_only`;
region labels `text title table figure chart equation header footer list caption signature stamp form_field`;
layout_class `single_column multi_column form invoice_like slide spreadsheet_like other`;
relationship_type `same_party supporting_document supersedes unknown`.

## Pipeline

`POST /pipeline/run {"source_id"}` runs 02 → (03 and/or 04) → 05 → 06 → 07/09/10 per region → 21 → 11 → 12 and stores
the SourceDocument, Markdown and confidence report. A failing step is recorded and the run continues (`partial`).
The reasoning chain is explicit: 14 (link sources to a case) → 15 (facts) → 16 (findings) → 17 (draft) → 18 (approve/execute).

## Agents in brief

| # | Agent | What it does / key rule |
|---|---|---|
| 01 | File validation | Magic-byte sniffing, size/page/zip-bomb/pixel limits, password PDFs, SHA-256 dedupe, encrypted storage. |
| 02 | Format router | Route id per file and per-page class from chars + image ratio (config thresholds). EML attachments become child sources. |
| 03 | Native text | PyMuPDF spans/blocks with bboxes in `pixel_top_left`; garbage-ratio check flags broken text layers. |
| 04 | OCR | Engine registry (Tesseract here; PaddleOCR if installed); region OCR maps back to page coordinates. |
| 05 | Layout | Geometric detector (tables, figures, headings by font size, header/footer bands); duplicates merged by IoU. |
| 06 | Reading order | XY-cut with column gap, headers first, footers last, captions attached; second pass gives the confidence. |
| 07 | Table | pdfplumber (ruled then text strategy) or OCR rows; true row/col spans; cross-page continuation; total-row badge. |
| 08 | Spreadsheet | openpyxl sheets as tables; formulas kept as text with cached values; hidden sheets flagged. |
| 09 | Chart/figure | Crop always; chart series only via llm_guard and only from numbers present in OCR text. |
| 10 | Equation | LaTeX only from a real recogniser, then parse gate + re-render similarity gate; otherwise unverified. |
| 11 | JSON assembly | Builds SourceDocument from stored outputs; CONFLICT lists exactly what is missing; content hash + Markdown. |
| 12 | Confidence | Weighted components, validators (arithmetic, dates, currency format, IBAN/Luhn, cross-block), `needs_review`. |
| 13 | Virtual merge | Mapping only; deterministic `virtual_document_id`. |
| 14 | Case linker | Explicit links, or suggestions from entity/id/date/domain signals; suggestions are never auto-confirmed. |
| 15 | Fact normalizer | Rule-based facts with evidence; Decimal normalisation (lakh/crore/k, parentheses); ranges abstain; no FX conversion. |
| 16 | Cross-doc reasoning | Comparability checks, then Decimal arithmetic; neutral wording; always human review. |
| 17 | Action draft | Template drafts, never sent; policy checks; idempotent action id. |
| 18 | Human approval | Table-driven state machine, separation of duties, Ed25519 signature bound to the content hash, idempotent execute, rollback if the audit write fails. |
| 19 | Audit | Hash-chained, signed, trigger-protected SQLite log; read API and full verification job. |
| 20 | Export | json/markdown/csv/html; server-side masking; HMAC signed URLs with expiry; signed manifest; AES-GCM at rest. |
| 21 | Consensus | Clusters native and OCR candidates by IoU, weighted vote, coverage audit of uncovered ink. |
| 22 | URL guard | SSRF guard (private, loopback, link-local, metadata, IPv4-mapped), redirect re-check, robots.txt, rate limit, no logins. |
| 23 | Access control | Default deny, column-level, expiring signed grants, row scoping before any query. |
| 24 | Chat SQL | sqlglot policy gate, ALLOW/CONFIRM/BLOCK, queries run on a throw-away DB holding only visible columns. |

Each agent module's docstring documents its algorithm and the exact confidence formula.

## Tests

`tests/` holds 276 tests: crypto, audit chain, notify, llm_guard, intake (01/02/03/08), vision (04-07/09/10), assembly and
pipeline (11/12/13/21), reasoning (14-17), governance (18-20, 22-24) and HTTP. Highest-risk areas have adversarial cases:
the SQL gate (DDL/DML, stacked statements, comments, system tables, function denylist, locked columns via subquery/CTE/
JOIN/WHERE/ORDER BY), approval tampering and double execution, SSRF targets and redirects, signed-URL expiry.
Test PDFs are generated by `tests/fixtures/make.py`; no sample data ships in the app.

## Known limits (be aware before relying on it)

- **State is in memory.** `common/store.py` is an in-process store (files encrypted, records plain); swap it for a database
  and object store before any multi-process or persistent deployment. Users are also in memory (`routers/platform.py`).
- Audit log persists only if `PF_AUDIT_DB` is a file; the WORM anchor is an interface with a local-file implementation.
- Optional engines not bundled: PaddleOCR (agent 04), pix2tex (agent 10), Playwright (agent 22). Without them: single OCR
  engine, equations return `NO_EQUATION_ENGINE`, URL ingest returns `failed`.
- Charts are not de-rendered from pixels; series exist only if a provider is configured and the numbers appear in the OCR text.
- OCR has no deskew or denoise step. DOCX/PPTX/HTML native text needs LibreOffice, which is not wired in; those routes are
  detected and registered but not text-extracted.
- Export formats: json, markdown, csv, html. XLSX/PDF/DOCX renderers are plug-in points (`RENDERERS`) but not implemented.
- Chat SQL rejects set operations (`UNION`, `INTERSECT`) and unknown columns by design; the gate is deliberately conservative.
- Layout is geometric, not a trained model; quality on dense or unusual pages is limited.
- Python 3.13 was used for development; the spec targets 3.11.
- `docs/CONTRACT.md` was not supplied with the spec; see that file.
