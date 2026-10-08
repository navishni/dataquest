"""Config service. /config is the single source of limits, thresholds, formats, bands, etc.

Reads backend/config.json once; override path with PARSEFUSION_CONFIG. Agents call
config.get("limits.max_size_bytes") and never hardcode thresholds.
"""
import json
import os
from pathlib import Path
from typing import Any

_DEFAULT = Path(__file__).resolve().parent.parent / "config.json"
_cache: dict | None = None


def load(force: bool = False) -> dict:
    global _cache
    if _cache is None or force:
        p = Path(os.environ.get("PARSEFUSION_CONFIG", _DEFAULT))
        _cache = json.loads(p.read_text(encoding="utf-8"))
    return _cache


def override(values: dict) -> None:
    """Test/admin hook: deep-merge values into the live config."""
    def merge(a, b):
        for k, v in b.items():
            if isinstance(v, dict) and isinstance(a.get(k), dict):
                merge(a[k], v)
            else:
                a[k] = v
    merge(load(), values)


def get(path: str, default: Any = None) -> Any:
    cur: Any = load()
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur


def public() -> dict:
    """Expose the frontend contract while keeping backend knobs server-side."""
    raw = load()
    ui = raw.get("ui", {})
    bands = raw.get("bands", {})
    high = float(bands.get("high", 1.0))
    medium = float(bands.get("medium", high))
    return {
        "poll_interval_ms": ui.get("poll_interval_ms", 1500),
        "auth": {"mode": "bearer", "login_url": "/auth/login"},
        "processing_modes": ui.get("processing_modes", []),
        "output_formats": [{"id": x, "label": x.upper()} for x in raw.get("formats", [])],
        "export_scopes": [{"id": x, "label": x.title()} for x in raw.get("export_scopes", [])],
        "action_types": [{"id": x, "label": x.replace("_", " ").title()} for x in raw.get("action_types", [])],
        "limits": {
            "max_file_mb": raw.get("limits", {}).get("max_size_bytes", 0) / (1024 * 1024),
            "max_pages": raw.get("limits", {}).get("max_pages", 0),
            "accepted_types": ui.get("accepted_types", []),
        },
        "confidence_bands": [
            {"id": "low", "min": 0, "max": medium, "label": "Low"},
            {"id": "medium", "min": medium, "max": high, "label": "Medium"},
            {"id": "high", "min": high, "max": 1, "label": "High"},
        ],
        "severity_levels": [{"id": x, "label": x.title()} for x in raw.get("severities", [])],
        "feature_flags": ui.get("feature_flags", {}),
        "pipeline_stages": ui.get("pipeline_stages", []),
    }
