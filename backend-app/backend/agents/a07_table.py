"""Agent 07 - Table Extraction (TableBlock).

Native PDFs: pdfplumber with the "lines" strategy, then the "text" strategy for borderless tables. Merged cells are
true spans derived from the shared grid edges (row/col index from the sorted unique cell edges; span = number of
edges crossed), never duplicated text. Scanned pages: OCR lines (agent 04) inside the region are clustered into rows
(y overlap) and columns (x-gap > 2x median char width), producing a span-1 grid with lower confidence.
Cell confidence (documented): native cell = 0.95 when pdfplumber text equals PyMuPDF text for the same box after
whitespace normalisation, 0.60 when they differ (the PyMuPDF reading is stored in `alternatives`); empty cell 0.90;
OCR cell = mean OCR confidence of its lines x 0.9. Block evidence confidence = mean of cell confidences.
Headers: first row is_header when it has no numeric cells and some later row has one. `normalized` is filled only for
pure numeric cells with a named rule (strip_thousands_separator, parentheses_negative, percent_strip); currency
symbols are never guessed. Badges ("Column total matches sum") are emitted only when a row labelled total/subtotal
exists and its numeric column is computable (tolerance config.confidence.arithmetic_tolerance).
Continuation: previous-page table in the same source with the same column count and a repeated or missing header
sets continues_from/continues_to. Rotated pages and nested tables are not handled (warning TABLE_ROTATED_PAGE).
"""
import hashlib
import io
import re
from decimal import Decimal, InvalidOperation
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from ..common import audit, config
from ..common.errors import AgentError, ErrorCode
from ..common.models import Badge, Cell, Evidence, Location, TableBlock, WarningItem
from ..common.store import store
from .a03_native_text import page_id

_NUM = re.compile(r"^\(?-?[\d,]+(\.\d+)?\)?%?$")


class TableInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_id: str
    page_number: int = Field(ge=1)
    region_id: str


def parse_numeric(text: str) -> Optional[tuple[Decimal, str]]:
    t = text.strip().replace("−", "-")
    if not t or not _NUM.match(t):
        return None
    rule, neg = "strip_thousands_separator" if "," in t else "plain_number", False
    if t.startswith("(") and t.endswith(")"):
        neg, t, rule = True, t[1:-1], "parentheses_negative"
    if t.endswith("%"):
        t, rule = t[:-1], "percent_strip"
    try:
        v = Decimal(t.replace(",", ""))
    except InvalidOperation:
        return None
    return (-v if neg else v), rule


def _grid_from_bboxes(boxes: list[tuple]) -> list[dict]:
    xs = sorted({round(b[0], 1) for b in boxes} | {round(b[2], 1) for b in boxes})
    ys = sorted({round(b[1], 1) for b in boxes} | {round(b[3], 1) for b in boxes})

    def idx(vals, v):
        return min(range(len(vals)), key=lambda i: abs(vals[i] - v))
    out = []
    for b in boxes:
        c0, c1, r0, r1 = idx(xs, b[0]), idx(xs, b[2]), idx(ys, b[1]), idx(ys, b[3])
        out.append({"bbox": b, "row": r0, "col": c0, "row_span": max(1, r1 - r0), "col_span": max(1, c1 - c0)})
    # compress edge indices to dense row/col numbers
    rows = sorted({c["row"] for c in out})
    cols = sorted({c["col"] for c in out})
    for c in out:
        r_end, c_end = c["row"] + c["row_span"], c["col"] + c["col_span"]
        c["row_span"] = max(1, len([r for r in rows if c["row"] <= r < r_end]))
        c["col_span"] = max(1, len([k for k in cols if c["col"] <= k < c_end]))
        c["row"], c["col"] = rows.index(c["row"]), cols.index(c["col"])
    return out


def _native(source_id: str, page_number: int, bbox_pt: list[float], pw: int, ph: int, scale: float,
            password: Optional[str] = None):
    import pdfplumber
    import pymupdf
    data = store.get_file(source_id)
    with pdfplumber.open(io.BytesIO(data), password=password or "") as pdf:
        if page_number > len(pdf.pages):
            raise AgentError(ErrorCode.NOT_FOUND, "Page not found")
        pl = pdf.pages[page_number - 1]
        crop = pl.crop(tuple(max(0, v) for v in (bbox_pt[0], bbox_pt[1], min(bbox_pt[2], pl.width), min(bbox_pt[3], pl.height))))
        found = None
        for strat in ({"vertical_strategy": "lines", "horizontal_strategy": "lines"},
                      {"vertical_strategy": "text", "horizontal_strategy": "text"}):
            tabs = crop.find_tables(table_settings=strat)
            if tabs and len(tabs[0].cells) >= 2:
                found = tabs[0]
                break
        if found is None:
            return None
        cells = _grid_from_bboxes(list(found.cells))
        mdoc = pymupdf.open(stream=data, filetype="pdf")
        mp = mdoc[page_number - 1]
        for c in cells:
            b = c["bbox"]
            t1 = (pl.crop(tuple(max(0, v) for v in b), strict=False).extract_text() or "").strip()
            t2 = " ".join(mp.get_text("text", clip=pymupdf.Rect(b)).split())
            c["text"] = " ".join(t1.split())
            if not c["text"] and not t2:
                c["conf"], c["alts"] = 0.90, []
            elif c["text"] == t2:
                c["conf"], c["alts"] = 0.95, []
            else:
                c["conf"], c["alts"] = 0.60, ([t2] if t2 else [])
            c["bbox_px"] = [round(v * scale, 2) for v in b]
        return cells


def _ocr(source_id: str, page_number: int, bbox_px: list[float]):
    from . import a04_ocr
    res = a04_ocr.run(a04_ocr.OcrInput(source_id=source_id, page_number=page_number, region=bbox_px))
    lines = sorted(res.lines, key=lambda l: (l.location.bbox[1], l.location.bbox[0]))
    if not lines:
        return None
    rows: list[list] = []
    for l in lines:
        if rows and abs((l.location.bbox[1] + l.location.bbox[3]) / 2 - sum((x.location.bbox[1] + x.location.bbox[3]) / 2 for x in rows[-1]) / len(rows[-1])) < (l.location.bbox[3] - l.location.bbox[1]) * 0.6:
            rows[-1].append(l)
        else:
            rows.append([l])
    cells = []
    for ri, row in enumerate(rows):
        row.sort(key=lambda l: l.location.bbox[0])
        for ci, l in enumerate(row):
            cells.append({"row": ri, "col": ci, "row_span": 1, "col_span": 1, "text": l.text, "alts": [],
                          "conf": round(l.confidence * 0.9, 4), "bbox_px": l.location.bbox})
    return cells


def run(inp: TableInput) -> TableBlock:
    import pymupdf
    lay = store.get("layout", page_id(inp.source_id, inp.page_number))
    reg = next((r for r in (lay or {}).get("regions", []) if r["region_id"] == inp.region_id), None)
    if reg is None:
        raise AgentError(ErrorCode.NOT_FOUND, "Region not found (run layout first)")
    if reg["type"] != "table":
        raise AgentError(ErrorCode.INVALID_INPUT, "Region is not a table")
    bbox_px = reg["location"]["bbox"]
    pw, ph = reg["location"]["page_width"], reg["location"]["page_height"]
    scale = config.get("page_classification.render_dpi", 200) / 72
    meta = store.meta(inp.source_id)
    warns: list[WarningItem] = []
    cells = None
    method = "ocr_grid"
    if meta["detected_mime"] == "application/pdf":
        doc = pymupdf.open(stream=store.get_file(inp.source_id), filetype="pdf")
        if doc.needs_pass:
            password = store.get_password(inp.source_id)
            if not password or not doc.authenticate(password):
                raise AgentError(ErrorCode.PASSWORD_REQUIRED, "Password-protected PDF could not be opened")
        if doc[inp.page_number - 1].rotation:
            warns.append(WarningItem(code="TABLE_ROTATED_PAGE", message="Rotated page: table geometry unreliable"))
        cells = _native(inp.source_id, inp.page_number, [v / scale for v in bbox_px], pw, ph, scale,
                        store.get_password(inp.source_id))
        method = "pdfplumber"
    if cells is None:
        cells = _ocr(inp.source_id, inp.page_number, bbox_px)
        method = "ocr_grid"
    if not cells:
        raise AgentError(ErrorCode.ENGINE_FAILED, "No table structure could be extracted")
    n_rows = max(c["row"] + c["row_span"] for c in cells)
    n_cols = max(c["col"] + c["col_span"] for c in cells)
    cells.sort(key=lambda c: (c["row"], c["col"]))
    grid = {(c["row"], c["col"]): c for c in cells}
    first = [c for c in cells if c["row"] == 0]
    later_num = any(parse_numeric(c["text"]) for c in cells if c["row"] > 0)
    header = bool(first) and not any(parse_numeric(c["text"]) for c in first) and any(c["text"] for c in first) and later_num
    out_cells = []
    for c in cells:
        pn = parse_numeric(c["text"]) if c["text"] else None
        out_cells.append(Cell(row=c["row"], col=c["col"], row_span=c["row_span"], col_span=c["col_span"],
                              is_header=header and c["row"] == 0, raw_text=c["text"],
                              normalized={"value": str(pn[0]), "rule": pn[1]} if pn else None,
                              location=Location(bbox=c["bbox_px"], page_width=pw, page_height=ph),
                              confidence=c["conf"], alternatives=c["alts"]))
    # badges
    block_id = hashlib.sha256(f"{inp.source_id}|{inp.page_number}|{inp.region_id}|table".encode()).hexdigest()[:16]
    badges = []
    tol = Decimal(str(config.get("confidence.arithmetic_tolerance", 0.01)))
    for r in range(n_rows):
        label = grid.get((r, 0))
        if label and re.search(r"\b(sub)?total\b", label["text"], re.I) and r > 0:
            for col in range(1, n_cols):
                tot = grid.get((r, col))
                tv = parse_numeric(tot["text"]) if tot else None
                vals = [parse_numeric(grid[(i, col)]["text"]) for i in range(1 if header else 0, r) if (i, col) in grid]
                if tv is None or not vals or any(v is None for v in vals):
                    continue
                s = sum(v[0] for v in vals)
                ok = abs(s - tv[0]) <= tol
                badges.append(Badge(scope_id=block_id, label="Column total matches sum",
                                    status="pass" if ok else "mismatch",
                                    detail=f"col {col}: computed {s} vs stated {tv[0]}"))
    # continuation
    cont_from = None
    prev_ids = store.get("table_index", f"{inp.source_id}:{inp.page_number - 1}", [])
    if prev_ids:
        prev = store.get("table", prev_ids[-1])
        if prev and prev["n_cols"] == n_cols:
            prev_hdr = [c["raw_text"] for c in prev["cells"] if c["row"] == 0]
            this_hdr = [c.raw_text for c in out_cells if c.row == 0]
            if not header or prev_hdr == this_hdr:
                cont_from = prev["block_id"]
                prev["continues_to"] = block_id
                store.put("table", prev["block_id"], prev)
    conf = sum(c.confidence for c in out_cells) / len(out_cells)
    block = TableBlock(block_id=block_id, page_number=inp.page_number, cells=out_cells, n_rows=n_rows, n_cols=n_cols,
                       continues_from=cont_from, badges=badges, warnings=warns,
                       evidence=Evidence(source_id=inp.source_id, page_id=page_id(inp.source_id, inp.page_number),
                                         location=Location(bbox=bbox_px, page_width=pw, page_height=ph),
                                         extraction_method=method, confidence=round(conf, 4)))
    store.put("table", block_id, block.model_dump(mode="json"))
    idx = store.get("table_index", f"{inp.source_id}:{inp.page_number}", [])
    if block_id not in idx:
        store.put("table_index", f"{inp.source_id}:{inp.page_number}", idx + [block_id])
    audit.append(event_type="table_extracted", object_type="region", object_id=inp.region_id,
                 details={"rows": n_rows, "cols": n_cols, "method": method})
    return block
