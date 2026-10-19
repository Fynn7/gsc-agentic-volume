"""Live resource telemetry (read-only tail of telemetry.jsonl)."""
from __future__ import annotations

import json
from pathlib import Path


def latest_telemetry(run_dir: str | Path) -> dict:
    """Return the newest telemetry sample or {} when unavailable."""
    path = Path(run_dir) / "telemetry.jsonl"
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return {}
    for line in reversed(lines[-20:]):
        try:
            data = json.loads(line)
        except ValueError:
            continue
        if isinstance(data, dict):
            return data
    return {}


def resource_line(run_dir: str | Path) -> str:
    """One-line GPU/VRAM + Ollama context summary for the Live header."""
    sample = latest_telemetry(run_dir)
    if not sample:
        return "resources: telemetry not yet written"
    parts: list[str] = []
    gpu = sample.get("gpu")
    if gpu:
        parts.append("GPU %s" % str(gpu)[:80])
    ollama = sample.get("ollama")
    if isinstance(ollama, dict):
        models = ollama.get("models") or []
        if models:
            first = models[0] if isinstance(models[0], dict) else {}
            ctx = first.get("context_length") or first.get("contextLength")
            parts.append("ollama %s ctx=%s" % (first.get("name", "?"), ctx))
        else:
            parts.append("ollama: idle")
    elif isinstance(ollama, str):
        parts.append("ollama %s" % ollama[:40])
    if not parts:
        parts.append("telemetry present, no GPU/ollama fields")
    return " | ".join(parts)
