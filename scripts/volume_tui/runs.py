"""History discovery + verification. Read-only; never re-invokes model/renderer/MCP."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
try:
    from run_lifecycle import process_alive
except Exception:  # pragma: no cover - import guard for tests
    def process_alive(pid):  # type: ignore
        return False


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def task_label(directory: Path, run: dict) -> str:
    """Human task name from the run's own config.json; falls back to the taskKey hash."""
    try:
        from config import TASK_A, TASK_B
    except ImportError:  # pragma: no cover - sys.path without the TUI package
        TASK_A, TASK_B = "", ""
    try:
        task_text = (json.loads((Path(directory) / "config.json").read_text(encoding="utf-8")).get("task") or "")
    except (OSError, ValueError):
        task_text = ""
    normalized = " ".join(task_text.split())
    if TASK_A and normalized == " ".join(TASK_A.split()):
        return "Task A"
    if TASK_B and normalized == " ".join(TASK_B.split()):
        return "Task B"
    if normalized:
        return "Custom: " + (normalized[:60] + "..." if len(normalized) > 60 else normalized)
    return "taskKey=%s" % run.get("taskKey", "?")


_DISCOVER_CACHE = {"at": 0.0, "key": None, "value": None}
DISCOVER_TTL = 5.0


def discover_runs(limit: int = 200, _fresh: bool = False) -> list[dict]:
    """Find real run dirs (contain run.json). Newest first. Tolerates missing/old fields.

    Results are cached for DISCOVER_TTL seconds (keyed on ROOT) so menu redraws
    stay instant; pass _fresh=True after a state change that must show at once.
    """
    global _DISCOVER_CACHE
    import time as _time
    cache_key = (str(ROOT), limit)
    now = _time.time()
    if (not _fresh and _DISCOVER_CACHE["value"] is not None
            and _DISCOVER_CACHE["key"] == cache_key
            and (now - _DISCOVER_CACHE["at"]) < DISCOVER_TTL):
        return _DISCOVER_CACHE["value"]
    found: dict[str, Path] = {}
    for base in (ROOT / "runtime",):
        if not base.is_dir():
            continue
        for run_file in base.rglob("run.json"):
            try:
                directory = run_file.parent
                # Skip lock dirs / config dirs: require manifest or session or runId field.
                data = _read_json(run_file) or {}
                if "runId" not in data:
                    continue
                found[str(directory.resolve())] = directory
                if len(found) >= limit:
                    break
            except OSError:
                continue
    runs = []
    for directory in found.values():
        run = _read_json(directory / "run.json") or {}
        manifest = _read_json(directory / "manifest.json")
        verification = _read_json(directory / "verification.json")
        session = _read_json(directory / "session.json")
        started = run.get("startedAt") or (directory / "run.json").stat().st_mtime if (directory / "run.json").exists() else 0
        status = classify(run, manifest)
        frames = None
        if isinstance(session, dict) and isinstance(session.get("events"), list):
            frames = sum(1 for e in session["events"] if e.get("kind") in ("initial_render", "render"))
        runs.append({"directory": str(directory), "runId": run.get("runId", directory.name),
                     "task": task_label(directory, run), "taskKey": run.get("taskKey", "?"),
                     "startedAt": started, "status": status,
                     "model": (_read_json(directory / "config.json") or {}).get("model", "?"),
                     "frames": frames, "verification": verification,
                     "manifest": manifest, "run": run, "sessionId": run.get("sessionId") or (manifest or {}).get("sessionId")})
    runs.sort(key=lambda r: r["startedAt"] or 0, reverse=True)
    _DISCOVER_CACHE.update(at=now, key=cache_key, value=runs)
    return runs


def classify(run: dict, manifest: dict | None) -> str:
    """Finished / Failed / Running / Unknown from evidence only."""
    status = (run.get("status") or "").lower()
    if status == "finished":
        code = (manifest or {}).get("exitCode")
        if (manifest or {}).get("timedOut"):
            return "Failed"
        return "Finished" if code in (0, None) else "Failed"
    if status == "failed":
        return "Failed"
    if status == "running":
        pid = run.get("pid")
        if isinstance(pid, int) and process_alive(pid):
            return "Running"
        hp = (manifest or {}).get("pid") if manifest else None
        if isinstance(hp, int) and process_alive(hp):
            return "Running"
        return "Unknown"
    if (run.get("directory") and (Path(run["directory"]) / "failure.json").exists()):
        return "Failed"
    return "Unknown"


def latest_for_base(base: str) -> Path | None:
    candidate = Path(base) / "LATEST.txt" if not Path(base).is_absolute() else Path(base) / "LATEST.txt"
    anchor = (ROOT / candidate) if not candidate.is_absolute() else candidate
    try:
        text = anchor.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return Path(text) if text else None


def _python_for_verify() -> str:
    """Interpreter that has NumPy/Pillow: sys.executable first, else the Codex bundle."""
    bundled = str(Path.home() / ".cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe")
    for exe in (sys.executable, bundled):
        try:
            probe = subprocess.run([exe, "-B", "-c", "import numpy,PIL"],
                                   capture_output=True, timeout=60)
            if probe.returncode == 0:
                return exe
        except (OSError, subprocess.SubprocessError):
            continue
    return sys.executable


def run_verify(run_dir: str, python: str | None = None) -> dict:
    """Call the existing verifier as a subprocess. Returns {'code', 'report', 'output'}."""
    exe = python or _python_for_verify()
    proc = subprocess.run([exe, "-B", str(ROOT / "scripts" / "verify_local_agent.py"), run_dir],
                          capture_output=True, text=True, timeout=300)
    report = None
    try:
        report = json.loads((Path(run_dir) / "verification.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    return {"code": proc.returncode, "report": report, "output": (proc.stdout[-4000:] + proc.stderr[-2000:])}


def verify_summary(report: dict | None) -> list[str]:
    """Honest three-way split: execution / transport / visual-quality."""
    if not report:
        return ["No verification.json yet — press Verify evidence (read-only check, no model call)."]
    lines = []
    lines.append("state=%s actualFrames=%s agentRenders=%s" % (
        report.get("state"), report.get("actualFrames"), report.get("agentRenders")))
    delivered = report.get("visuallyDeliveredRevisions", [])
    lines.append("Execution: %s | Image transport: %s (revisions %s)" % (
        "passed (session finished, exit 0)" if report.get("state") == "finished" else "NOT passed (see state/failure.json)",
        "passed (PNG bytes matched a model request)" if delivered else "NOT passed (no frame proven inside a model request)",
        delivered if delivered else "none"))
    lines.append("Visual quality: UNVERIFIED by tooling — open the final PNG and judge honestly.")
    final = report.get("final") or {}
    if final.get("image"):
        lines.append("final rev=%s %s" % (final.get("revision"), final.get("image")))
    return lines


def find_run(run_id: str, _fresh: bool = True) -> dict | None:
    """Lookup one run by runId (read-only)."""
    for entry in discover_runs(_fresh=_fresh):
        if entry.get("runId") == run_id:
            return entry
    return None


def run_config_text(run_dir: str | Path) -> str:
    """Pretty config.json for diff/detail views; empty when unreadable."""
    try:
        data = _read_json(Path(run_dir) / "config.json")
    except OSError:
        return ""
    if not isinstance(data, dict):
        return ""
    return json.dumps(data, indent=2, sort_keys=True)


def task_text_of(run_dir: str | Path) -> str:
    try:
        data = _read_json(Path(run_dir) / "config.json") or {}
    except OSError:
        return ""
    task = data.get("task")
    return task if isinstance(task, str) else ""


def config_diff(left_dir: str | Path, right_dir: str | Path) -> list[str]:
    """Unified diff of the two runs' config.json (read-only, no model call)."""
    import difflib
    left = run_config_text(left_dir).splitlines()
    right = run_config_text(right_dir).splitlines()
    if not left and not right:
        return ["No config.json in either run."]
    diff = list(difflib.unified_diff(left, right, fromfile="left config.json",
                                     tofile="right config.json", lineterm=""))
    return diff or ["Configs are identical."]


def final_image_of(entry: dict) -> str | None:
    """Best final PNG path: verification final, else newest frame PNG under runtime/<session>."""
    verification = entry.get("verification") or {}
    final = verification.get("final") if isinstance(verification, dict) else None
    if isinstance(final, dict) and final.get("image"):
        return str(final["image"])
    session_id = entry.get("sessionId")
    if session_id:
        try:
            candidates = sorted((ROOT / "runtime" / str(session_id)).rglob("*.png"))
            if candidates:
                return str(candidates[-1])
        except OSError:
            pass
    # Fallback inside the run dir itself.
    try:
        candidates = sorted(Path(str(entry.get("directory", ""))).rglob("frame-*/image.png"))
        if candidates:
            return str(candidates[-1])
    except OSError:
        pass
    return None


def frame_images(run_dir: str | Path, limit: int = 60) -> list[dict]:
    """Proven frame PNGs from session.json events (rev + path, newest last)."""
    try:
        data = _read_json(Path(run_dir) / "session.json") or {}
    except OSError:
        return []
    events = data.get("events")
    if not isinstance(events, list):
        return []
    out: list[dict] = []
    for event in events:
        if not isinstance(event, dict):
            continue
        if event.get("kind") not in ("initial_render", "render"):
            continue
        result = event.get("result") or {}
        image = result.get("image")
        scene = result.get("scene") or {}
        if image:
            out.append({"revision": scene.get("revision"), "image": str(image),
                        "nonblack": result.get("nonblackPixels")})
    return out[-limit:] if limit else out


def summary_numbers(entry: dict) -> dict:
    """Compact numbers for dashboard/compare (status/frames/seconds/model/volume)."""
    manifest = entry.get("manifest") or {}
    run = entry.get("run") or {}
    verification = entry.get("verification") or {}
    seconds = manifest.get("seconds") or manifest.get("totalSeconds")
    return {
        "runId": entry.get("runId", "?"),
        "status": entry.get("status", "Unknown"),
        "model": entry.get("model", "?"),
        "frames": entry.get("frames"),
        "seconds": seconds,
        "exitCode": manifest.get("exitCode"),
        "task": entry.get("task", "?"),
        "actualFrames": (verification or {}).get("actualFrames"),
        "agentRenders": (verification or {}).get("agentRenders"),
        "volume": ((_read_json(Path(str(entry.get("directory", ""))) / "config.json") or {}).get("volumePath", "?")),
    }
