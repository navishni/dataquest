"""Convenience orchestrator for integration checkpoint 2 (pipeline 01 -> 12 on one file).

Runs agents 02, 03/04, 05, 06, 07/09/10, 21, 11, 12 for a registered source, page by page. A failing step is recorded
in `steps` and the pipeline continues (partial output + warnings, never a 500). Agent 11 then reports CONFLICT listing
exactly what is missing if a required stage could not run.
"""
from .agents import (a02_format_router as a02, a03_native_text as a03, a04_ocr as a04, a05_layout as a05,
                     a06_reading_order as a06, a07_table as a07, a09_chart_figure as a09, a10_equation as a10,
                     a11_json_assembly as a11, a12_confidence_validation as a12, a21_consensus as a21)
from .common import audit
from .common.errors import AgentError
from .common.store import store
from .agents.a03_native_text import page_id
from .agents import a25_universal_formats as a25


def _run_pipeline(source_id: str) -> dict:
    steps: list[dict] = []

    def step(name: str, fn):
        try:
            r = fn()
            steps.append({"step": name, "ok": True})
            return r
        except AgentError as e:
            steps.append({"step": name, "ok": False, "code": e.code.value, "message": e.message})
            return None

    router = step("format_router", lambda: a02.run(a02.RouterInput(source_id=source_id)))
    if router is None:
        return {"source_id": source_id, "status": "failed", "steps": steps}
    mime = store.meta(source_id)["detected_mime"]
    if mime == "application/pdf" or mime.startswith("image/"):
        for u in router.units:
            pn = u.page_number
            if u.page_class == "blank":
                continue
            if mime == "application/pdf" and u.page_class in ("native_text", "mixed"):
                step(f"native_text:p{pn}", lambda pn=pn: a03.run(a03.NativeTextInput(source_id=source_id, page_number=pn)))
            if u.page_class in ("scanned", "mixed", "image_only") or mime.startswith("image/"):
                for eng in list(a04.ENGINES):
                    step(f"ocr:{eng}:p{pn}", lambda pn=pn, eng=eng: a04.run(a04.OcrInput(source_id=source_id, page_number=pn, engine=eng)))
            lay = step(f"layout:p{pn}", lambda pn=pn: a05.run(a05.LayoutInput(source_id=source_id, page_number=pn)))
            if lay is None:
                continue
            step(f"reading_order:p{pn}", lambda pn=pn, lay=lay: a06.run(a06.ReadingOrderInput(
                page_id=page_id(source_id, pn), region_ids=[r.region_id for r in lay.regions])))
            for r in lay.regions:
                if r.type == "table":
                    step(f"table:{r.region_id}", lambda pn=pn, r=r: a07.run(a07.TableInput(source_id=source_id, page_number=pn, region_id=r.region_id)))
                elif r.type in ("chart", "figure"):
                    step(f"chart_figure:{r.region_id}", lambda pn=pn, r=r: a09.run(a09.ChartFigureInput(source_id=source_id, page_number=pn, region_id=r.region_id)))
                elif r.type == "equation":
                    step(f"equation:{r.region_id}", lambda pn=pn, r=r: a10.run(a10.EquationInput(source_id=source_id, page_number=pn, region_id=r.region_id)))
        step("consensus", lambda: a21.run(a21.ConsensusInput(source_id=source_id)))
        doc = step("json_assembly", lambda: a11.run(a11.AssemblyInput(source_id=source_id)))
        conf = step("confidence_validation", lambda: a12.run(a12.ConfidenceInput(source_id=source_id))) if doc else None
        status = "complete" if doc and conf and doc.status == "complete" and all(s["ok"] for s in steps) else "partial" if doc else "failed"
    else:
        doc = step("universal_format_extraction", lambda: a25.run(a25.UniversalInput(source_id=source_id)))
        conf = step("confidence_validation", lambda: a12.run(a12.ConfidenceInput(source_id=source_id))) if doc else None
        status = "complete" if doc and conf and doc.status == "complete" and all(s["ok"] for s in steps) else "partial" if doc else "failed"
    audit.append(event_type="pipeline_run", object_type="source", object_id=source_id,
                 details={"status": status, "failed_steps": [s["step"] for s in steps if not s["ok"]]})
    return {"source_id": source_id, "status": status, "steps": steps}


def run_pipeline(source_id: str) -> dict:
    try:
        return _run_pipeline(source_id)
    finally:
        store.clear_password(source_id)
