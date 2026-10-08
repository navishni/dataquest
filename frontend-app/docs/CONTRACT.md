# ParseFusion frontend - API contract

The frontend calls only the endpoints below. Every response must use the envelope `{ok:true,data,request_id}` or `{ok:false,error:{code,message,details?},request_id}`. Responses are validated with zod; unknown extra fields are allowed.

If an endpoint is unreachable, not an envelope, or returns 404 for the route, the UI shows `Backend not connected: <endpoint>`.

## Core endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | /config | modes, formats, scopes, action types, limits, confidence bands, severities, feature flags, pipeline stages, poll interval, auth mode |
| GET | /auth/me | user, role, `capabilities[]` (the only source for permission checks) |
| POST | /auth/login, /auth/logout | assumed; login returns `{access_token}` |
| GET/POST | /batches, GET /batches/{id} | list, create, read batches |
| POST | /batches/{id}/sources/{sid}/retry | assumed |
| GET | /jobs/{id} | job status; finished when `result` or `error` is present |
| GET | /sources/{id}, /sources/{id}/pages/{n}, /sources/{id}/markdown | canonical document, page units, markdown |
| GET/POST | /cases, GET /cases/{id} | cases with links, facts, comparisons, findings, actions, timeline |
| POST | /cases/{id}/findings/{fid}/review | assumed |
| GET | /metrics, /health/agents | metrics cards and agent health |

## Additional endpoints assumed (not in the spec)

Finding review, batch source retry, login/logout, chat approval, case detail aggregate. If missing, the related control shows `Backend not connected`.

## Optional field: `allowed_decisions`

`ProposedAction.allowed_decisions` (or `details.allowed` on a CONFLICT error) lets the backend decide which approval buttons are enabled. Buttons also require the matching capability.

## The 24 agents

### 01_fileValidation
```
Agent 01 - File Validation (gatekeeper).
Purpose: validates an uploaded file (type, size, integrity, password) and registers it as a source.
Input:   multipart form: `file` (binary) and optional `password`.
Output:  { source_id, sanitized_filename, sha256, detected_mime, size_bytes, page_count?, status, error?, duplicate_of? }
Endpoint: POST /agents/file-validation
```

### 02_formatRouter
```
Agent 02 - Format Router.
Purpose: picks the processing route for a source and classifies each page/unit.
Input:   { source_id }
Output:  { source_id, route, units: [{ unit_id, page_number, page_class }] }
Endpoint: POST /agents/format-router
```

### 03_nativeText
```
Agent 03 - Native Text Extraction.
Purpose: reads the embedded text layer of a page with positions.
Input:   { source_id, page_number }
Output:  { page_id, spans: [{ text, location, font?, confidence }], has_usable_text }
Endpoint: POST /agents/native-text
```

### 04_ocr
```
Agent 04 - OCR.
Purpose: recognises text in a page or region using a named engine.
Input:   { source_id, page_number, region?: BoundingBox, engine? }
Output:  { engine, lines: [{ text, location, confidence }], handwriting_detected? }
Endpoint: POST /agents/ocr
```

### 05_layoutDetection
```
Agent 05 - Layout Detection.
Purpose: finds typed regions (text, table, figure, ...) on a page.
Input:   { source_id, page_number }
Output:  { layout_class, regions: [{ region_id, type, location, confidence }] }
Endpoint: POST /agents/layout
```

### 06_readingOrder
```
Agent 06 - Reading Order.
Purpose: orders page regions the way a person would read them.
Input:   { page_id, region_ids }
Output:  { ordered_ids, reading_order_confidence, warnings }
Endpoint: POST /agents/reading-order
```

### 07_tableExtraction
```
Agent 07 - Table Extraction.
Purpose: turns a table region into cells with spans, headers and per-cell confidence.
Input:   { source_id, page_number, region_id }
Output:  TableBlock
Endpoint: POST /agents/table
```

### 08_spreadsheet
```
Agent 08 - Spreadsheet.
Purpose: reads workbook sheets, cells, formulas, merged ranges and probable tables.
Input:   { source_id }
Output:  { sheets: [{ name, hidden, used_range, merged_ranges, probable_tables, cells: [...] }] }
Endpoint: POST /agents/spreadsheet
```

### 09_chartFigure
```
Agent 09 - Chart / Figure.
Purpose: crops a chart or figure region and, for charts, returns grounded series and an insight.
Input:   { source_id, page_number, region_id }
Output:  ChartBlock | FigureBlock
Endpoint: POST /agents/chart-figure
```

### 10_equation
```
Agent 10 - Equation.
Purpose: recognises an equation region and reports whether the result was verified.
Input:   { source_id, page_number, region_id }
Output:  EquationBlock
Endpoint: POST /agents/equation
```

### 11_jsonAssembly
```
Agent 11 - JSON Assembly.
Purpose: assembles all stored extraction results of a source into one evidence-linked document.
Input:   { source_id }
Output:  SourceDocument
Endpoint: POST /agents/json-assembly
```

### 12_confidenceValidation
```
Agent 12 - Confidence & Validation.
Purpose: reports per-block confidence, validation checks and document-level badges.
Input:   { source_id }
Output:  { document_confidence, blocks: [{ block_id, confidence, confidence_breakdown, checks, needs_review }], badges }
Endpoint: POST /agents/confidence-validation
```

### 13_virtualMerge
```
Agent 13 - Virtual Merge.
Purpose: maps several sources into one virtual document with a global page order (no pixels are merged).
Input:   { batch_id, ordered_source_ids }
Output:  { virtual_document_id, pages: [{ virtual_page_number, source_id, page_number, page_id, boundary_start }] }
Endpoint: POST /agents/virtual-merge
```

### 14_caseLinker
```
Agent 14 - Case Linker.
Purpose: links sources to a case explicitly, or suggests links; a person confirms or rejects suggestions.
Input:   { case_id, source_ids, mode: "explicit" | "suggest" }
Output:  { links: [{ link_id, case_id, source_id, relationship_type, relationship_confidence, matching_signals,
                     linked_by, linked_at, human_verified }] }
Endpoints: POST /agents/case-linker, POST /agents/case-linker/decision  ({ link_id, decision } -> { link_id, human_verified })
```

### 15_factNormalizer
```
Agent 15 - Fact Normalizer.
Purpose: extracts comparable facts (subject, metric, value, period) from a case's documents, each tied to evidence.
Input:   { case_id }
Output:  { facts: [{ fact_id, subject, metric, raw_text, raw_value, normalized_value, currency?, unit?, frequency?,
                     period_start?, period_end?, category?, basis?, normalization_rule, confidence, ambiguity_notes?, evidence }] }
Endpoint: POST /agents/fact-normalizer
```

### 16_crossDocReasoning
```
Agent 16 - Cross-Document Reasoning.
Purpose: compares facts across documents and reports "potential discrepancy" findings for manual review.
Input:   { case_id }
Output:  { comparisons, not_comparable, findings } (see types below)
Endpoint: POST /agents/cross-doc-reasoning
```

### 17_actionDraft
```
Agent 17 - Action Draft.
Purpose: drafts a follow-up action (never sent) for a finding; a person must approve it.
Input:   { case_id, finding_id?, action_type }
Output:  ProposedAction
Endpoint: POST /agents/action-draft
Optional extension: `allowed_decisions` (string[]) on ProposedAction lets the backend say which decisions are valid now.
```

### 18_humanApproval
```
Agent 18 - Human Approval.
Purpose: moves a drafted action through review. The backend alone decides whether a transition is valid.
Input:   { action_id, decision, notes?, edited_fields?, rejection_reason? }
Output:  { action: ProposedAction, event: {...}, signature?: {...} }
Endpoint: POST /agents/human-approval
```

### 19_audit
```
Agent 19 - Audit.
Purpose: reads the tamper-evident audit trail (hash chained) with filters and cursor paging.
Input:   query params { from?, to?, actor?, event_type?, case_id?, object_id?, cursor? }
Output:  { events: [{ event_id, timestamp, actor_id, actor_role, event_type, object_type, object_id, details, prev_hash, hash }],
           chain_valid, next_cursor? }
Endpoint: GET /agents/audit
```

### 20_export
```
Agent 20 - Export.
Purpose: creates an export of a source, case or table in a chosen format; returns signed download links.
Input:   { scope: { type, ids }, format, options?: { masked?, include_evidence? } }
Output:  { export_id, format, download_url, content_hash, signed_manifest_url?, created_at }
Endpoints: POST /agents/export, GET /agents/export/history -> { exports: Output[] }
```

### 21_consensus
```
Agent 21 - Consensus (parser jury) and coverage audit.
Purpose: shows how several extractors agreed on each block and which page regions no block covers.
Input:   { source_id, page_number?, block_id? }
Output:  { blocks: [{ block_id, winner, agreement, candidates, escalated, needs_review }],
           coverage: [{ page_number, coverage_score, uncovered_regions }] }
Endpoint: POST /agents/consensus
```

### 22_urlGuardWebRender
```
Agent 22 - URL Guard and Web Render.
Purpose: checks a link against the ingestion policy and, if allowed, renders the page as a new source.
Input:   { url, purpose?, authorization_basis? }
Output:  { status: "allowed" | "blocked", reason?, robots_checked, source_id?, snapshot?, error? }
Endpoint: POST /agents/url-ingest
```

### 23_accessControl
```
Agent 23 - Access Control.
Purpose: shows which tables/columns are locked for the caller, previews visible data, and handles access requests.
Locked columns arrive with `locked: true` and null values. The UI never asks for or shows hidden data.
Endpoints:
  GET  /agents/access/schema                      -> { tables: [{ resource_id, schema, name, locked, columns: [{ name, type?, locked, hidden_for_viewers? }] }] }
  GET  /agents/access/preview?resource_id=&limit= -> { columns: [{ name, locked }], rows: (unknown | null)[][] }
  POST /agents/access/request    { resource_id, columns?, reason, requested_duration? } -> { request_id, status }
  GET  /agents/access/requests                    -> { requests: [{ request_id, user_id, resource_id, columns?, reason, status, requested_at, valid_until? }] }
  POST /agents/access/decision   { request_id, decision, valid_until?, notes? } -> { request_id, status, signature? }
  PUT  /agents/access/policy     { resource_id, hidden_columns } -> { resource, hidden_columns, updated_by, updated_at }

`/auth/me.capabilities` controls which role can edit the viewer policy (`manage_access`), request access (`request_access`),
or decide requests (`admin`). The provisioned roles are `editor`, `viewer`, and `admin`; the UI does not infer permissions
from role names. An admin grant is signed by the backend, expires at `valid_until`, and unlocks the approved columns only
for the request owner.
```

### 24_chatSql
```
Agent 24 - Chat (natural language to governed SQL).
Purpose: answers a question through a policy-checked read-only query. The backend returns ALLOW, CONFIRM or BLOCK.
Input:   { question, conversation_id?, scope?: { resource_ids?, case_id? } }
Output:  { conversation_id, sql?, analysis?, decision, reason?, approval_id?, columns?, rows?, answer_text?, citations?, error? }
Endpoint: POST /agents/chat-sql
```
