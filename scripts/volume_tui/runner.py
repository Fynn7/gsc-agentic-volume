"""Launch helper: delegates 100% of lifecycle to scripts/run_local_agent.py.

The TUI never reserves directories, writes locks, or picks Run IDs. It only
writes a plain input config file, spawns the launcher, and reads back the
`run_started` line (runId + directory). Concurrency errors (exit 3) are
surfaced verbatim; no competing lock scheme exists here.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from pathlib import Path

import subproc as subproc_mod

ROOT = Path(__file__).resolve().parents[2]
CONTROL_DIR = ROOT / "runtime" / "tui-control"

# Launchers started by this TUI. Ctrl+C terminates these trees (launcher ->
# harness -> MCP children); the TUI never guesses a PID from disk first.
_ACTIVE: dict[int, subprocess.Popen] = {}
_ACTIVE_LOCK = threading.Lock()


def _register(proc: subprocess.Popen) -> None:
    with _ACTIVE_LOCK:
        _ACTIVE[proc.pid] = proc


def _unregister(proc: subprocess.Popen) -> None:
    with _ACTIVE_LOCK:
        _ACTIVE.pop(proc.pid, None)


def stop_active(timeout: float = 10.0) -> list[int]:
    """Terminate every launcher (and its whole tree) launched by this process."""
    with _ACTIVE_LOCK:
        procs = list(_ACTIVE.values())
    stopped: list[int] = []
    for proc in procs:
        if proc.poll() is not None:
            _unregister(proc)
            continue
        try:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                               capture_output=True, timeout=30,
                               **subproc_mod.kwargs())
            else:
                proc.terminate()
        except (OSError, subprocess.SubprocessError):
            pass
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            try:
                proc.kill()
            except OSError:
                pass
        _unregister(proc)
        if proc.poll() is not None:
            stopped.append(proc.pid)
    return stopped


class LaunchResult:
    def __init__(self):
        self.run_id: str | None = None
        self.directory: Path | None = None
        self.exit_code: int | None = None
        self.output_tail: str = ""


def launch(config_path: Path, python: str | None = None,
           on_line=None) -> LaunchResult:
    """Run the existing launcher synchronously; stream JSON/text lines via on_line."""
    exe = python or sys.executable
    cmd = [exe, "-B", str(ROOT / "scripts" / "run_local_agent.py"),
           "--config", str(config_path)]
    # The launcher and its harness children inherit the debug toggle, so a
    # headless TUI stays headless down the whole tree.
    env = dict(os.environ)
    env[subproc_mod.ENV_VAR] = "1" if subproc_mod.debug_windows() else "0"
    result = LaunchResult()
    lines: list[str] = []

    def pump(stream, sink):
        for raw in iter(stream.readline, ""):
            sink.append(raw)
            if on_line:
                try:
                    on_line(raw.rstrip("\n"))
                except Exception:
                    pass
        stream.close()

    proc = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, bufsize=1, encoding="utf-8", errors="replace",
                            **subproc_mod.kwargs())
    _register(proc)
    try:
        assert proc.stdout is not None
        thread = threading.Thread(target=pump, args=(proc.stdout, lines), daemon=True)
        thread.start()
        # Early read of run_started while the process runs.
        import time
        while proc.poll() is None:
            time.sleep(0.2)
            for raw in list(lines):
                try:
                    payload = json.loads(raw)
                except ValueError:
                    continue
                if payload.get("event") == "run_started" and not result.directory:
                    result.run_id = payload.get("runId")
                    result.directory = Path(payload.get("directory")) if payload.get("directory") else None
        thread.join(timeout=5)
    finally:
        _unregister(proc)
    result.exit_code = proc.returncode
    for raw in lines:
        try:
            payload = json.loads(raw)
        except ValueError:
            continue
        if payload.get("event") == "run_started" and not result.directory:
            result.run_id = payload.get("runId")
            result.directory = Path(payload.get("directory")) if payload.get("directory") else None
    result.output_tail = "".join(lines)[-6000:]
    return result


def request_stop(run_id: str) -> Path:
    """Ask the launcher to stop cooperatively (writes runtime/tui-control/<runId>.stop).

    The launcher polls this file while waiting on the harness and then flows
    through the normal interrupted/failure.json path, so evidence stays
    complete. Never kills a process directly.
    """
    CONTROL_DIR.mkdir(parents=True, exist_ok=True)
    marker = CONTROL_DIR / ("%s.stop" % run_id)
    try:
        marker.write_text("stop requested\n", encoding="utf-8")
    except OSError:
        pass
    return marker


def stop_notice() -> str:
    return ("Quitting the TUI stops the agent run, the local Ollama and the services this TUI "
            "started: the run keeps its evidence and stays in the run list. The hard stop "
            "(Ctrl+P -> Hard stop...) is the same, plus a local Ollama that this TUI did not "
            "start. Ctrl+C copies the selected text and stops nothing. Kill a harness PID "
            "manually only after confirming manifest.json pid belongs to this run directory.")
