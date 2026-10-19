"""Default tasks, example reuse, and user settings (Local/H200 presets).

Formal Task A/B strings intentionally match docs/CODEX_CLI_anleitung.md.
Custom tasks never inherit A/B prompts: they are built fresh from the example.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "experiments" / "local-vlm.example.json"
SETTINGS_FILE = ROOT / "runtime" / "tui-settings.json"
GATE_VOLUME = ROOT / "runtime" / "gate-volume" / "render.dat"

TASK_A = ("Set the camera rotationXYZ to [25,-35,10] degrees. Preserve transfer function, "
          "translation, crop and all unrelated parameters. Inspect the real initial and "
          "resulting images. Save the final native PNG and report the actual settings.")
TASK_B = ("Find a more informative view of this volume. Inspect the initial real PNG. "
          "Choose a camera, transfer-function or crop adjustment based on its visible content. "
          "After receiving the new native PNG, examine it before deciding a second adjustment. "
          "Produce at least two new native renders. Preserve unrelated parameters. "
          "Avoid aggressive clipping without evidence. Save the most informative actual frame "
          "and state limitations honestly.")

BASES = {"task-a": "runtime/local-vlm/cli-task-a",
         "task-b": "runtime/local-vlm/cli-task-b",
         "custom": "runtime/local-vlm/cli-custom"}

DEFAULTS = {"mode": "Local", "model": "qwen3.6:35b",
            "endpoint": "http://127.0.0.1:11434",
            "h200Endpoint": "http://127.0.0.1:11435",
            "rendererService": "http://127.0.0.1:8765",
            "volumePath": "",
            "contextSize": 16384,
            "renderBudget": 4,
            "temperature": 0,
            "think": False,
            "timeoutSeconds": 300,
            "taskTimeoutSeconds": 900,
            "maxOutputTokens": 1200}


def _coerce_settings(stored: dict) -> dict:
    """Coerce persisted settings to usable types; invalid values fall back to defaults."""
    out = dict(DEFAULTS)
    for key in DEFAULTS:
        if key not in stored:
            continue
        value = stored[key]
        default = DEFAULTS[key]
        try:
            if isinstance(default, bool):
                if isinstance(value, str):
                    lowered = value.strip().lower()
                    if lowered in ("1", "true", "yes", "on"):
                        out[key] = True
                    elif lowered in ("0", "false", "no", "off"):
                        out[key] = False
                elif isinstance(value, (int, float)):
                    out[key] = bool(value)
                elif isinstance(value, bool):
                    out[key] = value
            elif isinstance(default, int) and not isinstance(default, bool):
                out[key] = int(value)
            elif isinstance(default, float):
                out[key] = float(value)
            else:
                out[key] = value
        except (TypeError, ValueError):
            out[key] = default
    return out


def load_settings() -> dict:
    merged = dict(DEFAULTS)
    try:
        stored = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        if isinstance(stored, dict):
            merged = _coerce_settings(stored)
    except (OSError, ValueError):
        pass
    return merged


def validate_settings(settings: dict) -> list[str]:
    """Human-readable problems for the Settings form; empty means runnable."""
    problems: list[str] = []
    if settings.get("mode") not in ("Local", "H200"):
        problems.append("mode must be Local or H200")
    if not str(settings.get("model") or "").strip():
        problems.append("model must not be empty")
    for key in ("endpoint", "h200Endpoint", "rendererService"):
        value = str(settings.get(key) or "").strip()
        if not value.startswith("http://127.0.0.1:") and not value.startswith("http://localhost:"):
            problems.append("%s must be a loopback URL" % key)
    try:
        budget = int(settings.get("renderBudget", 0))
        if not 1 <= budget <= 30:
            problems.append("renderBudget must be between 1 and 30")
    except (TypeError, ValueError):
        problems.append("renderBudget must be an integer 1..30")
    for key in ("contextSize", "timeoutSeconds", "taskTimeoutSeconds", "maxOutputTokens"):
        try:
            if int(settings.get(key, 0)) < 1:
                problems.append("%s must be >= 1" % key)
        except (TypeError, ValueError):
            problems.append("%s must be a number" % key)
    try:
        temp = float(settings.get("temperature", 0))
        if not 0 <= temp <= 2:
            problems.append("temperature is usually 0..2")
    except (TypeError, ValueError):
        problems.append("temperature must be a number")
    return problems


def save_settings(settings: dict) -> None:
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps({k: settings.get(k, DEFAULTS[k]) for k in DEFAULTS}, indent=2), encoding="utf-8")


def active_endpoint(settings: dict) -> str:
    return settings["h200Endpoint"] if settings.get("mode") == "H200" else settings["endpoint"]


def selected_volume(settings: dict) -> Path | None:
    """Existing volume chosen in the TUI, else the audited gate volume, else None."""
    raw = str((settings or {}).get("volumePath") or "").strip().strip('"')
    if raw:
        candidate = Path(raw).expanduser()
        if candidate.is_file():
            return candidate.resolve()
    return GATE_VOLUME.resolve() if GATE_VOLUME.is_file() else None


def example_config() -> dict:
    return json.loads(EXAMPLE.read_text(encoding="utf-8"))


def build_effective_config(task_kind: str, custom_text: str, settings: dict) -> dict:
    """Fresh config from the example; never copies another experiment's prompt."""
    config = example_config()
    config["harness"] = "codex"
    config["model"] = settings["model"]
    config["endpoint"] = active_endpoint(settings)
    config["rendererService"] = settings["rendererService"]
    for key in ("contextSize", "renderBudget", "temperature", "think",
                "timeoutSeconds", "taskTimeoutSeconds", "maxOutputTokens"):
        if key in settings:
            config[key] = settings[key]
    volume = selected_volume(settings)
    config["volumePath"] = str(volume) if volume else config.get("volumePath")
    if task_kind == "task-a":
        config["task"] = TASK_A
    elif task_kind == "task-b":
        config["task"] = TASK_B
    else:
        text = (custom_text or "").strip()
        if not text:
            raise ValueError("Custom task text is empty.")
        config["task"] = text
    config["resultDirectory"] = BASES.get(task_kind, BASES["custom"])
    return config


def write_run_config(config: dict, task_kind: str) -> Path:
    """Write the per-launch config file under runtime/; original example untouched."""
    import secrets
    import time
    directory = ROOT / "runtime" / "tui-configs"
    directory.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    path = directory / ("tui-%s-%s-%s.json" % (task_kind, stamp, secrets.token_hex(3)))
    path.write_text(json.dumps(config, indent=2), encoding="utf-8")
    return path
