"""Headless Windows spawning with a TUI debug toggle.

Default is headless: on Windows every TUI-owned subprocess call hides its
console window (CREATE_NO_WINDOW + STARTUPINFO SW_HIDE), and the native
Raycaster hides its GLFW window. Set `debugWindows` in the TUI settings
(writes runtime/tui-settings.json) — or GAV_DEBUG_WINDOWS=1 in the
environment — to keep the old visible behaviour for debugging.

Only the standard library is used, so the renderer service (src/) and the
launcher (scripts/) can share this without pulling in TUI dependencies.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

ENV_VAR = "GAV_DEBUG_WINDOWS"
SETTINGS_KEY = "debugWindows"
SETTINGS_FILE = Path(__file__).resolve().parents[2] / "runtime" / "tui-settings.json"

CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_NO_WINDOW = 0x08000000
DETACHED_PROCESS = 0x00000008

_CACHE = {"at": 0.0, "mtime": None, "value": False}
_CACHE_TTL = 2.0


def _env_override() -> bool | None:
    raw = os.environ.get(ENV_VAR, "").strip().lower()
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    return None


def _settings_flag() -> bool:
    """debugWindows from the settings file on disk (cached by mtime)."""
    try:
        mtime = SETTINGS_FILE.stat().st_mtime
    except OSError:
        return False
    now = time.time()
    if _CACHE["mtime"] == mtime and (now - _CACHE["at"]) < _CACHE_TTL:
        return bool(_CACHE["value"])
    flag = False
    try:
        data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        flag = bool(data.get(SETTINGS_KEY, False)) if isinstance(data, dict) else False
    except (OSError, ValueError):
        flag = False
    _CACHE.update(at=now, mtime=mtime, value=flag)
    return flag


def debug_windows(settings: dict | None = None) -> bool:
    """True when popup windows stay visible (debug mode). Default False."""
    override = _env_override()
    if override is not None:
        return override
    if settings is not None:
        try:
            return bool(settings.get(SETTINGS_KEY, False))
        except Exception:
            return False
    return _settings_flag()


def kwargs(settings: dict | None = None) -> dict:
    """Extra subprocess kwargs that hide console windows on Windows.

    Only STARTUPINFO SW_HIDE is used for short-lived captured commands:
    CREATE_NO_WINDOW makes console tools like tasklist misbehave (empty
    match yields stdout=None plus a reader-thread decode error), while a
    hidden startup window suppresses the flash and keeps pipes intact.

    Empty when debug mode is on (status quo: windows may flash) or off
    Windows, so call sites can unconditionally splat the result.
    """
    if os.name != "nt" or debug_windows(settings):
        return {}
    try:
        info = subprocess.STARTUPINFO()
        info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        info.wShowWindow = subprocess.SW_HIDE
        return {"startupinfo": info}
    except Exception:
        return {}


def service_creationflags(settings: dict | None = None) -> int:
    """creationflags for long-lived owned services (Ollama, renderer daemon).

    Headless keeps a *hidden console* (CREATE_NO_WINDOW) instead of detaching:
    a detached service owns no console, so every console tool it spawns —
    Ollama's `gpu-discover` and `llama-server --list-devices` probes on entry —
    is handed a brand-new Windows Terminal window that steals focus. With a
    hidden console the grandchildren inherit it and stay invisible.

    Debug keeps the historical detached process (visible popups welcome).
    """
    if os.name != "nt":
        return 0
    if debug_windows(settings):
        return CREATE_NEW_PROCESS_GROUP | DETACHED_PROCESS
    return CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW


def with_kwargs(base: dict | None = None, settings: dict | None = None) -> dict:
    """Merge headless kwargs into an explicit kwarg dict (OR-ing flags).

    Debug mode returns `base` unchanged (status quo); headless mode keeps
    the caller's flags and only adds hiding.
    """
    kw = dict(base or {})
    extra = kwargs(settings)
    if not extra:
        return kw
    if "creationflags" in kw and "creationflags" in extra:
        kw["creationflags"] = kw["creationflags"] | extra["creationflags"]
    elif "creationflags" in extra:
        kw.setdefault("creationflags", extra["creationflags"])
    if extra.get("startupinfo") is not None:
        kw.setdefault("startupinfo", extra["startupinfo"])
    return kw


def windowless_python(exe: str, settings: dict | None = None) -> str:
    """pythonw.exe sibling of `exe` when headless; `exe` unchanged in debug.

    Console grandchildren (the renderer's MCP server is spawned by the
    Codex harness, which inherits none of our creation flags) would
    otherwise each open a visible console window. The windowless
    interpreter has no console at all; stdio stays redirected, so stdio
    transports such as MCP are unaffected. Falls back to `exe` when the
    sibling is missing or anything goes wrong.
    """
    try:
        if os.name != "nt" or debug_windows(settings):
            return exe
        candidate = Path(exe).with_name("pythonw.exe")
        if candidate.is_file():
            return str(candidate)
    except Exception:
        pass
    return exe
