"""Owned background services for the TUI: auto-start on entry, auto-stop on exit.

Rules (never violated):
- Only processes this TUI started itself are ever stopped. Ownership is proven
  by `runtime/tui/services.json` (session id + pid + image name) re-verified
  at release time. Foreign processes (pre-existing Ollama/renderer, SSH
  tunnels, harness children, another live TUI session) are never touched.
- Auto-start is Local-loopback only. H200 endpoints are never started, probed
  intrusively, or guessed; a dead tunnel is reported with its fix.
- Models are never downloaded (`ollama pull` stays a manual step).
- Leaving the TUI leaves nothing behind: the live agent run is stopped
  cooperatively (evidence stays complete), whatever survives is killed, and
  the services this TUI started are stopped with it. The "Stop owned services"
  button alone refuses while a run is live (that run still needs them).
- A stale record (previous TUI died) is adopted only after its heartbeat went
  stale; a fresh heartbeat means another live TUI owns them (contested).
"""
from __future__ import annotations

import json
import os
import secrets
import shutil
import signal
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

# Result of the one shutdown this process performs; repeated calls (quit path
# plus the safety net in main()) must not stop anything twice.
_SHUTDOWN_REPORT: dict | None = None

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


# Images the TUI itself may have spawned. Used only for descendants of this
# process, so an unrelated program with the same image name is never matched.
_OWNED_IMAGES = ("python.exe", "pythonw.exe", "codex.exe", "node.exe")
_LAUNCHER_IMAGES = ("python.exe", "pythonw.exe", "py.exe")
_HARNESS_IMAGES = ("codex.exe",)


def _block_interrupts() -> None:
    """Make the shutdown path immune to a second Ctrl+C."""
    try:
        signal.signal(signal.SIGINT, signal.SIG_IGN)
    except (ValueError, OSError, AttributeError):
        pass


def block_interrupts() -> None:
    """Ignore SIGINT for the rest of the process (call from the main thread).

    The shutdown must not be aborted half way: a second Ctrl+C would leave
    the agent tree and the local Ollama running.
    """
    _block_interrupts()


def _descendant_pids(root_pid: int) -> list[int]:
    """Live descendant PIDs of `root_pid` via a Toolhelp snapshot (Windows)."""
    if os.name != "nt":
        return []
    try:
        import ctypes
        from ctypes import wintypes
    except Exception:
        return []

    class Entry(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
        ]

    kernel32 = ctypes.windll.kernel32
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Entry)]
    kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Entry)]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    snapshot = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)
    invalid = wintypes.HANDLE(-1).value
    if not snapshot or snapshot == invalid:
        return []
    parents: dict[int, int] = {}
    entry = Entry()
    entry.dwSize = ctypes.sizeof(Entry)
    try:
        more = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while more:
            parents[int(entry.th32ProcessID)] = int(entry.th32ParentProcessID)
            more = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)
    children: dict[int, list[int]] = {}
    for pid, parent in parents.items():
        children.setdefault(parent, []).append(pid)
    found: list[int] = []
    stack = list(children.get(root_pid, []))
    while stack:
        pid = stack.pop()
        found.append(pid)
        stack.extend(children.get(pid, []))
    return found


def _pids_with_images(prefixes: tuple[str, ...]) -> list[int]:
    """Live PIDs whose image name starts with one of `prefixes` (Windows only)."""
    if os.name != "nt":
        return []
    try:
        out = subprocess.run(["tasklist", "/FO", "CSV", "/NH"],
                             capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return []
    pids: list[int] = []
    for line in out.stdout.splitlines():
        parts = [piece.strip().strip('"') for piece in line.split('","')]
        if len(parts) < 2 or not parts[1].isdigit():
            continue
        image = parts[0].lower()
        if any(image.startswith(prefix) for prefix in prefixes):
            pids.append(int(parts[1]))
    return pids


def _listener_pids(port: int) -> list[int]:
    """PIDs listening on a TCP `port` (netstat is present on every Windows box)."""
    if os.name != "nt":
        return []
    try:
        out = subprocess.run(["netstat", "-ano", "-p", "tcp"],
                             capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return []
    pids: set[int] = set()
    for line in out.stdout.splitlines():
        fields = line.split()
        if len(fields) < 5 or fields[0].upper() != "TCP" or fields[3].upper() != "LISTENING":
            continue
        local, pid = fields[1], fields[4]
        if local.rsplit(":", 1)[-1] == str(port) and pid.isdigit():
            pids.add(int(pid))
    return sorted(pids)


def _endpoint_up(endpoint: str, timeout: float = 1.0) -> bool:
    try:
        _http_get(endpoint.rstrip("/") + "/api/version", timeout)
        return True
    except Exception:
        return False


def _recorded_run_pids() -> list[tuple[int, tuple[str, ...]]]:
    """Launcher/harness PIDs from lock files, for trees the snapshot missed."""
    found: list[tuple[int, tuple[str, ...]]] = []
    try:
        files = list((ROOT / "runtime" / "locks").glob("*.lock"))
    except OSError:
        return found
    for path in files:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        launcher = data.get("pid")
        if isinstance(launcher, int) and process_alive(launcher):
            found.append((launcher, _LAUNCHER_IMAGES))
        harness = data.get("harnessPid")
        if isinstance(harness, int) and process_alive(harness):
            found.append((harness, _HARNESS_IMAGES))
    return found


def _manifest_run_pids() -> list[tuple[int, tuple[str, ...]]]:
    """Harness PIDs recorded by live runs when their launcher already exited."""
    found: list[tuple[int, tuple[str, ...]]] = []
    try:
        entries = runs_mod.discover_runs(limit=50, _fresh=True)
    except Exception:
        return found
    for entry in entries:
        if entry.get("status") != "Running":
            continue
        pid = (entry.get("manifest") or {}).get("pid")
        if isinstance(pid, int) and process_alive(pid):
            found.append((pid, _HARNESS_IMAGES))
    return found


def _terminate_agent_trees(descendants: list[int] | None = None) -> dict:
    """Kill this TUI's launcher/harness/MCP trees (agent first, never the TUI).

    `descendants` is the snapshot taken before the cooperative stop: children
    whose parent dies first (the codex MCP server) would otherwise be orphaned
    and survive the shutdown.
    """
    report: dict = {"stopped": [], "failed": []}
    mine = os.getpid()
    if descendants is None:
        descendants = _descendant_pids(mine)
    else:
        descendants = [pid for pid in descendants if pid != mine]
    try:
        import runner as runner_mod
        report["stopped"].extend("launcher pid %d" % pid for pid in runner_mod.stop_active())
    except Exception as error:  # noqa: BLE001 - shutdown must continue
        report["failed"].append("launcher: %s" % error)
    killed: set[int] = set()
    for pid in descendants:
        if pid == mine:
            continue
        image = (_image_of_pid(pid) or "").lower()
        if image in _OWNED_IMAGES:
            if _terminate(pid):
                killed.add(pid)
                report["stopped"].append("%s pid %d" % (image, pid))
    # Anything from the snapshot that outlived its parent (MCP servers).
    for pid in descendants:
        if pid == mine or pid in killed:
            continue
        if not process_alive(pid):
            continue
        image = (_image_of_pid(pid) or "").lower()
        if _terminate(pid):
            report["stopped"].append("%s pid %d (left over)" % (image or "process", pid))
        else:
            report["failed"].append("%s pid %d (terminate failed)" % (image or "process", pid))
    seen: set[int] = set()
    for pid, images in _recorded_run_pids() + _manifest_run_pids():
        if pid in seen or pid in killed:
            continue
        seen.add(pid)
        if not process_alive(pid):
            continue
        image = (_image_of_pid(pid) or "").lower()
        if image not in images:
            continue
        if _terminate(pid):
            report["stopped"].append("%s pid %d" % (image, pid))
        else:
            report["failed"].append("%s pid %d (terminate failed)" % (image, pid))
    return report


def terminate_local_inference(settings: dict | None = None) -> dict:
    """Hard-stop the Local Ollama server so the model is unloaded from VRAM.

    Ctrl+C is a full stop: every `ollama*` image is terminated (server, model
    runner, tray app), including an instance that predated this TUI and was
    only "reused". H200 endpoints are remote and are never touched.
    """
    report: dict = {"stopped": [], "failed": [], "endpoint": None, "verified": None}
    settings = settings or config_mod.load_settings()
    if settings.get("mode") != "Local":
        return report
    parsed = _loopback(settings.get("endpoint", ""))
    if parsed is None:
        return report
    endpoint = settings["endpoint"]
    report["endpoint"] = endpoint
    pids = set(_pids_with_images(("ollama",)))
    for pid in _listener_pids(parsed[1]):
        if (_image_of_pid(pid) or "").lower().startswith("ollama"):
            pids.add(pid)
    if not pids:
        report["verified"] = not _endpoint_up(endpoint)
        return report
    for pid in sorted(pids):
        image = (_image_of_pid(pid) or "").lower()
        if not image.startswith("ollama"):
            continue  # never kill a foreign listener on the configured port
        if _terminate(pid):
            report["stopped"].append("%s pid %d" % (image, pid))
        else:
            report["failed"].append("%s pid %d (terminate failed)" % (image, pid))
    deadline = time.time() + 5.0
    while time.time() < deadline and _endpoint_up(endpoint):
        time.sleep(0.2)
    report["verified"] = not _endpoint_up(endpoint)
    if not report["verified"]:
        report["failed"].append("inference still answering on %s after kill" % endpoint)
    return report


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


def start_temporary_ollama(settings: dict, context_size: int) -> dict:
    """Start a TUI-owned Ollama with a *temporary* OLLAMA_CONTEXT_LENGTH.

    Used when the user's live Ollama is reachable but loaded a different
    context: instead of asking the user to restart their service by hand, the
    TUI spawns a second loopback instance on a free port with the required
    context, registers it in the services record (reaped on TUI exit like any
    owned service), and the caller points only this run's in-memory config at
    it. Global settings file and the user's own Ollama are never touched.
    Returns {"ok": True, "endpoint", "pid", "key"} or {"ok": False, "error"}.
    """
    from socket import socket as _socket

    host = "127.0.0.1"
    try:
        probe = _socket()
        probe.bind((host, 0))
        port = probe.getsockname()[1]
        probe.close()
    except OSError as error:
        return {"ok": False, "error": "no free loopback port: %s" % error}
    endpoint = "http://%s:%d" % (host, port)
    exe = shutil.which("ollama.exe") or shutil.which("ollama")
    if not exe:
        return {"ok": False, "error": "no ollama binary on PATH; the TUI cannot invent it"}
    env = dict(os.environ)
    env.update(OLLAMA_HOST="%s:%d" % (host, port), OLLAMA_CONTEXT_LENGTH=str(context_size),
               OLLAMA_NO_CLOUD="1", OLLAMA_NUM_PARALLEL="1", OLLAMA_MAX_LOADED_MODELS="1",
               OLLAMA_FLASH_ATTENTION="1", OLLAMA_KV_CACHE_TYPE="q8_0")
    try:
        pid = _spawn([exe, "serve"], env, ROOT, "ollama-tmp-%d" % port)
    except Exception as error:
        return {"ok": False, "error": "auto-start failed: %s" % error}
    if not _wait_true(lambda: _http_get(endpoint.rstrip("/") + "/api/version", 1.0), 20.0):
        return {"ok": False, "error": "temporary instance on %s never became reachable (see runtime/tui/ollama-tmp-%d-stderr.log)" % (endpoint, port)}
    record = _read_record()
    now = time.time()
    live_recorded = [entry for entry in (record.get("services") or {}).values()
                     if isinstance(entry.get("pid"), int) and process_alive(entry["pid"])]
    if record.get("owner") not in (None, SESSION_ID) and live_recorded and (now - record.get("heartbeat", 0)) < HEARTBEAT_TTL:
        _terminate(pid)
        return {"ok": False, "error": "another live TUI session owns the background services; use that window"}
    services = dict(record.get("services") or {})
    key = "ollama-tmp-%d" % port
    services[key] = {"pid": pid, "image": "ollama.exe", "endpoint": endpoint,
                     "temporary": True, "contextLength": context_size}
    _write_record({"owner": SESSION_ID, "heartbeat": now, "services": services})
    return {"ok": True, "endpoint": endpoint, "pid": pid, "key": key}


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


def service_status(settings: dict | None = None) -> dict:
    """Read-only state of the local inference and renderer endpoints.

    The Dashboard shows it. It never starts, stops or changes a service, and
    it never asks the model anything.
    """
    settings = settings or config_mod.load_settings()
    status = {"inference": "n/a", "renderer": "n/a", "owned": owned_summary()}
    endpoint = str((settings.get("h200Endpoint") if settings.get("mode") == "H200"
                    else settings.get("endpoint")) or "")
    if endpoint:
        status["inference"] = "up" if _endpoint_up(endpoint) else "down"
    service = str(settings.get("rendererService") or "")
    if service:
        try:
            identity = _http_get(service.rstrip("/") + "/api/service", 1.5)
            status["renderer"] = ("up" if identity.get("service") == "gav-native-renderer"
                                  else "foreign service")
        except Exception:
            status["renderer"] = "down"
    return status


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
    thread, _hb_thread = _hb_thread, None
    if thread is not None:
        try:
            thread.join(timeout=2.0)
        except RuntimeError:
            pass


def _live_runs() -> list[str]:
    try:
        found = runs_mod.discover_runs(limit=50, _fresh=True)
    except Exception:
        return []
    return [r.get("runId", "?") for r in found if runs_mod.is_live(r)]


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


def release_owned_services(force: bool = False) -> dict:
    """Stop exactly our own services; refuse while any agent/launcher is active.

    `force=True` is used by the shutdown path after the agent tree has already
    been terminated: the "live run" gate is then meaningless (the evidence on
    disk still says Running) and must not keep Ollama loaded.
    """
    report: dict = {"stopped": [], "kept": None, "skipped": []}
    record = _read_record()
    if record.get("owner") != SESSION_ID or not record.get("services"):
        return report
    if not force:
        blockers = _live_runs() + _live_launchers()
        if blockers:
            report["kept"] = ("%s is still live and uses them — stop it first "
                              "(Live: Stop run); owned services left running" % blockers[0])
            return report
    remaining = {}
    for kind, entry in record["services"].items():
        pid = entry.get("pid")
        if not (isinstance(pid, int) and process_alive(pid)):
            # Already gone (the agent-tree kill may have taken it with it).
            if isinstance(pid, int):
                report["stopped"].append("%s pid %d (already gone)" % (kind, pid))
            continue
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


def request_run_stops() -> list[str]:
    """Ask every live run to stop cooperatively.

    The launcher polls `runtime/tui-control/<runId>.stop` and then records
    `interrupted` + `failure.json`, so the evidence stays complete instead of
    being cut in half by a kill. Returns the run ids that were asked to stop.
    """
    import runner as runner_mod
    requested: list[str] = []
    try:
        entries = runs_mod.discover_runs(limit=50, _fresh=True)
    except Exception:
        entries = []
    for entry in entries:
        if not runs_mod.is_live(entry):
            continue
        run_id = str(entry.get("runId") or "")
        if not run_id:
            continue
        try:
            runner_mod.request_stop(run_id)
            requested.append(run_id)
        except Exception:
            continue
    return requested


def _wait_for_launchers(timeout: float = 6.0) -> list[str]:
    """Wait for the launchers that were asked to stop to disappear."""
    deadline = time.time() + timeout
    while True:
        remaining = _live_launchers()
        if not remaining or time.time() >= deadline:
            return remaining
        time.sleep(0.25)

def _owns_ollama() -> bool:
    """True when this TUI started the local Ollama (it is in our record)."""
    record = _read_record()
    for entry in (record.get("services") or {}).values():
        if isinstance(entry, dict) and str(entry.get("image", "")).lower().startswith("ollama"):
            return True
    return False


def _inference_state(settings: dict | None = None, owned: bool = False) -> dict:
    """Report (never kill) the local inference endpoint state."""
    settings = settings or config_mod.load_settings()
    report: dict = {"endpoint": None, "verified": None, "owned": bool(owned)}
    if settings.get("mode") != "Local":
        return report
    endpoint = settings.get("endpoint", "")
    if _loopback(endpoint) is None:
        return report
    report["endpoint"] = endpoint
    report["verified"] = not _endpoint_up(endpoint)
    return report


def shutdown_all(settings: dict | None = None, hard: bool = False) -> dict:
    """Leave no process behind: agent run -> local Ollama -> owned services.

    The live runs are asked to stop first (cooperative stop, so the launcher
    still records `interrupted`/`failure.json`); whatever survives that grace
    period is killed, including children that outlived their parent. Then the
    local Ollama and the services this TUI started are stopped.

    `hard=True` (the Ctrl+C path) additionally kills every `ollama*` process,
    including one that predates this TUI. The normal quit only stops what
    this TUI started and reports anything foreign that stays alive.

    The result is cached: the quit path and the safety net in `main()` must
    not stop the same process twice.
    """
    global _SHUTDOWN_REPORT
    if _SHUTDOWN_REPORT is not None:
        return _SHUTDOWN_REPORT
    _block_interrupts()
    heartbeat_stop()
    report: dict = {"agent": [], "stopped": [], "failed": [], "kept": [], "inference": {},
                    "requested": [], "remaining": []}
    # Snapshot the children before anything dies: a child whose parent is
    # killed first (the codex MCP server) would otherwise be orphaned.
    descendants = _descendant_pids(os.getpid())
    report["requested"] = request_run_stops()
    if report["requested"]:
        # Give the launcher time to record the interrupted evidence.
        report["remaining"] = _wait_for_launchers()
    else:
        report["remaining"] = _live_launchers()
    agent = _terminate_agent_trees(descendants)
    report["agent"] = agent["stopped"]
    report["failed"].extend(agent["failed"])
    owned_ollama = _owns_ollama()
    if hard:
        # Same order as the documented hard stop: inference first, then the
        # remaining owned services.
        inference = terminate_local_inference(settings)
        report["inference"] = inference
        report["stopped"].extend(inference.get("stopped", []))
        report["failed"].extend(inference.get("failed", []))
    release = release_owned_services(force=True)
    report["stopped"].extend(release["stopped"])
    report["kept"].extend(release["skipped"])
    if release.get("kept"):
        report["kept"].append(release["kept"])
    if not hard:
        inference = _inference_state(settings, owned=owned_ollama)
        report["inference"] = inference
        if inference.get("endpoint") and not inference.get("verified") and not inference.get("owned"):
            report["kept"].append("local Ollama at %s still runs (not started by this TUI, "
                                  "left alone)" % inference["endpoint"])
    _SHUTDOWN_REPORT = report
    return report


def shutdown_lines(report: dict) -> list[str]:
    lines = []
    for run_id in report.get("requested", []):
        lines.append("[stop requested] run %s (the launcher records interrupted/failure.json)" % run_id)
    for stopped in report.get("stopped", []):
        lines.append("[stopped] %s" % stopped)
    for kept in report.get("kept", []):
        lines.append("[kept] %s" % kept)
    inference = report.get("inference") or {}
    if inference.get("endpoint"):
        if inference.get("verified"):
            state = "verified down"
        elif inference.get("owned"):
            state = "STILL ANSWERING — check manually"
        else:
            state = "still running (not started by this TUI)"
        lines.append("[inference] local Ollama at %s: %s" % (inference["endpoint"], state))
    for remaining in report.get("remaining", []):
        lines.append("[note] launcher was still alive after the stop request and was killed: %s" % remaining)
    for failed in report.get("failed", []):
        lines.append("[failed] %s" % failed)
    return lines


def release_lines(report: dict) -> list[str]:
    lines = []
    for stopped in report.get("stopped", []):
        lines.append("[stopped] %s" % stopped)
    for skipped in report.get("skipped", []):
        lines.append("[kept] %s" % skipped)
    if report.get("kept"):
        lines.append("[kept] %s" % report["kept"])
    return lines


def service_logs(limit_lines: int = 40) -> list[tuple[str, list[str]]]:
    """Tail of owned-service stdout/stderr logs (read-only, newest lines)."""
    out: list[tuple[str, list[str]]] = []
    try:
        files = sorted(_logdir().glob("*.log"))
    except OSError:
        return out
    for path in files[-8:]:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        lines = text.splitlines()[-limit_lines:]
        out.append((path.name, lines))
    return out


def owned_services() -> list[dict]:
    """Owned services with liveness for the Services workspace."""
    record = _read_record()
    services = record.get("services") or {}
    out = []
    for kind, entry in services.items():
        pid = entry.get("pid")
        alive = isinstance(pid, int) and process_alive(pid) and _image_of_pid(pid) == entry.get("image")
        out.append({"kind": kind, "pid": pid, "image": entry.get("image"),
                    "alive": bool(alive), "owned": record.get("owner") == SESSION_ID,
                    "endpoint": entry.get("endpoint") or entry.get("service") or "",
                    "temporary": bool(entry.get("temporary"))})
    return out
