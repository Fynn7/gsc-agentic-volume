"""Launch helper: delegates 100% of lifecycle to scripts/run_local_agent.py.

The TUI never reserves directories, writes locks, or picks Run IDs. It only
writes a plain input config file, spawns the launcher, and reads back the
`run_started` line (runId + directory). Concurrency errors (exit 3) are
surfaced verbatim; no competing lock scheme exists here.
"""
from __future__ import annotations

import json
import subprocess
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


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

    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, bufsize=1, encoding="utf-8", errors="replace")
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


def stop_notice() -> str:
    return ("Stop is intentionally limited: the CLI has no verified safe-cancel API. "
            "Closing the TUI never kills the agent; the run keeps its evidence and can be "
            "re-opened via History. Kill a harness PID only manually after confirming "
            "manifest.json pid belongs to this run directory.")
