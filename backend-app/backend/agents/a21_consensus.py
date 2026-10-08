"""Agent 21 - Consensus / Parser Jury + coverage audit.

Candidates per text block come from stored native-text spans and every stored OCR engine result for the page.
Items are clustered by bbox IoU >= config.consensus.iou_align (greedy, deterministic order: extractor reliability then
bbox). Each candidate is normalised (NFKC, collapsed whitespace) before comparison.
Voting weight = engine_reliability[extractor] * candidate confidence. Candidates whose normalised text has
rapidfuzz ratio >= split_similarity*100 against each other belong to the same vote group.
 agreement = "unanimous" (one group holds all candidates)  | "majority" (top group weight > 50% of total weight)
           | "split" (otherwise). similarity = mean pairwise ratio/100 (1.0 for a single candidate).
 escalated = needs_review = (agreement == "split" and similarity < split_similarity). Single-candidate blocks are
 needs_review when their confidence is below bands.medium. No LLM tiebreak is invoked here (escalation only).
Coverage: page raster -> "ink" = gray < ink_threshold; covered = pixels inside any block bbox or layout
table/figure/chart/equation region; uncovered ink is grouped into 4-connected components on a 4x downsampled grid;
components below min_uncovered_area_px (original px) are ignored as noise. coverage_score = covered_ink / total_ink
(1.0 when the page has no ink).
"""
import hashlib
import io
import unicodedata
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field
from rapidfuzz import fuzz

from ..common import audit, config
from ..common.errors import AgentError, ErrorCode
from ..common.store import store
from .a03_native_text import page_id


class ConsensusInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_id: str
    page_number: Optional[int] = Field(default=None, ge=1)
    block_id: Optional[str] = None


class Candidate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    extractor: str
    value: str
    confidence: float


class ConsensusBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")
    block_id: str
    winner: Candidate
    agreement: str
    candidates: list[Candidate]
    escalated: bool
    needs_review: bool
    page_number: int
    bbox: list[float]
    similarity: float


class Coverage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    page_number: int
    coverage_score: float
    uncovered_regions: list[dict]


class ConsensusOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    blocks: list[ConsensusBlock]
    coverage: list[Coverage]


def norm(s: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", s).split())


def _iou(a, b) -> float:
    from .a05_layout import iou
    return iou(a, b)


def _items(source_id: str, pn: int) -> list[dict]:
    pid = page_id(source_id, pn)
    items = []
    nt = store.get("native_text", pid)
    if nt:
        for s in nt["spans"]:
            items.append({"extractor": "native_text", "text": s["text"], "bbox": s["location"]["bbox"], "conf": s["confidence"]})
    for eng in sorted(config.get("consensus.engine_reliability", {})):
        o = store.get("ocr", f"{pid}:{eng}")
        if o:
            for l in o["lines"]:
                items.append({"extractor": eng, "text": l["text"], "bbox": l["location"]["bbox"], "conf": l["confidence"]})
    return items


def _vote(cluster: list[dict]) -> tuple[Candidate, str, float, list[Candidate]]:
    rel = config.get("consensus.engine_reliability", {})
    thr = config.get("consensus.split_similarity", 0.85) * 100
    cands = [Candidate(extractor=i["extractor"], value=i["text"], confidence=round(i["conf"], 4)) for i in cluster]
    groups: list[list[int]] = []
    for idx, c in enumerate(cands):
        for g in groups:
            if fuzz.ratio(norm(cands[g[0]].value), norm(c.value)) >= thr:
                g.append(idx)
                break
        else:
            groups.append([idx])
    w = [rel.get(c.extractor, 0.5) * max(c.confidence, 1e-6) for c in cands]
    total = sum(w)
    best = max(groups, key=lambda g: (sum(w[i] for i in g), -min(g)))
    winner = cands[max(best, key=lambda i: (w[i], -i))]
    if len(groups) == 1:
        agreement = "unanimous"
    elif sum(w[i] for i in best) > total / 2:
        agreement = "majority"
    else:
        agreement = "split"
    if len(cands) == 1:
        sim = 1.0
    else:
        pairs = [(i, j) for i in range(len(cands)) for j in range(i + 1, len(cands))]
        sim = sum(fuzz.ratio(norm(cands[i].value), norm(cands[j].value)) for i, j in pairs) / len(pairs) / 100
    return winner, agreement, sim, cands


def _coverage(source_id: str, pn: int, boxes: list[list[float]]) -> Coverage:
    import numpy as np
    from PIL import Image
    png = store.page_image(source_id, pn)
    gray = np.asarray(Image.open(io.BytesIO(png)).convert("L"))
    ink = gray < config.get("consensus.ink_threshold", 200)
    H, W = ink.shape
    mask = np.zeros_like(ink)
    lay = store.get("layout", page_id(source_id, pn)) or {}
    allb = list(boxes) + [r["location"]["bbox"] for r in lay.get("regions", []) if r["type"] in ("table", "figure", "chart", "equation")]
    for b in allb:
        x1, y1, x2, y2 = (int(max(0, b[0])), int(max(0, b[1])), int(min(W, b[2] + 1)), int(min(H, b[3] + 1)))
        mask[y1:y2, x1:x2] = True
    total = int(ink.sum())
    if total == 0:
        return Coverage(page_number=pn, coverage_score=1.0, uncovered_regions=[])
    covered = int((ink & mask).sum())
    unc = ink & ~mask
    f = 4
    h4, w4 = (H + f - 1) // f, (W + f - 1) // f
    pad = np.zeros((h4 * f, w4 * f), dtype=bool)
    pad[:H, :W] = unc
    small = pad.reshape(h4, f, w4, f).any(axis=(1, 3))
    seen = np.zeros_like(small)
    regions = []
    min_area = config.get("consensus.min_uncovered_area_px", 150)
    for y, x in zip(*np.nonzero(small)):
        if seen[y, x]:
            continue
        stack, cells = [(y, x)], []
        seen[y, x] = True
        while stack:
            cy, cx = stack.pop()
            cells.append((cy, cx))
            for ny, nx in ((cy + 1, cx), (cy - 1, cx), (cy, cx + 1), (cy, cx - 1)):
                if 0 <= ny < h4 and 0 <= nx < w4 and small[ny, nx] and not seen[ny, nx]:
                    seen[ny, nx] = True
                    stack.append((ny, nx))
        ys, xs = [c[0] for c in cells], [c[1] for c in cells]
        bb = [float(min(xs) * f), float(min(ys) * f), float((max(xs) + 1) * f), float((max(ys) + 1) * f)]
        area = int(unc[int(bb[1]):int(bb[3]), int(bb[0]):int(bb[2])].sum())
        if area >= min_area:
            regions.append({
                "bbox": bb,
                "coordinate_system": "pixel_top_left",
                "page_width": W,
                "page_height": H,
                "ink_pixels": area,
            })
    regions.sort(key=lambda r: (r["bbox"][1], r["bbox"][0]))
    return Coverage(page_number=pn, coverage_score=round(covered / total, 4), uncovered_regions=regions)


def run(inp: ConsensusInput) -> ConsensusOutput:
    meta = store.meta(inp.source_id)
    router = store.get("router", inp.source_id)
    if inp.page_number:
        pages = [inp.page_number]
    elif router:
        pages = [u["page_number"] for u in router["units"]]
    else:
        raise AgentError(ErrorCode.CONFLICT, "Run format-router first", {"missing": ["router"]})
    if meta["detected_mime"] != "application/pdf" and not meta["detected_mime"].startswith("image/"):
        raise AgentError(ErrorCode.UNSUPPORTED_FORMAT, "Consensus applies to paged/raster sources")
    blocks, cov = [], []
    rel = config.get("consensus.engine_reliability", {})
    for pn in pages:
        items = sorted(_items(inp.source_id, pn), key=lambda i: (-rel.get(i["extractor"], 0.5), i["bbox"]))
        clusters: list[list[dict]] = []
        for it in items:
            best, bi = 0.0, -1
            for ci, cl in enumerate(clusters):
                if any(c["extractor"] == it["extractor"] for c in cl):
                    continue
                v = max(_iou(it["bbox"], c["bbox"]) for c in cl)
                if v > best:
                    best, bi = v, ci
            if bi >= 0 and best >= config.get("consensus.iou_align", 0.3):
                clusters[bi].append(it)
            else:
                clusters.append([it])
        page_boxes = []
        for cl in sorted(clusters, key=lambda c: (c[0]["bbox"][1], c[0]["bbox"][0])):
            bb = [min(i["bbox"][0] for i in cl), min(i["bbox"][1] for i in cl), max(i["bbox"][2] for i in cl), max(i["bbox"][3] for i in cl)]
            page_boxes.append(bb)
            winner, agreement, sim, cands = _vote(cl)
            esc = agreement == "split" and sim < config.get("consensus.split_similarity", 0.85)
            nr = esc or (len(cands) == 1 and winner.confidence < config.get("bands.medium", 0.6))
            bid = hashlib.sha256(f"{inp.source_id}|{pn}|{[round(v) for v in bb]}|text".encode()).hexdigest()[:16]
            if inp.block_id and inp.block_id != bid:
                continue
            blocks.append(ConsensusBlock(block_id=bid, winner=winner, agreement=agreement, candidates=cands,
                                         escalated=esc, needs_review=nr, page_number=pn, bbox=bb, similarity=round(sim, 4)))
        try:
            cov.append(_coverage(inp.source_id, pn, page_boxes))
        except AgentError:
            raise
    out = ConsensusOutput(blocks=blocks, coverage=cov)
    for pn in pages:
        store.put("consensus", f"{inp.source_id}:{pn}", {
            "blocks": [b.model_dump(mode="json") for b in blocks if b.page_number == pn],
            "coverage": next(c.model_dump(mode="json") for c in cov if c.page_number == pn)})
    audit.append(event_type="consensus_run", object_type="source", object_id=inp.source_id,
                 details={"pages": len(pages), "blocks": len(blocks), "escalated": sum(b.escalated for b in blocks)})
    return out
