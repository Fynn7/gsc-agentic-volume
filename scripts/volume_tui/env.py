"""Startup environment checks. Read-only: never starts a model, agent, or download."""
from __future__ import annotations

import json
import shutil
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _http_json(url: str, timeout: float = 3.0):
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.load(response)


def check_environment(settings: dict) -> dict:
    """Return {'ok': bool, 'items': [{'name','ok','detail','fix'}]}."""
    items = []

    def add(name, ok, detail="", fix=""):
        items.append({"name": name, "ok": bool(ok), "detail": detail, "fix": fix})

    try:
        import numpy  # noqa: F401
        import PIL  # noqa: F401
        add("Python deps (NumPy/Pillow)", True, sys.version.split()[0])
    except Exception as error:
        add("Python deps (NumPy/Pillow)", False, str(error),
            "Use the bundled Codex runtime python or install deps into the isolated TUI env (see README).")

    codex = shutil.which("codex.cmd") or shutil.which("codex")
    add("Codex CLI", bool(codex), codex or "not on PATH",
        "Install Codex CLI and ensure `codex` resolves." if not codex else "")

    ollama = shutil.which("ollama") or shutil.which("ollama.exe")
    add("Ollama binary", bool(ollama), ollama or "not on PATH",
        "Install Ollama (Local mode needs it)." if not ollama else "")

    volume = ROOT / "runtime" / "gate-volume" / "render.dat"
    raycaster = ROOT / "VirtualSampling" / "Raycaster" / "VS" / "x64" / "Release" / "Raycaster.exe"
    add("Default volume", volume.is_file(), str(volume),
        "Restore runtime/gate-volume/render.dat; do not substitute another dataset silently." if not volume.is_file() else "")
    add("Native raycaster", raycaster.is_file(), str(raycaster),
        "Build/restore the audited Raycaster.exe; stop if missing." if not raycaster.is_file() else "")

    endpoint = (settings.get("h200Endpoint") if settings.get("mode") == "H200" else settings.get("endpoint", "")).rstrip("/")
    try:
        version = _http_json(endpoint + "/api/version")
        add("Inference %s" % endpoint, True, str(version.get("version", version))[:80])
    except Exception as error:
        if settings.get("mode") == "H200":
            add("Inference %s" % endpoint, False, str(error)[:160],
                "Open the documented SSH tunnel first; never silently fall back to local/small/cloud models.")
        else:
            add("Inference %s" % endpoint, False, str(error)[:160],
                "Start local Ollama (`ollama serve`) or run Start-VolumeTUI with -StartOllama path; TUI never auto-starts it silently.")

    renderer = settings.get("rendererService", "").rstrip("/")
    try:
        identity = _http_json(renderer + "/api/service")
        ok = identity.get("service") == "gav-native-renderer"
        add("Renderer %s" % renderer, ok, str(identity)[:120],
            "" if ok else "Port conflict: the URL does not identify gav-native-renderer. Check port 8765.")
    except Exception as error:
        add("Renderer %s" % renderer, False, str(error)[:160],
            "The TUI auto-starts it on entry; if this persists, start it yourself from the "
            "repo root with the Codex bundled python: python -B src/app.py --port 8765.")

    model_ok = True
    if settings.get("mode") == "H200":
        # Honest pending state: remote capability check happens live in Settings.
        model_ok = True
    _ = model_ok
    return {"ok": all(i["ok"] for i in items if i["name"].startswith(("Python", "Codex", "Default", "Native"))),
            "items": items}


_CHECK_CACHE = {"at": 0.0, "key": None, "value": None}
CHECK_TTL = 15.0


def cached_check(settings: dict, ttl: float = CHECK_TTL) -> dict:
    """Cached preflight so every menu redraw does not block on HTTP timeouts."""
    import time
    key = json.dumps(settings, sort_keys=True)
    now = time.time()
    if _CHECK_CACHE["value"] is not None and _CHECK_CACHE["key"] == key and (now - _CHECK_CACHE["at"]) < ttl:
        return _CHECK_CACHE["value"]
    result = check_environment(settings)
    _CHECK_CACHE.update(at=now, key=key, value=result)
    return result
