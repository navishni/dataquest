"""Agent 08 - Spreadsheet. Deterministic.

openpyxl is loaded twice (data_only=False for formulas, True for cached values). raw_value = stored value
(ISO string for dates), displayed_value = value rendered through number_format (dates, percent, currency,
thousands separators, fixed decimals; unknown formats fall back to str()). Hidden sheets/rows/columns are kept
with hidden=true. Merged ranges are listed as A1 ranges and the covered cells are included. probable_tables:
connected blocks of non-empty cells (separated by empty rows/columns) with >=2 rows and >=2 cols; the block is
reported as an A1 range. CSV/TSV: delimiter via csv.Sniffer and encoding via charset-normalizer, one sheet.
Cells are capped at config.limits.max_cells_per_sheet; if capped a CELLS_TRUNCATED warning carries true counts.
Files over 20 MB are loaded read_only (hidden/merged metadata then unavailable -> warning).
"""
import csv
import io
import re
from datetime import date, datetime, time
from typing import Any, Optional

from openpyxl import load_workbook
from openpyxl.utils import get_column_letter, range_boundaries
from pydantic import BaseModel, ConfigDict

from ..common import audit, config
from ..common.errors import AgentError, ErrorCode
from ..common.models import WarningItem
from ..common.store import store

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


class SpreadsheetInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_id: str


class SheetCell(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ref: str
    raw_value: Any = None
    displayed_value: Optional[str] = None
    formula: Optional[str] = None
    number_format: Optional[str] = None
    hidden: Optional[bool] = None


class Sheet(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    hidden: bool
    used_range: Optional[str] = None
    merged_ranges: list[str] = []
    probable_tables: list[str] = []
    cells: list[SheetCell]


class SpreadsheetOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sheets: list[Sheet]
    warnings: list[WarningItem] = []


def _split_fmt(fmt: str) -> str:
    return fmt.split(";")[0]


def format_value(v: Any, fmt: Optional[str]) -> Optional[str]:
    if v is None:
        return None
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    f = _split_fmt(fmt or "General")
    if isinstance(v, (datetime, date, time)):
        if isinstance(v, time):
            return v.strftime("%H:%M:%S")
        py = (f.replace("yyyy", "%Y").replace("yy", "%y").replace("mmmm", "%B").replace("mmm", "%b")
              .replace("dd", "%d").replace("hh", "%H").replace("ss", "%S"))
        py = re.sub(r"(?<![%a-zA-Z])mm(?![a-zA-Z])", "%m", py)
        py = re.sub(r"(?<=%H:)%m", "%M", py)
        if "%" not in py:
            py = "%Y-%m-%d" if not isinstance(v, datetime) or (v.hour, v.minute, v.second) == (0, 0, 0) else "%Y-%m-%d %H:%M:%S"
        return v.strftime(py)
    if isinstance(v, (int, float)):
        cur = re.match(r'^[\[\]$€£¥"A-Za-z .]*?([$€£¥]|Rs\.?|INR)', f)
        symbol = cur.group(1) if cur and any(ch in f for ch in "#0") else ""
        pct = "%" in f
        m = re.search(r"0\.(0+)", f)
        dec = len(m.group(1)) if m else 0
        val = v * 100 if pct else v
        body = f"{abs(val):,.{dec}f}" if "," in f else f"{abs(val):.{dec}f}"
        s = f"{symbol}{body}{'%' if pct else ''}"
        if val < 0:
            s = f"({s})" if "(" in f else "-" + s
        if f in ("General", "") and isinstance(v, float):
            return repr(round(v, 10)).rstrip("0").rstrip(".") if "." in repr(v) else str(v)
        return s
    return str(v)


def _raw(v: Any) -> Any:
    if isinstance(v, (datetime, date, time)):
        return v.isoformat()
    return v


def probable_tables(grid: dict[tuple[int, int], Any]) -> list[str]:
    if not grid:
        return []
    cells, seen, out = set(grid), set(), []
    for start in sorted(cells):
        if start in seen:
            continue
        comp, stack = [], [start]
        seen.add(start)
        while stack:
            r, c = stack.pop()
            comp.append((r, c))
            for dr in (-1, 0, 1):
                for dc in (-1, 0, 1):
                    n = (r + dr, c + dc)
                    if n in cells and n not in seen:
                        seen.add(n)
                        stack.append(n)
        r0, r1 = min(r for r, _ in comp), max(r for r, _ in comp)
        c0, c1 = min(c for _, c in comp), max(c for _, c in comp)
        if r1 > r0 and c1 > c0:
            out.append(f"{get_column_letter(c0)}{r0}:{get_column_letter(c1)}{r1}")
    return sorted(out)


def _from_csv(data: bytes) -> tuple[Sheet, list[WarningItem]]:
    from charset_normalizer import from_bytes
    best = from_bytes(data).best()
    text = str(best) if best else data.decode("utf-8", errors="replace")
    try:
        dialect = csv.Sniffer().sniff(text[:8192], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    rows = list(csv.reader(io.StringIO(text), dialect))
    cap = config.get("limits.max_cells_per_sheet")
    cells, grid, total = [], {}, 0
    for r, row in enumerate(rows, 1):
        for c, val in enumerate(row, 1):
            if val == "":
                continue
            total += 1
            grid[(r, c)] = val
            if len(cells) < cap:
                cells.append(SheetCell(ref=f"{get_column_letter(c)}{r}", raw_value=val, displayed_value=val))
    warns = []
    if total > cap:
        warns.append(WarningItem(code="CELLS_TRUNCATED", message=f"Returned {cap} of {total} cells"))
    used = f"A1:{get_column_letter(max((c for _, c in grid), default=1))}{max((r for r, _ in grid), default=1)}" if grid else None
    return Sheet(name="Sheet1", hidden=False, used_range=used, probable_tables=probable_tables(grid), cells=cells), warns


def run(inp: SpreadsheetInput) -> SpreadsheetOutput:
    meta = store.meta(inp.source_id)
    data = store.get_file(inp.source_id)
    mime = meta["detected_mime"]
    if mime == "text/csv":
        sheet, warns = _from_csv(data)
        out = SpreadsheetOutput(sheets=[sheet], warnings=warns)
    elif mime == XLSX_MIME:
        out = _from_xlsx(data)
    else:
        raise AgentError(ErrorCode.UNSUPPORTED_FORMAT, "Not a spreadsheet")
    store.put("spreadsheet", inp.source_id, out.model_dump(mode="json"))
    audit.append(event_type="spreadsheet_run", object_type="source", object_id=inp.source_id,
                 details={"sheets": len(out.sheets)})
    return out


def _from_xlsx(data: bytes) -> SpreadsheetOutput:
    ro = len(data) > 20 * 1024 * 1024
    try:
        wf = load_workbook(io.BytesIO(data), data_only=False, read_only=ro)
        wv = load_workbook(io.BytesIO(data), data_only=True, read_only=ro)
    except Exception:  # noqa: BLE001
        raise AgentError(ErrorCode.CORRUPT_FILE, "Workbook is corrupt")
    cap = config.get("limits.max_cells_per_sheet")
    warns = [WarningItem(code="READ_ONLY_MODE", message="Large file: hidden/merged metadata unavailable")] if ro else []
    sheets = []
    for ws in wf.worksheets:
        wsv = wv[ws.title]
        merged = [str(m) for m in ws.merged_cells.ranges] if not ro else []
        merged_cover = set()
        for m in merged:
            c0, r0, c1, r1 = range_boundaries(m)
            merged_cover |= {(r, c) for r in range(r0, r1 + 1) for c in range(c0, c1 + 1)}
        hidden_rows = {i for i, d in ws.row_dimensions.items() if d.hidden} if not ro else set()
        hidden_cols = set()
        if not ro:
            for k, d in ws.column_dimensions.items():
                if d.hidden:
                    lo, hi = (d.min or 0), (d.max or 0)
                    hidden_cols |= set(range(lo, hi + 1)) if lo else set()
        cells, grid, total = [], {}, 0
        for row in ws.iter_rows():
            for cell in row:
                v = cell.value
                if v is None and (cell.row, cell.column) not in merged_cover:
                    continue
                if v is None:
                    continue
                total += 1
                grid[(cell.row, cell.column)] = v
                if len(cells) >= cap:
                    continue
                formula = v if isinstance(v, str) and v.startswith("=") else None
                cached = wsv[cell.coordinate].value
                raw = cached if formula else v
                fmt = cell.number_format if cell.number_format != "General" else None
                hid = (cell.row in hidden_rows) or (cell.column in hidden_cols) or None
                cells.append(SheetCell(ref=cell.coordinate, raw_value=_raw(raw),
                                       displayed_value=format_value(raw, cell.number_format),
                                       formula=formula, number_format=fmt, hidden=hid))
        if total > cap:
            warns.append(WarningItem(code="CELLS_TRUNCATED", message=f"Sheet {ws.title}: returned {cap} of {total} cells"))
        used = None
        if grid:
            used = f"A1:{get_column_letter(max(c for _, c in grid))}{max(r for r, _ in grid)}"
        sheets.append(Sheet(name=ws.title, hidden=ws.sheet_state != "visible", used_range=used,
                            merged_ranges=sorted(merged), probable_tables=probable_tables(grid), cells=cells))
    return SpreadsheetOutput(sheets=sheets, warnings=warns)
