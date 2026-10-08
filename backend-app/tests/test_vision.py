import json

import pytest

from backend.agents import a01_file_validation as a01
from backend.agents import a04_ocr as a04
from backend.agents import a05_layout as a05
from backend.agents import a06_reading_order as a06
from backend.agents import a07_table as a07
from backend.agents import a09_chart_figure as a09
from backend.agents import a10_equation as a10
from backend.common import config, llm_guard
from backend.common.errors import AgentError, ErrorCode
from backend.common.store import store
from tests.fixtures import make


def up(data, name="a.pdf"):
    return a01.run(a01.FileValidationInput(filename=name, data=data)).source_id


# ------------- 04 OCR -------------
def test_ocr_reads_scanned_text(analyst):
    sid = up(make.scanned_pdf("INVOICE 4821 TOTAL 1250"))
    o = a04.run(a04.OcrInput(source_id=sid, page_number=1))
    text = " ".join(l.text for l in o.lines).upper()
    assert o.engine == "tesseract" and "INVOICE" in text and "4821" in text
    assert all(0 <= l.confidence <= 1 for l in o.lines)


def test_ocr_region_maps_back_to_page_coordinates(analyst):
    sid = up(make.scanned_pdf("HELLO WORLD"))
    full = a04.run(a04.OcrInput(source_id=sid, page_number=1))
    box = full.lines[0].location.bbox
    pad = 10
    reg = [max(0, box[0] - pad), max(0, box[1] - pad), box[2] + pad, box[3] + pad]
    o = a04.run(a04.OcrInput(source_id=sid, page_number=1, region=reg))
    assert o.lines and abs(o.lines[0].location.bbox[1] - box[1]) < 25


def test_ocr_lines_sorted_and_deterministic(analyst):
    sid = up(make.scanned_pdf("ALPHA BETA"))
    a = a04.run(a04.OcrInput(source_id=sid, page_number=1))
    assert a == a04.run(a04.OcrInput(source_id=sid, page_number=1))
    ys = [l.location.bbox[1] for l in a.lines]
    assert ys == sorted(ys)


def test_ocr_unknown_engine_and_bad_region(analyst):
    sid = up(make.scanned_pdf())
    with pytest.raises(AgentError) as e:
        a04.run(a04.OcrInput(source_id=sid, page_number=1, engine="nope"))
    assert e.value.code == ErrorCode.INVALID_INPUT
    with pytest.raises(AgentError) as e2:
        a04.run(a04.OcrInput(source_id=sid, page_number=1, region=[0, 0, 99999, 99999]))
    assert e2.value.code == ErrorCode.INVALID_INPUT


def test_ocr_no_engine_available(analyst, monkeypatch):
    sid = up(make.scanned_pdf())
    monkeypatch.setattr(a04, "ENGINES", {})
    with pytest.raises(AgentError) as e:
        a04.run(a04.OcrInput(source_id=sid, page_number=1))
    assert e.value.code == ErrorCode.ENGINE_FAILED


def test_ocr_timeout_code(analyst, monkeypatch):
    sid = up(make.scanned_pdf())

    def slow(img, timeout):
        raise AgentError(ErrorCode.TIMEOUT, "OCR timed out")
    monkeypatch.setitem(a04.ENGINES, "tesseract", slow)
    with pytest.raises(AgentError) as e:
        a04.run(a04.OcrInput(source_id=sid, page_number=1))
    assert e.value.code == ErrorCode.TIMEOUT


def test_ocr_second_engine_pluggable(analyst, monkeypatch):
    sid = up(make.scanned_pdf())
    monkeypatch.setitem(a04.ENGINES, "fake", lambda img, t: [("fake line", [1, 2, 50, 20], 0.9)])
    o = a04.run(a04.OcrInput(source_id=sid, page_number=1, engine="fake"))
    assert o.engine == "fake" and o.lines[0].text == "fake line"


def test_ocr_page_not_found(analyst):
    sid = up(make.scanned_pdf())
    with pytest.raises(AgentError) as e:
        a04.run(a04.OcrInput(source_id=sid, page_number=5))
    assert e.value.code == ErrorCode.NOT_FOUND


# ------------- 05 layout -------------
def table_rows():
    return [["Item", "Qty", "Amount"], ["Rent", "1", "1,000"], ["Fees", "2", "500"], ["Total", "", "1,500"]]


def test_layout_detects_table_title_text(analyst):
    import pymupdf
    doc = pymupdf.open()
    p = doc.new_page(width=595, height=842)
    p.insert_text((72, 80), "Quarterly Report", fontsize=24)
    p.insert_text((72, 120), "This is the first paragraph of body text for the page.", fontsize=11)
    p.insert_text((72, 136), "More body text follows on the next line here.", fontsize=11)
    sid = up(doc.tobytes())
    o = a05.run(a05.LayoutInput(source_id=sid, page_number=1))
    types = {r.type for r in o.regions}
    assert "title" in types and "text" in types and o.layout_class == "single_column"
    assert all(r.location.coordinate_system == "pixel_top_left" and r.location.page_width for r in o.regions)


def test_layout_table_region_and_stable_ids(analyst):
    sid = up(make.table_pdf(table_rows()))
    o = a05.run(a05.LayoutInput(source_id=sid, page_number=1))
    assert any(r.type == "table" for r in o.regions)
    assert [r.region_id for r in o.regions] == [r.region_id for r in a05.run(a05.LayoutInput(source_id=sid, page_number=1)).regions]


def test_layout_multi_column_derived_from_geometry(analyst):
    import pymupdf
    doc = pymupdf.open()
    p = doc.new_page(width=595, height=842)
    body = "Lorem ipsum dolor sit amet consectetur adipiscing elit sed do eiusmod tempor incididunt ut labore et dolore magna aliqua. " * 3
    for i in range(3):
        p.insert_textbox(pymupdf.Rect(50, 100 + i * 220, 280, 300 + i * 220), body, fontsize=11)
        p.insert_textbox(pymupdf.Rect(320, 100 + i * 220, 550, 300 + i * 220), body, fontsize=11)
    sid = up(doc.tobytes())
    assert a05.run(a05.LayoutInput(source_id=sid, page_number=1)).layout_class == "multi_column"


def test_layout_page_not_found(analyst):
    sid = up(make.text_pdf())
    with pytest.raises(AgentError) as e:
        a05.run(a05.LayoutInput(source_id=sid, page_number=3))
    assert e.value.code == ErrorCode.NOT_FOUND


def test_layout_merge_and_low_confidence_warning(analyst):
    regs = [{"type": "text", "bbox": [0, 0, 100, 100], "confidence": 0.9},
            {"type": "text", "bbox": [2, 2, 100, 100], "confidence": 0.8},
            {"type": "figure", "bbox": [200, 200, 300, 300], "confidence": 0.1}]
    assert len(a05._merge(regs, 0.6)) == 2
    sid = up(make.text_pdf())
    a05.DETECTORS.insert(0, lambda s, p: (regs, 595, 842))
    try:
        o = a05.run(a05.LayoutInput(source_id=sid, page_number=1))
    finally:
        a05.DETECTORS.pop(0)
    assert len(o.regions) == 1 and any(w.code == "LOW_CONFIDENCE_REGION" for w in o.warnings)


def test_layout_class_helpers():
    assert a05.classify_layout([], 100, 100) == "other"
    forms = [{"type": "form_field", "bbox": [0, i * 10, 10, i * 10 + 5], "confidence": 1} for i in range(4)]
    assert a05.classify_layout(forms, 100, 100) == "form"
    assert a05.classify_layout([{"type": "table", "bbox": [0, 0, 100, 80], "confidence": 1}], 100, 100) == "spreadsheet_like"
    assert a05.classify_layout([{"type": "text", "bbox": [0, 0, 50, 20], "confidence": 1}], 200, 100) == "slide"


# ------------- 06 reading order -------------
def seed_layout(pid, regions, pw=1000):
    store.put("layout", pid, {"layout_class": "x", "regions": [
        {"region_id": rid, "type": t, "location": {"bbox": bb, "page_width": pw, "page_height": 1400}, "confidence": 0.9}
        for rid, t, bb in regions]})


def test_reading_order_two_columns_headers_footers_captions(analyst):
    pid = "s-p1"
    seed_layout(pid, [("f", "footer", [0, 1350, 1000, 1390]), ("r1", "text", [540, 200, 950, 400]), ("l2", "text", [50, 450, 480, 650]),
                      ("l1", "text", [50, 200, 480, 400]), ("h", "header", [0, 10, 1000, 60]), ("fig", "figure", [50, 700, 480, 900]),
                      ("cap", "caption", [50, 910, 480, 940]), ("r2", "text", [540, 450, 950, 650])])
    o = a06.run(a06.ReadingOrderInput(page_id=pid, region_ids=["f", "r1", "l2", "l1", "h", "fig", "cap", "r2"]))
    assert o.ordered_ids[0] == "h" and o.ordered_ids[-1] == "f"
    assert o.ordered_ids.index("l1") < o.ordered_ids.index("l2") < o.ordered_ids.index("r1")
    assert o.ordered_ids.index("cap") == o.ordered_ids.index("fig") + 1
    assert sorted(o.ordered_ids) == sorted(["f", "r1", "l2", "l1", "h", "fig", "cap", "r2"])
    assert 0.5 <= o.reading_order_confidence <= 1


def test_reading_order_permutation_property(analyst):
    import random
    rnd = random.Random(7)
    regs = [(f"r{i}", "text", [rnd.randint(0, 800), i * 100, rnd.randint(850, 990), i * 100 + 60]) for i in range(12)]
    seed_layout("p-p1", regs)
    ids = [r[0] for r in regs]
    assert sorted(a06.run(a06.ReadingOrderInput(page_id="p-p1", region_ids=ids)).ordered_ids) == sorted(ids)


def test_reading_order_subset_empty_and_errors(analyst):
    seed_layout("p-p1", [("a", "text", [0, 0, 10, 10]), ("b", "text", [0, 20, 10, 30])])
    assert a06.run(a06.ReadingOrderInput(page_id="p-p1", region_ids=[])).ordered_ids == []
    assert a06.run(a06.ReadingOrderInput(page_id="p-p1", region_ids=["b"])).ordered_ids == ["b"]
    for bad, code in ((["a", "a"], ErrorCode.INVALID_INPUT), (["zz"], ErrorCode.INVALID_INPUT)):
        with pytest.raises(AgentError) as e:
            a06.run(a06.ReadingOrderInput(page_id="p-p1", region_ids=bad))
        assert e.value.code == code
    with pytest.raises(AgentError) as e:
        a06.run(a06.ReadingOrderInput(page_id="missing", region_ids=["a"]))
    assert e.value.code == ErrorCode.NOT_FOUND


def test_reading_order_ambiguous_columns_warning(analyst):
    seed_layout("p-p1", [("a", "text", [50, 100, 480, 300]), ("b", "text", [490, 100, 950, 300]), ("c", "text", [50, 320, 480, 500]), ("d", "text", [490, 320, 950, 500])])
    o = a06.run(a06.ReadingOrderInput(page_id="p-p1", region_ids=["a", "b", "c", "d"]))
    assert any(w.code == "AMBIGUOUS_COLUMNS" for w in o.warnings) and o.reading_order_confidence < 1


# ------------- 07 table -------------
def layout_and_table(sid, rows=None):
    lay = a05.run(a05.LayoutInput(source_id=sid, page_number=1))
    reg = next(r for r in lay.regions if r.type == "table")
    return a07.run(a07.TableInput(source_id=sid, page_number=1, region_id=reg.region_id))


def test_table_cells_headers_normalization_and_badge_pass(analyst):
    t = layout_and_table(up(make.table_pdf(table_rows())))
    d = {(c.row, c.col): c for c in t.cells}
    assert t.n_rows == 4 and t.n_cols == 3 and d[(0, 0)].raw_text == "Item" and d[(0, 0)].is_header
    assert d[(1, 2)].normalized == {"value": "1000", "rule": "strip_thousands_separator"} and d[(1, 0)].normalized is None
    assert [b.status for b in t.badges] == ["pass"] and "1500" in t.badges[0].detail
    assert 0 < t.evidence.confidence <= 1 and all(0 <= c.confidence <= 1 for c in t.cells)


def test_table_badge_mismatch(analyst):
    rows = table_rows()
    rows[3][2] = "1,600"
    t = layout_and_table(up(make.table_pdf(rows)))
    assert t.badges[0].status == "mismatch" and "1500" in t.badges[0].detail and "1600" in t.badges[0].detail


def test_table_no_badge_when_not_computable(analyst):
    rows = [["A", "B"], ["x", "y"], ["z", "w"]]
    assert layout_and_table(up(make.table_pdf(rows))).badges == []


def test_table_never_normalizes_currency_cells(analyst):
    rows = [["Item", "Price"], ["Pen", "$5"], ["Ink", "Rs. 10"]]
    t = layout_and_table(up(make.table_pdf(rows)))
    assert all(c.normalized is None for c in t.cells if c.raw_text in ("$5", "Rs. 10"))


def test_table_merged_cell_true_span(analyst):
    import pymupdf
    doc = pymupdf.open()
    p = doc.new_page(width=595, height=842)
    for (x0, y0, x1, y1, txt) in [(72, 150, 292, 174, "Merged header"), (292, 150, 402, 174, "C"), (72, 174, 182, 198, "a"), (182, 174, 292, 198, "b"),
                                  (292, 174, 402, 198, "1"), (72, 198, 182, 222, "c"), (182, 198, 292, 222, "d"), (292, 198, 402, 222, "2")]:
        r = pymupdf.Rect(x0, y0, x1, y1)
        p.draw_rect(r, color=(0, 0, 0), width=0.8)
        p.insert_text((x0 + 3, y0 + 16), txt, fontsize=10)
    sid = up(doc.tobytes())
    t = layout_and_table(sid)
    m = next(c for c in t.cells if c.raw_text == "Merged header")
    assert m.col_span == 2 and m.row_span == 1 and sum(1 for c in t.cells if c.raw_text == "Merged header") == 1


def test_table_continuation_across_pages(analyst):
    import pymupdf
    doc = pymupdf.open()
    for page_rows in ([["Item", "Amt"], ["a", "1"], ["b", "2"]], [["Item", "Amt"], ["c", "3"], ["d", "4"]]):
        p = doc.new_page(width=595, height=842)
        for r, row in enumerate(page_rows):
            for c, v in enumerate(row):
                rect = pymupdf.Rect(72 + c * 110, 150 + r * 24, 182 + c * 110, 174 + r * 24)
                p.draw_rect(rect, color=(0, 0, 0), width=0.8)
                p.insert_text((rect.x0 + 4, rect.y0 + 16), v, fontsize=10)
    sid = up(doc.tobytes())
    blocks = []
    for pn in (1, 2):
        lay = a05.run(a05.LayoutInput(source_id=sid, page_number=pn))
        reg = next(r for r in lay.regions if r.type == "table")
        blocks.append(a07.run(a07.TableInput(source_id=sid, page_number=pn, region_id=reg.region_id)))
    assert blocks[1].continues_from == blocks[0].block_id
    assert store.get("table", blocks[0].block_id)["continues_to"] == blocks[1].block_id


def test_table_errors(analyst):
    sid = up(make.table_pdf(table_rows()))
    with pytest.raises(AgentError) as e:
        a07.run(a07.TableInput(source_id=sid, page_number=1, region_id="nope"))
    assert e.value.code == ErrorCode.NOT_FOUND
    lay = a05.run(a05.LayoutInput(source_id=sid, page_number=1))
    other = next(r for r in lay.regions if r.type != "table")
    with pytest.raises(AgentError) as e2:
        a07.run(a07.TableInput(source_id=sid, page_number=1, region_id=other.region_id))
    assert e2.value.code == ErrorCode.INVALID_INPUT


def test_parse_numeric_rules():
    p = a07.parse_numeric
    assert p("1,234.50")[1] == "strip_thousands_separator" and str(p("(500)")[0]) == "-500" and p("12%")[1] == "percent_strip" and p("abc") is None and p("$5") is None


# ------------- 09 chart / figure -------------
def seed_region(sid, rtype, bbox):
    from backend.agents.a05_layout import region_id
    rid = region_id(sid, 1, bbox)
    store.put("layout", f"{sid}-p1", {"layout_class": "other", "regions": [
        {"region_id": rid, "type": rtype, "location": {"bbox": bbox, "page_width": 1653, "page_height": 827}, "confidence": 0.85}]})
    return rid


def test_figure_block_crop_url_and_no_fabrication(analyst):
    sid = up(make.scanned_pdf())
    rid = seed_region(sid, "figure", [10, 10, 800, 400])
    b = a09.run(a09.ChartFigureInput(source_id=sid, page_number=1, region_id=rid))
    assert b.type == "figure" and b.crop_url.startswith(f"/sources/{sid}/crops/")
    assert store.get("crop", f"{sid}:{b.crop_url.rsplit('/', 1)[1]}")[:4] == b"\x89PNG"


def test_chart_without_model_returns_null_series_with_warning(analyst):
    sid = up(make.scanned_pdf("Q1 10 Q2 20 Q3 30 Q4 40"))
    rid = seed_region(sid, "chart", [10, 10, 1600, 800])
    b = a09.run(a09.ChartFigureInput(source_id=sid, page_number=1, region_id=rid))
    assert b.type == "chart" and b.series is None and any(w.code == "CHART_NOT_DERENDERED" for w in b.warnings)
    assert b.evidence.confidence < 1


def test_chart_series_grounded_in_ocr_numbers_only(analyst):
    sid = up(make.scanned_pdf("Q1 10 Q2 20 Q3 30 Q4 40", size=1200))
    rid = seed_region(sid, "chart", [10, 10, 1600, 800])
    calls = []

    def prov(system, user, schema, **k):
        calls.append(1)
        if len(calls) == 1:
            return json.dumps({"chart_type": "bar", "title": None, "series": [{"name": "s", "points": [{"x": "Q1", "y": 10}, {"x": "Q2", "y": 777}]}]})
        return json.dumps({"insight_text": "Values rise."})
    llm_guard.provider = prov
    b = a09.run(a09.ChartFigureInput(source_id=sid, page_number=1, region_id=rid))
    assert b.type == "chart"
    assert b.series is None or all(p["y"] != 777 for s in b.series for p in s["points"])
    assert b.evidence.confidence <= 0.8


def test_chart_figure_region_errors(analyst):
    sid = up(make.scanned_pdf())
    with pytest.raises(AgentError) as e:
        a09.run(a09.ChartFigureInput(source_id=sid, page_number=1, region_id="x"))
    assert e.value.code == ErrorCode.NOT_FOUND
    rid = seed_region(sid, "text", [0, 0, 10, 10])
    with pytest.raises(AgentError) as e2:
        a09.run(a09.ChartFigureInput(source_id=sid, page_number=1, region_id=rid))
    assert e2.value.code == ErrorCode.INVALID_INPUT


# ------------- 10 equation -------------
def test_equation_no_engine_partial_output(analyst):
    sid = up(make.scanned_pdf())
    rid = seed_region(sid, "equation", [10, 10, 800, 200])
    b = a10.run(a10.EquationInput(source_id=sid, page_number=1, region_id=rid))
    assert b.latex is None and not b.verified and any(w.code == "NO_EQUATION_ENGINE" for w in b.warnings)


def test_equation_parse_gate_rejects_bad_latex(analyst, monkeypatch):
    sid = up(make.scanned_pdf())
    rid = seed_region(sid, "equation", [10, 10, 800, 200])
    monkeypatch.setattr(a10, "RECOGNIZERS", [lambda png: r"\frac{a}{b"])
    b = a10.run(a10.EquationInput(source_id=sid, page_number=1, region_id=rid))
    assert not b.verified and any(w.code == "LATEX_PARSE_FAILED" for w in b.warnings)


def test_equation_rerender_gate_blocks_mismatch_and_passes_match(analyst, monkeypatch):
    sid = up(make.scanned_pdf("x"))
    rid = seed_region(sid, "equation", [0, 0, 800, 250])
    monkeypatch.setattr(a10, "RECOGNIZERS", [lambda png: r"\sum_{i=1}^{n} i^2 = \frac{n(n+1)(2n+1)}{6}"])
    b = a10.run(a10.EquationInput(source_id=sid, page_number=1, region_id=rid))
    assert not b.verified and any(w.code in ("RERENDER_MISMATCH", "RERENDER_UNAVAILABLE") for w in b.warnings)
    monkeypatch.setattr(a10, "render_similarity", lambda l, c: 0.95)
    assert a10.run(a10.EquationInput(source_id=sid, page_number=1, region_id=rid)).verified


def test_equation_plain_text_and_parse_ok():
    assert a10.plain_text(r"\frac{a}{b} + \alpha^2") == "(a)/(b) + α^2"
    assert a10.parse_ok(r"\foo{x}")[0] is False and a10.parse_ok(r"\left( x \right)")[0] is True
