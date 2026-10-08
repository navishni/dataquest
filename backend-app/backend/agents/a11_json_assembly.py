"""Agent 11 - JSON Assembly (SourceDocument). Deterministic; never re-runs extractors.

Reads stored outputs (router, layout, reading order, consensus, table/chart/equation blocks). If anything required is
missing it raises CONFLICT with the exact list. Per page: each layout region becomes a block (text-like regions get the
consensus winners whose bbox is >= config.assembly.containment_threshold inside the region, joined top-to-bottom;
table/chart/figure/equation regions embed the stored block under `data`). Consensus blocks that fall in no region become
text blocks with an ORPHAN_TEXT warning. reading_order_index comes from agent 06 (orphans follow, sorted y,x).
Unknown fields from upstream blocks are preserved under `extra`. The result is validated against the SourceDocument
model (duplicate block ids rejected) before saving; content_hash = SHA-256 of canonical JSON of the document.
A Markdown rendering is stored under kind "markdown": titles -> headings, tables -> GFM, equations -> $$..$$, figures ->
image references with captions.
"""
import copy
import hashlib
from typing import Optional

from pydantic import BaseModel, ConfigDict

from ..common import audit, config, crypto
from ..common.errors import AgentError, ErrorCode
from ..common.models import Block, Evidence, Location, PageOut, SourceDocument, WarningItem
from ..common.store import store
from .a03_native_text import page_id
from .a05_layout import containment


class AssemblyInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_id: str


def _same_bbox(a, b) -> bool:
    return a is not None and b is not None and all(abs(x - y) < 1.0 for x, y in zip(a, b))


def _normalise_text(value: str) -> str:
    return " ".join((value or "").casefold().split())


def _covered_by_table_cell(consensus_block: dict, table: dict) -> bool:
    """Treat text as represented only when a structured cell contains both its text and location."""
    text = _normalise_text(consensus_block["winner"]["value"])
    if not text:
        return False
    for cell in table.get("cells", []):
        location = cell.get("location") or {}
        cell_bbox = location.get("bbox")
        if not cell_bbox or containment(consensus_block["bbox"], cell_bbox) < 0.35:
            continue
        candidates = [cell.get("raw_text", ""), *(cell.get("alternatives") or [])]
        if any(text in _normalise_text(candidate) for candidate in candidates):
            return True
    return False


def _md_table(t: dict) -> str:
    grid: dict = {}
    for c in t["cells"]:
        grid[(c["row"], c["col"])] = c["raw_text"].replace("|", "\\|")
    rows = [[grid.get((r, c), "") for c in range(t["n_cols"])] for r in range(t["n_rows"])]
    if not rows:
        return ""
    lines = ["| " + " | ".join(rows[0]) + " |", "|" + "---|" * t["n_cols"]]
    lines += ["| " + " | ".join(r) + " |" for r in rows[1:]]
    return "\n".join(lines)


def _row_signature(table: dict, row_number: int) -> tuple:
    return tuple((c.get("col"), c.get("raw_text", ""), c.get("row_span", 1), c.get("col_span", 1))
                 for c in sorted((x for x in table.get("cells", []) if x.get("row") == row_number),
                                 key=lambda x: x.get("col", 0)))


def _repeated_header_rows(previous: dict, current: dict) -> int:
    """Find repeated leading rows when two linked table fragments meet at a page break."""
    count = 0
    for row in range(min(3, previous.get("n_rows", 0), current.get("n_rows", 0))):
        left, right = _row_signature(previous, row), _row_signature(current, row)
        if not left or left != right:
            break
        count += 1
    return count


def _merge_table_chain(table: dict) -> dict:
    """Join linked page fragments into one table while recording cell source pages/rows."""
    merged = copy.deepcopy(table)
    merged_cells = merged.get("cells", [])
    current = table
    visited = {table.get("block_id")}
    source_pages = [int(table.get("page_number", 1))]
    continuations = []
    segments = [{"block_id": table.get("block_id"), "page_number": int(table.get("page_number", 1))}]
    for cell in merged_cells:
        cell["source_page_number"] = int(table.get("page_number", 1))
        cell["source_row"] = int(cell.get("row", 0))

    while current.get("continues_to"):
        next_id = current["continues_to"]
        if next_id in visited:
            break
        following = store.get("table", next_id)
        if not following:
            break
        visited.add(next_id)
        overlap = _repeated_header_rows(current, following)
        row_offset = int(merged.get("n_rows", 0)) - overlap
        page_number = int(following.get("page_number", 1))
        for cell in following.get("cells", []):
            source_row = int(cell.get("row", 0))
            if source_row < overlap:
                continue
            copied = copy.deepcopy(cell)
            copied["source_page_number"] = page_number
            copied["source_row"] = source_row
            copied["row"] = row_offset + source_row
            merged_cells.append(copied)
        merged["n_rows"] = row_offset + int(following.get("n_rows", 0))
        source_pages.append(page_number)
        segments.append({"block_id": following.get("block_id"), "page_number": page_number})
        continuations.append({"block_id": following.get("block_id"), "page_number": page_number,
                              "continued_rows": int(following.get("n_rows", 0)) - overlap})
        merged["badges"] = (merged.get("badges") or []) + (following.get("badges") or [])
        merged["warnings"] = (merged.get("warnings") or []) + (following.get("warnings") or [])
        current = following

    merged["cells"] = merged_cells
    merged["continues_to"] = None
    merged["source_pages"] = source_pages
    merged["source_segments"] = segments
    merged["continuations"] = continuations
    if len(source_pages) > 1:
        merged["caption"] = f"Continued across pages {source_pages[0]}–{source_pages[-1]}"
    return merged


def run(inp: AssemblyInput) -> SourceDocument:
    meta = store.meta(inp.source_id)
    router = store.get("router", inp.source_id)
    missing: list[str] = []
    if router is None:
        raise AgentError(ErrorCode.CONFLICT, "Missing upstream outputs", {"missing": ["router"]})
    thr = config.get("assembly.containment_threshold", 0.5)
    pages_out, warnings, md, cov_scores, uncovered = [], [], [], [], []
    plans = []
    for u in router["units"]:
        pn, pid = u["page_number"], page_id(inp.source_id, u["page_number"])
        if u["page_class"] == "blank":
            plans.append((pn, pid, None, None, None))
            continue
        lay, ro, cons = store.get("layout", pid), store.get("reading_order", pid), store.get("consensus", f"{inp.source_id}:{pn}")
        for name, v in (("layout", lay), ("reading_order", ro), ("consensus", cons)):
            if v is None:
                missing.append(f"{name}:page{pn}")
        if lay:
            tabs = [store.get("table", i) for i in store.get("table_index", f"{inp.source_id}:{pn}", [])]
            cf = [b for b in store.list("chart_figure") if b["evidence"]["page_id"] == pid]
            eqs = [b for b in store.list("equation") if b["evidence"]["page_id"] == pid]
            for r in lay["regions"]:
                pool = {"table": tabs, "chart": cf, "figure": cf, "equation": eqs}.get(r["type"])
                if pool is not None and not any(_same_bbox(r["location"]["bbox"], b["evidence"]["location"]["bbox"]) for b in pool):
                    missing.append(f"{r['type']}:{r['region_id']}")
        plans.append((pn, pid, lay, ro, cons))
    if missing:
        raise AgentError(ErrorCode.CONFLICT, "Missing upstream outputs", {"missing": sorted(set(missing))})

    all_ids = set()
    for pn, pid, lay, ro, cons in plans:
        page = PageOut(page_id=pid, page_number=pn, image_url=f"/sources/{inp.source_id}/pages/{pn}/image")
        if lay is None:
            pages_out.append(page)
            warnings.append(WarningItem(code="BLANK_PAGE", message="Page has no blocks", page_number=pn))
            continue
        warnings.extend(WarningItem.model_validate(w) for w in lay.get("warnings", []))
        warnings.extend(WarningItem.model_validate(w) for w in ro.get("warnings", []))
        if lay["regions"]:
            pw, ph = lay["regions"][0]["location"]["page_width"], lay["regions"][0]["location"]["page_height"]
        else:
            import io as _io
            from PIL import Image as _Image
            pw, ph = _Image.open(_io.BytesIO(store.page_image(inp.source_id, pn))).size
        order = {rid: i for i, rid in enumerate(ro["ordered_ids"])}
        cblocks = cons["blocks"]
        used = set()
        blocks: list[Block] = []
        md_page = []
        for r in lay["regions"]:
            rb, loc = r["location"]["bbox"], r["location"]
            ev_loc = Location(bbox=rb, page_width=loc["page_width"], page_height=loc["page_height"])
            data, text, method, conf, extra = None, None, "layout", r["confidence"], {}
            block_type = r["type"]
            if r["type"] in ("table", "chart", "figure", "equation"):
                pool = (store.list("table") if r["type"] == "table" else store.list("chart_figure") if r["type"] != "equation" else store.list("equation"))
                b = next(x for x in pool if x["evidence"]["page_id"] == pid and _same_bbox(rb, x["evidence"]["location"]["bbox"]))
                method, conf = b["evidence"]["extraction_method"], b["evidence"]["confidence"]
                if r["type"] == "table":
                    if b.get("continues_from"):
                        used |= {c["block_id"] for c in cblocks if _covered_by_table_cell(c, b)}
                        parent = store.get("table", b["continues_from"]) or {}
                        data = {"continuation_of": b["continues_from"],
                                "continued_from_page": parent.get("page_number"),
                                "page_number": pn, "continued_rows": b.get("n_rows", 0)}
                        text = f"Continuation of table from page {parent.get('page_number', pn - 1)}"
                        extra = {"continuation_only": True}
                        block_type = "table_continuation"
                    else:
                        merged_table = _merge_table_chain(b)
                        data = {k: v for k, v in merged_table.items() if k != "evidence"}
                        used |= {c["block_id"] for c in cblocks if _covered_by_table_cell(c, merged_table)}
                        md_page.append(_md_table(merged_table))
                        block_type = r["type"]
                elif r["type"] == "equation":
                    data = {k: v for k, v in b.items() if k not in ("evidence",)}
                    md_page.append(f"$$\n{b.get('latex') or ''}\n$$")
                else:
                    data = {k: v for k, v in b.items() if k not in ("evidence",)}
                    md_page.append(f"![{b.get('caption') or r['type']}]({b['crop_url']})")
                    block_type = r["type"]
            else:
                mine = [c for c in cblocks if containment(c["bbox"], rb) >= thr]
                mine.sort(key=lambda c: (round(c["bbox"][1] / 5), c["bbox"][0]))
                used |= {c["block_id"] for c in mine}
                text = "\n".join(c["winner"]["value"] for c in mine) or None
                if mine:
                    conf = sum(c["winner"]["confidence"] for c in mine) / len(mine)
                    method = "consensus"
                    extra = {"agreement": [c["agreement"] for c in mine], "needs_review": any(c["needs_review"] for c in mine)}
                if text:
                    md_page.append(("# " + text) if r["type"] == "title" else text)
            blocks.append(Block(block_id=r["region_id"], type=block_type, page_number=pn,
                                reading_order_index=order.get(r["region_id"], 10**6), text=text, data=data,
                                evidence=Evidence(source_id=inp.source_id, page_id=pid, location=ev_loc,
                                                  extraction_method=method, confidence=round(min(max(conf, 0), 1), 4)),
                                extra=extra))
        for c in cblocks:
            if c["block_id"] in used:
                continue
            warnings.append(WarningItem(code="ORPHAN_TEXT", message="Text could not be mapped to a layout block or structured table cell",
                                        page_number=pn, block_id=c["block_id"]))
            blocks.append(Block(block_id=c["block_id"], type="text", page_number=pn, reading_order_index=10**6,
                                text=c["winner"]["value"],
                                evidence=Evidence(source_id=inp.source_id, page_id=pid,
                                                  location=Location(bbox=c["bbox"], page_width=pw, page_height=ph),
                                                  extraction_method="consensus", confidence=c["winner"]["confidence"]),
                                extra={"orphan": True}))
            md_page.append(c["winner"]["value"])
        blocks.sort(key=lambda b: (b.reading_order_index, b.evidence.location.bbox[1], b.evidence.location.bbox[0]))
        for i, b in enumerate(blocks):
            b.reading_order_index = i
        page.blocks, page.width, page.height = blocks, pw, ph
        pages_out.append(page)
        md.append(f"<!-- page {pn} -->\n" + "\n\n".join(x for x in md_page if x))
        cv = cons["coverage"]
        cov_scores.append(cv["coverage_score"])
        uncovered += [{"page_number": pn, **u} for u in cv["uncovered_regions"]]

    ids = [b.block_id for p in pages_out for b in p.blocks]
    if len(ids) != len(set(ids)):
        raise AgentError(ErrorCode.ENGINE_FAILED, "Assembly produced duplicate block ids")
    vidx = {}
    for p in pages_out:
        v = store.get("virtual_index", f"{inp.source_id}:{p.page_number}")
        for b in p.blocks:
            b.virtual_page_number = v["virtual_page_number"] if v else None
    status = "complete" if all(s >= 0.9 for s in cov_scores) and not any(w.code == "ORPHAN_TEXT" for w in warnings) else "partial"
    doc = SourceDocument(source_id=inp.source_id, status=status, warnings=warnings, pages=pages_out,
                         coverage_score=round(sum(cov_scores) / len(cov_scores), 4) if cov_scores else None,
                         uncovered_regions=uncovered)
    d = doc.model_dump(mode="json")
    d.pop("content_hash")
    doc.content_hash = crypto.hash_obj(d)
    SourceDocument.model_validate(doc.model_dump(mode="json"))  # fail loudly if invalid
    store.put("document", inp.source_id, doc.model_dump(mode="json"))
    store.put("markdown", inp.source_id, "\n\n".join(md))
    audit.append(event_type="json_assembled", object_type="source", object_id=inp.source_id,
                 details={"pages": len(pages_out), "blocks": len(ids), "content_hash": doc.content_hash})
    return doc
