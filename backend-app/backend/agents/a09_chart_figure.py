"""Agent 09 - Chart / Figure. Returns ChartBlock | FigureBlock.

The region is cropped from the page image and stored encrypted; crop_url is a backend-issued route
(/sources/{source_id}/crops/{crop_id}). Classification: layout label chart -> chart; label figure -> figure unless the OCR
text inside the region contains >= 4 numeric tokens (then chart). Chart series are NEVER fabricated: OCR text inside
the region (agent 04, preferred over any model reading) is passed as context to llm_guard, which may propose
{chart_type,title,series} only from numbers present in that OCR text; ungrounded values are dropped by the guard.
With no LLM provider configured the chart is returned with series=None and warning CHART_NOT_DERENDERED.
Confidence (documented): 0.55 base (pixel-derived charts are never exact) + 0.25 * grounding_score
(share of proposed numbers found in OCR text); 0.30 when series is null. Never 1.0.
insight_text: <=2 neutral sentences, generated via llm_guard and then verified so every number exists in the series
or OCR text; otherwise omitted with warning. Figures: caption = nearest caption region within 1.5x region height.
"""
import hashlib
import re
from typing import Optional, Union

from pydantic import BaseModel, ConfigDict, Field

from ..common import audit, config, llm_guard
from ..common.errors import AgentError, ErrorCode
from ..common.models import ChartBlock, Evidence, FigureBlock, Location, WarningItem
from ..common.store import store
from .a03_native_text import page_id
from .a05_layout import containment


class ChartFigureInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_id: str
    page_number: int = Field(ge=1)
    region_id: str


class _Point(BaseModel):
    x: Optional[str] = None
    y: float


class _Series(BaseModel):
    name: Optional[str] = None
    points: list[_Point]


class _ChartProposal(BaseModel):
    chart_type: Optional[str] = None
    title: Optional[str] = None
    series: Optional[list[_Series]] = None


class _Insight(BaseModel):
    insight_text: Optional[str] = None


def _crop(png: bytes, bbox: list[float]) -> bytes:
    import io
    from PIL import Image
    im = Image.open(io.BytesIO(png)).convert("RGB")
    out = io.BytesIO()
    im.crop(tuple(int(v) for v in bbox)).save(out, format="PNG")
    return out.getvalue()


def run(inp: ChartFigureInput) -> Union[ChartBlock, FigureBlock]:
    pid = page_id(inp.source_id, inp.page_number)
    lay = store.get("layout", pid)
    reg = next((r for r in (lay or {}).get("regions", []) if r["region_id"] == inp.region_id), None)
    if reg is None:
        raise AgentError(ErrorCode.NOT_FOUND, "Region not found (run layout first)")
    if reg["type"] not in ("chart", "figure"):
        raise AgentError(ErrorCode.INVALID_INPUT, "Region is not a chart or figure")
    bbox = reg["location"]["bbox"]
    loc = Location(bbox=bbox, page_width=reg["location"]["page_width"], page_height=reg["location"]["page_height"])
    png = store.page_image(inp.source_id, inp.page_number)
    crop = _crop(png, bbox)
    crop_id = hashlib.sha256(crop).hexdigest()[:16]
    store.put("crop", f"{inp.source_id}:{crop_id}", crop)
    crop_url = f"/sources/{inp.source_id}/crops/{crop_id}"
    block_id = hashlib.sha256(f"{inp.source_id}|{pid}|{inp.region_id}|chartfig".encode()).hexdigest()[:16]
    warns: list[WarningItem] = []

    ocr_text, ocr_blocks = "", []
    try:
        from . import a04_ocr
        res = a04_ocr.run(a04_ocr.OcrInput(source_id=inp.source_id, page_number=inp.page_number, region=bbox))
        ocr_blocks = [{"block_id": f"ocr{i}", "text": l.text} for i, l in enumerate(res.lines)]
        ocr_text = " ".join(l.text for l in res.lines)
    except AgentError:
        warns.append(WarningItem(code="OCR_UNAVAILABLE", message="No OCR text available for cross-checking"))
    n_numbers = len(llm_guard.numbers_in(ocr_text))
    is_chart = reg["type"] == "chart" or n_numbers >= 4

    if not is_chart:
        cap = None
        cap_candidates = []
        page_consensus = store.get("consensus", f"{inp.source_id}:{inp.page_number}") or {}
        for r in (lay or {}).get("regions", []):
            if r["type"] != "caption":
                continue
            cap_bbox = r["location"]["bbox"]
            vertical_gap = max(0.0, max(cap_bbox[1] - bbox[3], bbox[1] - cap_bbox[3]))
            horizontal_overlap = max(0.0, min(cap_bbox[2], bbox[2]) - max(cap_bbox[0], bbox[0]))
            if vertical_gap > 1.5 * max(bbox[3] - bbox[1], 1) or horizontal_overlap == 0:
                continue
            lines = [c for c in page_consensus.get("blocks", []) if containment(c["bbox"], cap_bbox) >= 0.5]
            lines.sort(key=lambda c: (c["bbox"][1], c["bbox"][0]))
            text = " ".join(c["winner"]["value"].strip() for c in lines if c["winner"]["value"].strip())
            if text:
                cap_candidates.append((vertical_gap, -horizontal_overlap, text))
        if cap_candidates:
            cap = min(cap_candidates)[2]
        block = FigureBlock(block_id=block_id, crop_url=crop_url, caption=cap, warnings=warns,
                            evidence=Evidence(source_id=inp.source_id, page_id=pid, location=loc,
                                              extraction_method="crop+layout", confidence=round(reg["confidence"], 4)))
    else:
        title = chart_type = series = insight = None
        conf = 0.30
        g = llm_guard.call("Extract chart_type, title and data series from the OCR text of a chart. "
                           "Only use numbers that appear in the context.", ocr_blocks, _ChartProposal)
        if g["ok"] and g["output"].get("series"):
            o = g["output"]
            chart_type, title, series = o.get("chart_type"), o.get("title"), o.get("series")
            conf = round(0.55 + 0.25 * g["grounding_report"]["score"], 4)
            ins = llm_guard.call("Write at most two neutral factual sentences describing the series. "
                                 "Do not compute anything.",
                                 ocr_blocks + [{"block_id": "series", "text": str(series)}], _Insight)
            if ins["ok"] and ins["output"].get("insight_text"):
                insight = ins["output"]["insight_text"]
                if len(re.findall(r"[.!?](\s|$)", insight)) > 2:
                    insight, _ = None, warns.append(WarningItem(code="INSIGHT_TOO_LONG", message="Insight dropped"))
            elif ins.get("warning"):
                warns.append(WarningItem(code=ins["warning"], message="Insight text omitted"))
        else:
            warns.append(WarningItem(code="CHART_NOT_DERENDERED",
                                     message="Series not extracted; no grounded chart model output available"))
        block = ChartBlock(block_id=block_id, crop_url=crop_url, chart_type=chart_type, title=title,
                           axes=None, series=series, insight_text=insight, warnings=warns,
                           evidence=Evidence(source_id=inp.source_id, page_id=pid, location=loc,
                                             extraction_method="ocr+llm_guard" if series else "crop", confidence=conf))
    store.put("chart_figure", block_id, block.model_dump(mode="json"))
    audit.append(event_type="chart_extracted", object_type="region", object_id=inp.region_id,
                 details={"kind": block.type})
    return block
