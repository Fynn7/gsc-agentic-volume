"""Owned background services for the TUI: auto-start on entry, auto-stop on exit.

Rules (never violated):
- Only processes this TUI started itself are ever stopped. Ownership is proven
  by `runtime/tui/services.json` (session id + pid + image name) re-verified
  at release time. Foreign processes (pre-existing Ollama/renderer, SSH
  tunnels, harness children, another live TUI session) are never touched.
- Auto-start is Local-loopback only. H200 endpoints are never started, probed
  intrusively, or guessed; a dead tunnel is reported with its fix.
- Models are never downloaded (`ollama pull` stays a manual step).
- Release is refused while any agent run or launcher is still active; owned
  services stay up and the record is kept for a later session to adopt.
- A stale record (previous TUI died) is adopted only after its heartbeat went
  stale; a fresh heartbeat means another live TUI owns them (contested).
"""
from __future__ import annotations

import json
import os
import secrets
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import config as config_mod
import runs as runs_mod
from run_lifecycle import process_alive

SESSION_ID = secrets.token_hex(8)
RECORD = ROOT / "runtime" / "tui" / "services.json"
HEARTBEAT_TTL = 90.0

_CREATE_FLAGS = 0x00000008 | 0x00000200 | 0x08000000 if os.name == "nt" else 0


def _logdir() -> Path:
    return RECORD.parent


def _read_record() -> dict:
    try:
        data = json.loads(RECORD.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_record(data: dict) -> None:
    RECORD.parent.mkdir(parents=True, exist_ok=True)
    temporary = RECORD.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(str(temporary), str(RECORD))


def _http_get(url: str, timeout: float = 5.0):
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.load(response)


def _spawn(cmd: list[str], env: dict, cwd: Path, stem: str) -> int:
    """Detached background start; stdio goes to owned log files, never the TUI."""
    _logdir().mkdir(parents=True, exist_ok=True)
    out = (_logdir() / ("%s-stdout.log" % stem)).open("ab")
    err = (_logdir() / ("%s-stderr.log" % stem)).open("ab")
    try:
        proc = subprocess.Popen(cmd, env=env, cwd=str(cwd), stdin=subprocess.DEVNULL,
                                stdout=out, stderr=err, creationflags=_CREATE_FLAGS)
        return proc.pid
    finally:
        out.close()
        err.close()


def _wait_true(probe, timeout: float, interval: float = 0.25) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            probe()
            return True
        except Exception:
            time.sleep(interval)
    return False


def _image_of_pid(pid: int) -> str | None:
    """Image name (e.g. ollama.exe) for ownership re-verification. None if gone."""
    if os.name != "nt":
        return None
    try:
        out = subprocess.run(["tasklist", "/FI", "PID eq %d" % pid, "/FO", "CSV", "/NH"],
                             capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return None
    for line in out.stdout.splitlines():
        parts = [piece.strip().strip('"') for piece in line.split('","')]
        if len(parts) >= 2 and parts[1] == str(pid) and parts[0]:
            return parts[0].lower()
    return None


def _terminate(pid: int) -> bool:
    try:
        if os.name == "nt":
            done = subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                                  capture_output=True, timeout=30)
            return done.returncode == 0
        os.kill(pid, 15)
        return True
    except (OSError, subprocess.SubprocessError):
        return False


def _loopback(url: str) -> tuple[str, int] | None:
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    if parts.hostname not in ("127.0.0.1", "localhost") or not parts.port:
        return None
    return parts.hostname, parts.port


def _ensure_ollama(settings: dict, owned: dict, report: dict) -> None:
    endpoint = settings.get("endpoint", "")
    parsed = _loopback(endpoint)
    if parsed is None:
        report["problems"].append({"name": "Inference %s" % endpoint, "ok": False,
                                   "detail": "non-loopback endpoints are never auto-started",
                                   "fix": "Start your inference service manually; the TUI only starts 127.0.0.1/localhost."})
        return
    host, port = parsed
    try:
        _http_get(endpoint.rstrip("/") + "/api/version", 3.0)
        report["reused"].append("ollama %s (already running, left untouched)" % endpoint)
        return
    except Exception:
        pass
    exe = shutil.which("ollama.exe") or shutil.which("ollama")
    if not exe:
        report["problems"].append({"name": "Inference %s" % endpoint, "ok": False,
                                   "detail": "unreachable and no ollama binary on PATH",
                                   "fix": "Install Ollama, or start it yourself; the TUI cannot invent it."})
        return
    context = config_mod.example_config().get("contextSize", 16384)
    env = dict(os.environ)
    env.update(OLLAMA_HOST="%s:%d" % (host, port), OLLAMA_CONTEXT_LENGTH=str(context),
               OLLAMA_NO_CLOUD="1", OLLAMA_NUM_PARALLEL="1", OLLAMA_MAX_LOADED_MODELS="1",
               OLLAMA_FLASH_ATTENTION="1", OLLAMA_KV_CACHE_TYPE="q8_0")
    try:
        pid = _spawn([exe, "serve"], env, ROOT, "ollama")
    except Exception as error:
        report["problems"].append({"name": "Inference %s" % endpoint, "ok": False,
                                   "detail": "auto-start failed: %s" % error,
                                   "fix": "Start `ollama serve` yourself (see runtime/tui/ollama-stderr.log)."})
        return
    if _wait_true(lambda: _http_get(endpoint.rstrip("/") + "/api/version", 1.0), 10.0):
        owned["ollama"] = {"pid": pid, "image": "ollama.exe"}
        report["started"].append("ollama %s (pid %d, TUI-owned)" % (endpoint, pid))
    else:
        report["problems"].append({"name": "Inference %s" % endpoint, "ok": False,
                                   "detail": "started pid %d but never became reachable" % pid,
                                   "fix": "Read runtime/tui/ollama-stderr.log; port %d may be held by another app." % port})


def _ensure_renderer(settings: dict, owned: dict, report: dict) -> None:
    service = settings.get("rendererService", "")
    parsed = _loopback(service)
    if parsed is None:
        report["problems"].append({"name": "Renderer %s" % service, "ok": False,
                                   "detail": "non-loopback renderers are never auto-started",
                                   "fix": "The renderer must stay on this Windows machine (127.0.0.1/localhost)."})
        return
    _host, port = parsed
    try:
        identity = _http_get(service.rstrip("/") + "/api/service", 3.0)
        if identity.get("service") == "gav-native-renderer":
            report["reused"].append("renderer %s (already running, left untouched)" % service)
            return
        report["problems"].append({"name": "Renderer %s" % service, "ok": False,
                                   "detail": "port answers but is not this application: %s" % str(identity)[:100],
                                   "fix": "Port conflict: free %d or point rendererService at this app; nothing was killed." % port})
        return
    except Exception:
        pass
    python = runs_mod._python_for_verify()
    try:
        pid = _spawn([python, "-B", str(ROOT / "src" / "app.py"), "--port", str(port)],
                     dict(os.environ), ROOT, "renderer")
    except Exception as error:
        report["problems"].append({"name": "Renderer %s" % service, "ok": False,
                                   "detail": "auto-start failed: %s" % error,
                                   "fix": "Start it yourself: python -B src/app.py --port %d (see runtime/tui/renderer-stderr.log)." % port})
        return

    def healthy():
        identity = _http_get(service.rstrip("/") + "/api/service", 1.0)
        if identity.get("service") != "gav-native-renderer":
            raise ValueError("foreign service on port %d" % port)

    if _wait_true(healthy, 10.0):
        owned["renderer"] = {"pid": pid, "image": "python.exe", "service": service}
        report["started"].append("renderer %s (pid %d, TUI-owned)" % (service, pid))
    else:
        report["problems"].append({"name": "Renderer %s" % service, "ok": False,
                                   "detail": "started pid %d but never identified as gav-native-renderer" % pid,
                                   "fix": "Read runtime/tui/renderer-stderr.log; port %d may be held by another app." % port})


def model_problems(settings: dict) -> list[dict]:
    """Missing-model / context-mismatch problems. Never downloads anything."""
    endpoint = settings["h200Endpoint"] if settings.get("mode") == "H200" else settings.get("endpoint", "")
    model = settings.get("model", "")
    try:
        tags = _http_get(endpoint.rstrip("/") + "/api/tags", 10.0)
    except Exception:
        return []  # unreachable is already reported elsewhere
    names = [m.get("name") for m in tags.get("models", []) if isinstance(m, dict)]
    if model not in names:
        return [{"name": "Model %s" % model, "ok": False,
                 "detail": "not present on %s (has: %s)" % (endpoint, ", ".join(names[:8]) or "none"),
                 "fix": "Run `ollama pull %s` against that endpoint first; the TUI never downloads models by itself." % model}]
    if settings.get("mode") != "Local":
        return []
    try:
        seen = _http_get(endpoint.rstrip("/") + "/api/ps", 5.0)
    except Exception:
        return []
    want = config_mod.example_config().get("contextSize")
    problems = []
    for entry in seen.get("models", []):
        if entry.get("name") == model and entry.get("context_length") not in (None, want):
            problems.append({"name": "Model context", "ok": False,
                             "detail": "loaded %s context %s != required %s" % (model, entry.get("context_length"), want),
                             "fix": "Restart Ollama with OLLAMA_CONTEXT_LENGTH=%s; the TUI never kills a live service." % want})
    return problems


def ensure_services(settings: dict) -> dict:
    """Start missing local services, adopt stale records, report honestly."""
    report: dict = {"started": [], "reused": [], "adopted": [], "problems": [],
                    "contested": False, "owns": False}
    record = _read_record()
    now = time.time()
    live_recorded = [entry for entry in (record.get("services") or {}).values()
                     if isinstance(entry.get("pid"), int) and process_alive(entry["pid"])]
    if record.get("owner") not in (None, SESSION_ID) and live_recorded and (now - record.get("heartbeat", 0)) < HEARTBEAT_TTL:
        report["contested"] = True
        report["problems"].append({"name": "Services", "ok": False,
                                   "detail": "Another live TUI session owns the background services.",
                                   "fix": "Use the other TUI window, or close it and re-enter; this session starts/stops nothing."})
        return report
    owned: dict = {}
    for kind, entry in (record.get("services") or {}).items():
        pid = entry.get("pid")
        if isinstance(pid, int) and process_alive(pid) and _image_of_pid(pid) == entry.get("image"):
            owned[kind] = entry
    if owned:
        report["adopted"] = sorted(owned)
    if settings.get("mode") == "Local":
        _ensure_ollama(settings, owned, report)
    else:
        try:
            _http_get(settings.get("h200Endpoint", "").rstrip("/") + "/api/version", 5.0)
            report["reused"].append("h200 tunnel %s (user-owned, left untouched)" % settings.get("h200Endpoint"))
        except Exception as error:
            report["problems"].append({"name": "H200 %s" % settings.get("h200Endpoint"), "ok": False,
                                       "detail": str(error)[:160],
                                       "fix": "Open the documented SSH tunnel first; the TUI never dials out or guesses accounts."})
    _ensure_renderer(settings, owned, report)
    report["problems"].extend(model_problems(settings))
    report["owns"] = bool(owned)
    _write_record({"owner": SESSION_ID, "heartbeat": time.time(), "services": owned})
    try:
        import env as env_mod
        env_mod._CHECK_CACHE.update(at=0.0, key=None, value=None)
    except Exception:
        pass
    return report


def report_lines(report: dict) -> list[str]:
    lines = []
    for started in report.get("started", []):
        lines.append("[started] %s" % started)
    for reused in report.get("reused", []):
        lines.append("[reused] %s" % reused)
    for adopted in report.get("adopted", []):
        lines.append("[adopted] %s (stale record, verified still ours)" % adopted)
    for problem in report.get("problems", []):
        lines.append("[DOWN] %s: %s" % (problem.get("name"), (problem.get("detail") or "")[:140]))
        if problem.get("fix"):
            lines.append("         fix: %s" % problem["fix"])
    return lines


def owned_summary() -> str:
    record = _read_record()
    if record.get("owner") != SESSION_ID or not record.get("services"):
        return "foreign/manual"
    alive = [kind for kind, entry in record["services"].items()
             if isinstance(entry.get("pid"), int) and process_alive(entry["pid"])]
    return "TUI-owned (%s)" % ", ".join(sorted(alive)) if alive else "TUI-owned (exited)"


_hb_stop = threading.Event()
_hb_thread: threading.Thread | None = None


def heartbeat_start() -> None:
    global _hb_thread
    if _hb_thread is not None:
        return
    _hb_stop.clear()

    def loop():
        while not _hb_stop.wait(30.0):
            try:
                record = _read_record()
                if record.get("owner") == SESSION_ID:
                    record["heartbeat"] = time.time()
                    _write_record(record)
            except Exception:
                pass

    thread = threading.Thread(target=loop, daemon=True)
    thread.start()
    _hb_thread = thread


def heartbeat_stop() -> None:
    global _hb_thread
    _hb_stop.set()
    _hb_thread = None


def _live_runs() -> list[str]:
    try:
        found = runs_mod.discover_runs(limit=50, _fresh=True)
    except Exception:
        return []
    return [r.get("runId", "?") for r in found if r.get("status") == "Running"]


def _live_launchers() -> list[str]:
    hits = []
    try:
        files = list((ROOT / "runtime" / "locks").glob("*.lock"))
    except OSError:
        return []
    for path in files:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        for field in ("pid", "harnessPid"):
            if process_alive(data.get(field)):
                hits.append("%s:%s" % (path.name, field))
                break
    return hits


def release_owned_services() -> dict:
    """Stop exactly our own services; refuse while any agent/launcher is active."""
    report: dict = {"stopped": [], "kept": None, "skipped": []}
    record = _read_record()
    if record.get("owner") != SESSION_ID or not record.get("services"):
        return report
    blockers = _live_runs() + _live_launchers()
    if blockers:
        report["kept"] = "activity still live (%s); owned services left running" % blockers[0]
        return report
    remaining = {}
    for kind, entry in record["services"].items():
        pid = entry.get("pid")
        if not (isinstance(pid, int) and process_alive(pid)):
            continue  # already gone; drop silently
        if _image_of_pid(pid) != entry.get("image"):
            report["skipped"].append("%s pid %d (image no longer ours, left alone)" % (kind, pid))
            continue
        if _terminate(pid):
            report["stopped"].append("%s pid %d" % (kind, pid))
        else:
            report["skipped"].append("%s pid %d (terminate failed)" % (kind, pid))
            remaining[kind] = entry
    _write_record({"owner": SESSION_ID, "heartbeat": time.time(), "services": remaining})
    return report


def release_lines(report: dict) -> list[str]:
    lines = []
    for stopped in report.get("stopped", []):
        lines.append("[stopped] %s" % stopped)
    for skipped in report.get("skipped", []):
        lines.append("[kept] %s" % skipped)
    if report.get("kept"):
        lines.append("[kept] %s" % report["kept"])
    return lines
