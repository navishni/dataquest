"""Agent 10 - Equation (EquationBlock).

Display equations only. The crop is passed to a registered image->LaTeX recognizer (RECOGNIZERS: callable(PNG bytes)
-> latex str; register pix2tex/Nougat/MathPix wrappers here). With none registered the block is returned with
latex=None, verified=false and warning NO_EQUATION_ENGINE (partial output, not a 500).
verified = true ONLY if (1) the LaTeX parses (balanced braces/\\left-\\right/\\begin-\\end, every \\command is in the
supported set, matplotlib mathtext accepts it) AND (2) the re-render of the LaTeX (matplotlib mathtext) correlates with
the crop at >= config.equation.min_similarity (normalised cross-correlation of 64x256 binarised images).
Otherwise verified=false with a warning explaining which gate failed.
Confidence = 0.5 * parse_ok + 0.5 * similarity (0 when unverified-by-engine); never 1.0 unless both gates are perfect.
plain_text is a deterministic LaTeX->text rendering (\\frac, \\sqrt, ^, _, Greek letters).
"""
import hashlib
import io
import re
from typing import Callable, Optional

from pydantic import BaseModel, ConfigDict, Field

from ..common import audit, config
from ..common.errors import AgentError, ErrorCode
from ..common.models import EquationBlock, Evidence, Location, WarningItem
from ..common.store import store
from .a03_native_text import page_id

RECOGNIZERS: list[Callable[[bytes], str]] = []
_GREEK = {"alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "epsilon": "ε", "theta": "θ", "lambda": "λ",
          "mu": "μ", "pi": "π", "sigma": "σ", "phi": "φ", "omega": "ω", "Delta": "Δ", "Sigma": "Σ", "Omega": "Ω"}
_CMDS = set(_GREEK) | {"frac", "sqrt", "sum", "int", "prod", "left", "right", "cdot", "times", "pm", "leq", "geq", "neq",
                       "approx", "infty", "partial", "sin", "cos", "tan", "log", "ln", "exp", "lim", "begin", "end",
                       "text", "mathrm", "mathbf", "to", "rightarrow", "in", "cdots", "ldots", "div", "bar", "hat", "vec"}


class EquationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_id: str
    page_number: int = Field(ge=1)
    region_id: str


def parse_ok(latex: str) -> tuple[bool, str]:
    depth = 0
    for ch in latex:
        depth += (ch == "{") - (ch == "}")
        if depth < 0:
            return False, "unbalanced braces"
    if depth:
        return False, "unbalanced braces"
    if len(re.findall(r"\\left", latex)) != len(re.findall(r"\\right", latex)):
        return False, "unbalanced \\left/\\right"
    if len(re.findall(r"\\begin", latex)) != len(re.findall(r"\\end", latex)):
        return False, "unbalanced \\begin/\\end"
    unknown = [c for c in re.findall(r"\\([A-Za-z]+)", latex) if c not in _CMDS]
    if unknown:
        return False, f"unsupported command {unknown[0]}"
    return True, ""


def plain_text(latex: str) -> str:
    t = latex
    for _ in range(5):
        t = re.sub(r"\\frac\{([^{}]*)\}\{([^{}]*)\}", r"(\1)/(\2)", t)
        t = re.sub(r"\\sqrt\{([^{}]*)\}", r"sqrt(\1)", t)
    t = re.sub(r"\\([A-Za-z]+)", lambda m: _GREEK.get(m.group(1), {"cdot": "·", "times": "×", "leq": "<=", "geq": ">=",
                                                                     "neq": "!=", "pm": "±", "infty": "∞", "to": "->",
                                                                     "approx": "≈"}.get(m.group(1), m.group(1))), t)
    t = t.replace("{", "").replace("}", "").replace("\\", "")
    return re.sub(r"\s+", " ", t).strip()


def _binarise(im, size=(256, 64)):
    import numpy as np
    from PIL import Image
    g = im.convert("L").resize(size)
    a = np.asarray(g, dtype=float)
    return (a < a.mean()).astype(float)


def render_similarity(latex: str, crop_png: bytes) -> Optional[float]:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import numpy as np
        from PIL import Image
        fig = plt.figure(figsize=(4, 1), dpi=100)
        fig.text(0.5, 0.5, f"${latex}$", ha="center", va="center", fontsize=18)
        buf = io.BytesIO()
        fig.savefig(buf, format="png")
        plt.close(fig)
        a = _binarise(Image.open(io.BytesIO(buf.getvalue())))
        b = _binarise(Image.open(io.BytesIO(crop_png)))
        a, b = a - a.mean(), b - b.mean()
        den = np.sqrt((a * a).sum() * (b * b).sum())
        return float(max(0.0, (a * b).sum() / den)) if den else 0.0
    except Exception:  # noqa: BLE001 - invalid mathtext etc.
        return None


def run(inp: EquationInput) -> EquationBlock:
    pid = page_id(inp.source_id, inp.page_number)
    lay = store.get("layout", pid)
    reg = next((r for r in (lay or {}).get("regions", []) if r["region_id"] == inp.region_id), None)
    if reg is None:
        raise AgentError(ErrorCode.NOT_FOUND, "Region not found (run layout first)")
    if reg["type"] != "equation":
        raise AgentError(ErrorCode.INVALID_INPUT, "Region is not an equation")
    bbox = reg["location"]["bbox"]
    loc = Location(bbox=bbox, page_width=reg["location"]["page_width"], page_height=reg["location"]["page_height"])
    from .a09_chart_figure import _crop
    crop = _crop(store.page_image(inp.source_id, inp.page_number), bbox)
    block_id = hashlib.sha256(f"{inp.source_id}|{pid}|{inp.region_id}|eq".encode()).hexdigest()[:16]
    warns: list[WarningItem] = []
    latex, verified, conf, method = None, False, 0.0, "none"
    if not RECOGNIZERS:
        warns.append(WarningItem(code="NO_EQUATION_ENGINE", message="No image-to-LaTeX engine registered"))
    else:
        try:
            latex = RECOGNIZERS[0](crop).strip()
            method = "image_to_latex"
        except Exception:  # noqa: BLE001
            raise AgentError(ErrorCode.ENGINE_FAILED, "Equation recognizer failed")
        ok, why = parse_ok(latex)
        sim = render_similarity(latex, crop) if ok else None
        thr = config.get("equation.min_similarity", 0.8)
        if not ok:
            warns.append(WarningItem(code="LATEX_PARSE_FAILED", message=why))
        elif sim is None:
            warns.append(WarningItem(code="RERENDER_UNAVAILABLE", message="Could not re-render LaTeX for comparison"))
        elif sim < thr:
            warns.append(WarningItem(code="RERENDER_MISMATCH", message=f"Re-render similarity {sim:.2f} below {thr}"))
        verified = bool(ok and sim is not None and sim >= thr)
        conf = round(0.5 * (1 if ok else 0) + 0.5 * (sim or 0.0), 4)
    block = EquationBlock(block_id=block_id, latex=latex, plain_text=plain_text(latex) if latex else None,
                          verified=verified, warnings=warns,
                          evidence=Evidence(source_id=inp.source_id, page_id=pid, location=loc,
                                            extraction_method=method, confidence=conf))
    store.put("equation", block_id, block.model_dump(mode="json"))
    audit.append(event_type="equation_extracted", object_type="region", object_id=inp.region_id,
                 details={"verified": verified})
    return block
