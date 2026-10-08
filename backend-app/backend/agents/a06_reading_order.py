"""Agent 06 - Reading Order. Deterministic.

Recursive XY-cut: try a horizontal cut (empty y-band) first, else a vertical cut (empty x-band wider than
config.layout.column_gap_ratio * page_width), else sort by (y, x). Headers are placed first and footers last;
a caption is placed immediately after the nearest figure/table above or below it. Floating sidebars (narrow text
regions beside a wide one, overlapping in y) are flagged SIDEBAR. Output is asserted to be a permutation of
region_ids.
Confidence = pairwise agreement (1 - inversions/pairs) between the XY-cut order and a second heuristic
(columns by x-center clustering, then top-to-bottom); x0.7 and AMBIGUOUS_COLUMNS when a vertical gap is within
50% of the threshold. 1.0 only for <=1 region.
"""
from typing import Optional

from pydantic import BaseModel, ConfigDict

from ..common import audit, config
from ..common.errors import AgentError, ErrorCode
from ..common.models import WarningItem
from ..common.store import store


class ReadingOrderInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    page_id: str
    region_ids: list[str]


class ReadingOrderOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ordered_ids: list[str]
    reading_order_confidence: float
    warnings: list[WarningItem] = []


def _xy_cut(items: list[dict], min_gap_x: float, state: dict) -> list[dict]:
    if len(items) <= 1:
        return items
    # horizontal cut
    for axis, lo, hi, gap in (("x", 0, 2, min_gap_x), ("y", 1, 3, 1.0)):
        s = sorted(items, key=lambda r: r["bbox"][lo])
        reach, cut = s[0]["bbox"][hi], None
        for i in range(1, len(s)):
            g = s[i]["bbox"][lo] - reach
            if g > gap:
                cut = i
                if axis == "x" and g < 1.5 * gap:
                    state["ambiguous"] = True
                break
            reach = max(reach, s[i]["bbox"][hi])
        if cut:
            return _xy_cut(s[:cut], min_gap_x, state) + _xy_cut(s[cut:], min_gap_x, state)
        if axis == "x" and len(items) > 1:
            # near-miss gaps -> ambiguous
            near = [s[i]["bbox"][lo] - max(t["bbox"][hi] for t in s[:i]) for i in range(1, len(s))]
            if near and max(near) > 0.2 * gap:
                state["ambiguous"] = True
    return sorted(items, key=lambda r: (r["bbox"][1], r["bbox"][0]))


def _second_order(items: list[dict], page_w: float) -> list[str]:
    cols: list[list[dict]] = []
    for r in sorted(items, key=lambda r: (r["bbox"][0] + r["bbox"][2]) / 2):
        c = (r["bbox"][0] + r["bbox"][2]) / 2
        wide = (r["bbox"][2] - r["bbox"][0]) > 0.6 * page_w
        if wide:
            cols.append([r])
            continue
        if cols and not ((cols[-1][0]["bbox"][2] - cols[-1][0]["bbox"][0]) > 0.6 * page_w) and \
                abs(c - (cols[-1][0]["bbox"][0] + cols[-1][0]["bbox"][2]) / 2) < 0.15 * page_w:
            cols[-1].append(r)
        else:
            cols.append([r])
    return [r["region_id"] for col in cols for r in sorted(col, key=lambda r: r["bbox"][1])]


def _agreement(a: list[str], b: list[str]) -> float:
    pos = {k: i for i, k in enumerate(b)}
    n = len(a)
    if n < 2:
        return 1.0
    inv = sum(1 for i in range(n) for j in range(i + 1, n) if pos[a[i]] > pos[a[j]])
    return 1 - inv / (n * (n - 1) / 2)


def run(inp: ReadingOrderInput) -> ReadingOrderOutput:
    if len(set(inp.region_ids)) != len(inp.region_ids):
        raise AgentError(ErrorCode.INVALID_INPUT, "Duplicate region ids")
    layout = store.get("layout", inp.page_id)
    if layout is None:
        raise AgentError(ErrorCode.NOT_FOUND, "Layout for page not found")
    by_id = {r["region_id"]: {"region_id": r["region_id"], "type": r["type"], "bbox": r["location"]["bbox"],
                              "pw": r["location"]["page_width"]} for r in layout["regions"]}
    missing = [i for i in inp.region_ids if i not in by_id]
    if missing:
        raise AgentError(ErrorCode.INVALID_INPUT, "Unknown region ids", missing)
    items = [by_id[i] for i in inp.region_ids]
    if not items:
        out = ReadingOrderOutput(ordered_ids=[], reading_order_confidence=1.0)
        store.put("reading_order", inp.page_id, out.model_dump(mode="json"))
        audit.append(event_type="reading_order_run", object_type="page", object_id=inp.page_id,
                     details={"regions": 0, "confidence": out.reading_order_confidence})
        return out
    pw = items[0]["pw"] or max(r["bbox"][2] for r in items)
    headers = [r for r in items if r["type"] == "header"]
    footers = [r for r in items if r["type"] == "footer"]
    caps = [r for r in items if r["type"] == "caption"]
    body = [r for r in items if r["type"] not in ("header", "footer", "caption")]
    state = {"ambiguous": False}
    ordered = _xy_cut(body, config.get("layout.column_gap_ratio", 0.04) * pw, state)
    for c in sorted(caps, key=lambda r: (r["bbox"][1], r["bbox"][0])):
        anchors = [(abs(c["bbox"][1] - a["bbox"][3]) if a["bbox"][3] <= c["bbox"][1] + 5 else abs(a["bbox"][1] - c["bbox"][3]), i)
                   for i, a in enumerate(ordered) if a["type"] in ("figure", "table", "chart")]
        if anchors:
            ordered.insert(min(anchors)[1] + 1, c)
        else:
            ordered.append(c)
    ids = [r["region_id"] for r in sorted(headers, key=lambda r: (r["bbox"][1], r["bbox"][0]))] + \
          [r["region_id"] for r in ordered] + \
          [r["region_id"] for r in sorted(footers, key=lambda r: (r["bbox"][1], r["bbox"][0]))]
    assert sorted(ids) == sorted(inp.region_ids), "reading order must be a permutation"
    warns = []
    conf = _agreement(ids, headers_first(_second_order(body, pw), headers, footers, caps, ids))
    if state["ambiguous"]:
        conf *= 0.7
        warns.append(WarningItem(code="AMBIGUOUS_COLUMNS", message="Column split is ambiguous"))
    for r in body:
        for o in body:
            if r is not o and r["type"] == "text" and o["type"] == "text" and \
                    (r["bbox"][2] - r["bbox"][0]) < 0.25 * pw < 0.5 * (o["bbox"][2] - o["bbox"][0]) and \
                    r["bbox"][1] < o["bbox"][3] and r["bbox"][3] > o["bbox"][1] and r["bbox"][0] >= o["bbox"][2] - 1:
                warns.append(WarningItem(code="SIDEBAR", message="Floating sidebar region", block_id=r["region_id"]))
                break
    out = ReadingOrderOutput(ordered_ids=ids, reading_order_confidence=round(max(0.0, min(1.0, conf)), 4), warnings=warns)
    store.put("reading_order", inp.page_id, out.model_dump(mode="json"))
    audit.append(event_type="reading_order_run", object_type="page", object_id=inp.page_id,
                 details={"regions": len(ids), "confidence": out.reading_order_confidence})
    return out


def headers_first(second: list[str], headers, footers, caps, final_ids: list[str]) -> list[str]:
    """Build comparable second ordering over the same id set (headers first, captions where XY-cut put them)."""
    base = [r["region_id"] for r in headers] + second + [r["region_id"] for r in footers]
    for cid in [r["region_id"] for r in caps]:
        if cid in final_ids:
            idx = final_ids.index(cid)
            prev = final_ids[idx - 1] if idx > 0 else None
            pos = base.index(prev) + 1 if prev in base else len(base)
            base.insert(pos, cid)
    return base
