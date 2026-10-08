"""Agent registry used by GET /health/agents (the frontend 'contract check') and the README table."""
from .agents import (a01_file_validation as a01, a02_format_router as a02, a03_native_text as a03, a04_ocr as a04,
                     a05_layout as a05, a06_reading_order as a06, a07_table as a07, a08_spreadsheet as a08,
                     a09_chart_figure as a09, a10_equation as a10, a11_json_assembly as a11,
                     a12_confidence_validation as a12, a13_virtual_merge as a13, a14_case_linker as a14,
                     a15_fact_normalizer as a15, a16_cross_doc_reasoning as a16, a17_action_draft as a17,
                     a18_human_approval as a18, a19_audit as a19, a20_export as a20, a21_consensus as a21,
                     a22_url_guard as a22, a23_access_control as a23, a24_chat_sql as a24)
from .common import crypto
from .common.models import ProposedAction, SourceDocument, TableBlock, ChartBlock, EquationBlock

AGENTS = [
    ("01", "File Validation", "POST", "/agents/file-validation", a01.FileValidationInput, a01.FileValidationOutput),
    ("02", "Format Router", "POST", "/agents/format-router", a02.RouterInput, a02.RouterOutput),
    ("03", "Native Text", "POST", "/agents/native-text", a03.NativeTextInput, a03.NativeTextOutput),
    ("04", "OCR", "POST", "/agents/ocr", a04.OcrInput, a04.OcrOutput),
    ("05", "Layout", "POST", "/agents/layout", a05.LayoutInput, a05.LayoutOutput),
    ("06", "Reading Order", "POST", "/agents/reading-order", a06.ReadingOrderInput, a06.ReadingOrderOutput),
    ("07", "Table", "POST", "/agents/table", a07.TableInput, TableBlock),
    ("08", "Spreadsheet", "POST", "/agents/spreadsheet", a08.SpreadsheetInput, a08.SpreadsheetOutput),
    ("09", "Chart / Figure", "POST", "/agents/chart-figure", a09.ChartFigureInput, ChartBlock),
    ("10", "Equation", "POST", "/agents/equation", a10.EquationInput, EquationBlock),
    ("11", "JSON Assembly", "POST", "/agents/json-assembly", a11.AssemblyInput, SourceDocument),
    ("12", "Confidence & Validation", "POST", "/agents/confidence-validation", a12.ConfidenceInput, a12.ConfidenceOutput),
    ("13", "Virtual Merge", "POST", "/agents/virtual-merge", a13.MergeInput, a13.MergeOutput),
    ("14", "Case Linker", "POST", "/agents/case-linker", a14.LinkerInput, a14.LinkerOutput),
    ("15", "Fact Normalizer", "POST", "/agents/fact-normalizer", a15.FactInput, a15.FactOutput),
    ("16", "Cross-Document Reasoning", "POST", "/agents/cross-doc-reasoning", a16.ReasoningInput, a16.ReasoningOutput),
    ("17", "Action Draft", "POST", "/agents/action-draft", a17.DraftInput, ProposedAction),
    ("18", "Human Approval", "POST", "/agents/human-approval", a18.ApprovalInput, a18.ApprovalOutput),
    ("19", "Audit", "GET", "/agents/audit", a19.AuditQuery, a19.AuditResponse),
    ("20", "Export", "POST", "/agents/export", a20.ExportInput, a20.ExportOutput),
    ("21", "Consensus", "POST", "/agents/consensus", a21.ConsensusInput, a21.ConsensusOutput),
    ("22", "URL Guard", "POST", "/agents/url-ingest", a22.UrlInput, a22.UrlOutput),
    ("23", "Access Control", "GET", "/agents/access/schema", a23.PreviewInput, a23.SchemaOutput),
    ("24", "Chat (NL to SQL)", "POST", "/agents/chat-sql", a24.ChatInput, a24.ChatOutput),
]


def contract_check() -> list[dict]:
    out = []
    for aid, name, method, route, i, o in AGENTS:
        try:
            ih = crypto.hash_obj(i.model_json_schema())[:12]
            oh = crypto.hash_obj(o.model_json_schema())[:12]
            out.append({"agent": aid, "name": name, "method": method, "route": route, "status": "ok",
                        "input_schema_hash": ih, "output_schema_hash": oh})
        except Exception:  # noqa: BLE001
            out.append({"agent": aid, "name": name, "method": method, "route": route, "status": "error"})
    return out
