"""Shared contract types. Where docs/CONTRACT.md exists it wins; these mirror the shapes named in the spec.

NOTE: docs/CONTRACT.md was not supplied with the brief, so these are derived from the agent prompts.
Reconcile against the real contract before the day-1 freeze.
"""
from datetime import datetime, timezone
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WarningItem(Strict):
    code: str
    message: str
    page_number: Optional[int] = None
    block_id: Optional[str] = None


class Location(Strict):
    """bbox is [x1,y1,x2,y2] in page-image pixels, top-left origin."""
    bbox: Optional[list[float]] = None
    page_width: Optional[int] = None
    page_height: Optional[int] = None
    coordinate_system: str = "pixel_top_left"
    bbox_unavailable_reason: Optional[str] = None


class Evidence(Strict):
    source_id: str
    page_id: Optional[str] = None
    location: Location
    extraction_method: str
    confidence: float = Field(ge=0, le=1)


class EvidenceReference(Strict):
    source_id: str
    block_id: str
    page_id: Optional[str] = None
    excerpt: str
    location: Optional[Location] = None


class Check(Strict):
    name: str
    status: Literal["pass", "fail", "warn", "skipped"]
    detail: Optional[str] = None


class Badge(Strict):
    scope_id: str
    label: str
    status: Literal["pass", "mismatch", "warn", "info"]
    detail: Optional[str] = None


class Unit(Strict):
    unit_id: str
    page_number: int
    page_class: str


# ---- blocks ----------------------------------------------------------------
class Cell(Strict):
    row: int
    col: int
    row_span: int = 1
    col_span: int = 1
    is_header: bool = False
    raw_text: str = ""
    normalized: Optional[dict] = None  # {"value":..., "rule": "strip_thousands_separator"}
    location: Location
    confidence: float = Field(ge=0, le=1)
    alternatives: list[str] = []


class TableBlock(Strict):
    block_id: str
    type: Literal["table"] = "table"
    page_number: int
    cells: list[Cell]
    n_rows: int
    n_cols: int
    continues_from: Optional[str] = None
    continues_to: Optional[str] = None
    badges: list[Badge] = []
    warnings: list[WarningItem] = []
    evidence: Evidence


class ChartBlock(Strict):
    block_id: str
    type: Literal["chart"] = "chart"
    crop_url: str
    chart_type: Optional[str] = None
    title: Optional[str] = None
    axes: Optional[dict] = None
    series: Optional[list[dict]] = None
    insight_text: Optional[str] = None
    warnings: list[WarningItem] = []
    evidence: Evidence


class FigureBlock(Strict):
    block_id: str
    type: Literal["figure"] = "figure"
    crop_url: str
    caption: Optional[str] = None
    warnings: list[WarningItem] = []
    evidence: Evidence


class EquationBlock(Strict):
    block_id: str
    type: Literal["equation"] = "equation"
    latex: Optional[str] = None
    plain_text: Optional[str] = None
    verified: bool = False
    warnings: list[WarningItem] = []
    evidence: Evidence


class Block(Strict):
    """BaseBlock: every assembled block has all of these."""
    block_id: str
    type: str
    page_number: int
    reading_order_index: int
    text: Optional[str] = None
    data: Optional[dict] = None
    evidence: Evidence
    virtual_page_number: Optional[int] = None
    extra: dict[str, Any] = {}


class PageOut(Strict):
    page_id: str
    page_number: int
    image_url: str
    width: Optional[int] = None
    height: Optional[int] = None
    blocks: list[Block] = []


class SourceDocument(Strict):
    source_id: str
    status: Literal["complete", "partial", "failed"]
    warnings: list[WarningItem] = []
    errors: list[str] = []
    pages: list[PageOut]
    coverage_score: Optional[float] = None
    uncovered_regions: list[dict] = []
    content_hash: Optional[str] = None


class ProposedAction(Strict):
    action_id: str
    case_id: str
    finding_id: Optional[str] = None
    action_type: str
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    subject: str
    body: str
    status: Literal["draft", "in_review", "approved", "executed", "rejected", "cancelled"] = "draft"
    requires_human_approval: bool = True
    generated_by: str = "ai"
    labels: list[str] = ["ai_generated", "not_sent"]
    idempotency_key: str
    final_content_hash: str
    supporting_evidence: list[EvidenceReference] = []
    policy_checks: list[Check] = []
    drafted_by: str
    version: int = 1
    approvals: list[dict] = []
