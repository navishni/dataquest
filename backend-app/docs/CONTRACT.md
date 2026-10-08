# Contract status

The backend uses `frontend-app/docs/CONTRACT.md` as the HTTP contract. HTTP shapes match that contract; internal agent
models may use storage-oriented names only behind the router adapter.

## Derived models (source of truth: `backend/common/models.py`)

`Location(bbox, page_width, page_height, coordinate_system="pixel_top_left", bbox_unavailable_reason)`,
`Evidence(source_id, page_id, location, extraction_method, confidence)`, `EvidenceReference(source_id, block_id, page_id, excerpt, location)`,
`Check(name, status, detail)`, `Badge(scope_id, label, status, detail)`, `Unit`, `Cell`, `TableBlock`, `ChartBlock`, `FigureBlock`,
`EquationBlock`, `Block`, `PageOut`, `SourceDocument`, `ProposedAction`, `WarningItem(code, message, page_number, block_id)`.

## Decisions made where the spec was silent (confirm or change)

- `page_id` is `"{source_id}-p{page_number}"`; bboxes are pixel, top-left origin, rendered at `page_classification.render_dpi` (200).
- Error HTTP mapping is in `common/errors.py`.
- Dates compared by agent 16 produce a finding with pct_diff 100 when they differ (days shown in `abs_diff`).
- `GET /health/agents` returns a per-agent contract check built from `registry.AGENTS`.
- Additive audit fields: `seq`, `outcome`, `request_id`, `signature`, `ip_masked`, `user_agent_family`.

Agent 23 HTTP adapters map internal policy models to the frontend's `tables`, `resource_id`, and positional row shapes.
The editor policy applies to the viewer role, and a valid admin-signed grant can temporarily reveal the approved fields.
The admin role is never restricted by viewer policy.
