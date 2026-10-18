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
            "rendererService": "http://127.0.0.1:8765"}


def load_settings() -> dict:
    merged = dict(DEFAULTS)
    try:
        stored = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        if isinstance(stored, dict):
            merged.update({k: stored[k] for k in DEFAULTS if k in stored})
    except (OSError, ValueError):
        pass
    return merged


def save_settings(settings: dict) -> None:
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps({k: settings.get(k, DEFAULTS[k]) for k in DEFAULTS}, indent=2), encoding="utf-8")


def active_endpoint(settings: dict) -> str:
    return settings["h200Endpoint"] if settings.get("mode") == "H200" else settings["endpoint"]


def example_config() -> dict:
    return json.loads(EXAMPLE.read_text(encoding="utf-8"))


def build_effective_config(task_kind: str, custom_text: str, settings: dict) -> dict:
    """Fresh config from the example; never copies another experiment's prompt."""
    config = example_config()
    config["harness"] = "codex"
    config["model"] = settings["model"]
    config["endpoint"] = active_endpoint(settings)
    config["rendererService"] = settings["rendererService"]
    volume = GATE_VOLUME.resolve() if GATE_VOLUME.is_file() else None
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
