"""History discovery + verification. Read-only; never re-invokes model/renderer/MCP."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import subproc as subproc_mod

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


def run_started_at(run_dir: str | Path) -> float | None:
    """When the run started (run.json), None when the file says nothing.

    One place reads the start of a run: the chat log puts the task first
    with this time, so the log reads in order.
    """
    run = _read_json(Path(run_dir) / "run.json") or {}
    started = run.get("startedAt")
    if isinstance(started, (int, float)) and not isinstance(started, bool):
        return float(started)
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

# One-click tidy for the history list: finished runs move here. Restore one
# run with unarchive_run (the GUI Archived runs dialog) — moving a folder
# back with the file manager restores it too. Discovery never looks inside
# it, so archived runs stay hidden; discover_archived_runs is the only reader.
ARCHIVE_DIRNAME = "archive"


def archive_root() -> Path:
    """Where archived runs live (runtime/archive, created on first archive)."""
    return ROOT / "runtime" / ARCHIVE_DIRNAME


def _is_under_archive(path: str | Path) -> bool:
    """True when a run/session path already lives inside runtime/archive."""
    try:
        resolved = Path(path).resolve()
    except OSError:
        resolved = Path(path)
    try:
        root = archive_root().resolve()
    except OSError:
        root = archive_root()
    try:
        return resolved == root or root in resolved.parents
    except Exception:
        return False


def _describe_entry(directory: Path) -> dict | None:
    """One history-shaped entry for a run dir (run.json + manifest + session).

    The single builder behind history and archived listings: both roads read
    the same fields, so a run looks identical before and after archiving.
    """
    run = _read_json(directory / "run.json") or {}
    if "runId" not in run:
        return None
    manifest = _read_json(directory / "manifest.json")
    verification = _read_json(directory / "verification.json")
    session = _read_json(directory / "session.json")
    started = run.get("startedAt") or (directory / "run.json").stat().st_mtime if (directory / "run.json").exists() else 0
    status = classify(run, manifest)
    frames = None
    if isinstance(session, dict) and isinstance(session.get("events"), list):
        frames = sum(1 for e in session["events"] if e.get("kind") in ("initial_render", "render"))
    return {"directory": str(directory), "runId": run.get("runId", directory.name),
            "task": task_label(directory, run), "taskKey": run.get("taskKey", "?"),
            "startedAt": started, "status": status,
            "model": (_read_json(directory / "config.json") or {}).get("model", "?"),
            "frames": frames, "verification": verification,
            "manifest": manifest, "run": run, "sessionId": run.get("sessionId") or (manifest or {}).get("sessionId")}


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
                if _is_under_archive(run_file):
                    continue
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
        entry = _describe_entry(directory)
        if entry is not None:
            runs.append(entry)
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


def is_live(entry: dict) -> bool:
    """True while the run has not recorded an end.

    `classify` guesses from process liveness; this asks the recorded state, so
    a run whose launcher died but whose harness still runs is still live and
    must be stopped (and keeps its services) instead of being ignored.
    """
    if str(entry.get("status") or "").lower() == "running":
        return True
    return str((entry.get("run") or {}).get("status") or "").lower() == "running"


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
                                   capture_output=True, timeout=60,
                                   **subproc_mod.kwargs())
            if probe.returncode == 0:
                return exe
        except (OSError, subprocess.SubprocessError):
            continue
    return sys.executable


def run_verify(run_dir: str, python: str | None = None) -> dict:
    """Call the existing verifier as a subprocess. Returns {'code', 'report', 'output'}."""
    exe = python or _python_for_verify()
    proc = subprocess.run([exe, "-B", str(ROOT / "scripts" / "verify_local_agent.py"), run_dir],
                          capture_output=True, text=True, timeout=300,
                          **subproc_mod.kwargs())
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


# Keys that carry base64 image bytes in the recorded logs. The copied JSON
# replaces them with a size note: image bytes never belong in a clipboard
# paste, and the run log stays readable.
_IMAGE_BLOB_KEYS = ("data", "image_url", "b64_json", "imageData", "image_base64")
_BLOB_MIN_CHARS = 512

# A list of more than 512 numbers is a bulk array, not a fact about the run:
# the statistics of the volume carry an exact histogram with 65536 bins, and
# that one array alone is 130000 lines of the copy. The copy keeps the count
# and drops the numbers - the file on the disk still has every one of them.
_BULK_MIN_ITEMS = 512


def _is_bulk_numbers(value) -> bool:
    """Is this a list of numbers that is too long to read (and to copy)?

    Every item is checked: a list that holds anything else keeps its
    content, so nothing disappears silently.
    """
    if not isinstance(value, list) or len(value) <= _BULK_MIN_ITEMS:
        return False
    for item in value:
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            return False
    return True


def _readable_payload(value, key=None):
    """Replace the bulky payloads with a size note (never copied raw).

    Two kinds: the base64 image bytes and the bulk number arrays (the
    histogram of the volume). Everything else is copied as it is.
    """
    if isinstance(value, dict):
        return {k: _readable_payload(v, k) for k, v in value.items()}
    if isinstance(value, list):
        if _is_bulk_numbers(value):
            return "<%d numbers omitted (%s)>" % (len(value), key or "values")
        return [_readable_payload(v, key) for v in value]
    if isinstance(value, str) and key in _IMAGE_BLOB_KEYS and len(value) > _BLOB_MIN_CHARS:
        return "<%d base64 image bytes omitted>" % len(value)
    return value


def _jsonl_records(path: Path) -> tuple[list, list]:
    """Parse a .jsonl log into records; broken lines are reported, not hidden."""
    records: list = []
    problems: list[str] = []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as error:
        return records, [str(error)]
    for number, line in enumerate(text.splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except ValueError:
            problems.append("line %d is not valid JSON" % number)
    return records, problems


def run_json_log(run_dir: str | Path, events=None) -> dict:
    """The whole run as one JSON document (read-only).

    The document holds the run's meta data, the complete event log and every
    JSON log file of the run directory (parsed, so the copy is valid JSON
    and not a wall of text). Base64 image payloads become a size note and
    every file that could not be read is listed under "skipped" — nothing
    disappears silently.
    """
    import time as _time

    directory = Path(run_dir)
    run = _read_json(directory / "run.json") or {}
    document = {
        "runId": run.get("runId", directory.name),
        "runDir": str(directory),
        "copiedAt": _time.strftime("%Y-%m-%dT%H:%M:%S"),
        "status": run.get("status"),
        "events": list(events or []),
        "files": {},
        "skipped": [],
    }
    sources = sorted(directory.glob("*.json")) + sorted(directory.glob("*.jsonl"))
    http = directory / "http"
    if http.is_dir():
        sources += sorted(http.glob("*.metadata.json"))
    for path in sources:
        name = path.name if path.parent == directory else "http/%s" % path.name
        try:
            if path.suffix == ".jsonl":
                records, problems = _jsonl_records(path)
                document["files"][name] = _readable_payload(records)
                for problem in problems:
                    document["skipped"].append({"name": name, "reason": problem})
            else:
                data = json.loads(path.read_text(encoding="utf-8"))
                document["files"][name] = _readable_payload(data)
        except (OSError, ValueError) as error:
            document["skipped"].append({"name": name, "reason": str(error)})
    return document


def _unique_archive_target(name: str) -> Path:
    """Archive target for a folder name; never overwrites an archived folder."""
    root = archive_root()
    candidate = root / name
    if not candidate.exists():
        return candidate
    index = 1
    while True:
        indexed = root / ("%s-%d" % (name, index))
        if not indexed.exists():
            return indexed
        index += 1


def _session_dir_for(entry: dict) -> Path | None:
    """Session folder (runtime/<sessionId>) holding this run's frames, if any."""
    session_id = entry.get("sessionId")
    if not session_id:
        session_id = (entry.get("run") or {}).get("sessionId")
    if not session_id:
        return None
    candidate = ROOT / "runtime" / str(session_id)
    try:
        if candidate.is_dir() and not _is_under_archive(candidate):
            return candidate
    except OSError:
        return None
    return None


def _clean_dangling_latest(moved_srcs: set[str]) -> None:
    """Drop LATEST.txt pointers left behind by archived runs (read-only otherwise)."""
    base = ROOT / "runtime"
    if not base.is_dir():
        return
    try:
        pointers = list(base.rglob("LATEST.txt"))
    except OSError:
        return
    for pointer in pointers:
        try:
            if _is_under_archive(pointer):
                continue
            text = pointer.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if text in moved_srcs:
            try:
                pointer.unlink()
            except OSError:
                continue


def _launcher_alive_for(entry: dict) -> bool:
    """True when a lock file still ties a live process to this run.

    A run whose paper status is "running" but whose processes are all gone
    (stale Unknown: launcher died before recording an end) is history, not
    live — archiving it is safe. The only thing that must never be moved
    under a working process is a run a launcher is still starting: the lock
    file names the run before run.json ever learns its end.
    """
    run_id = str(entry.get("runId") or "")
    directory = str(entry.get("directory") or "")
    try:
        locks = sorted((ROOT / "runtime" / "locks").glob("*.lock"))
    except OSError:
        return False
    for lock_path in locks:
        try:
            payload = json.loads(lock_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(payload, dict):
            continue
        refs = {str(payload.get("runId") or ""), str(payload.get("directory") or "")}
        if run_id not in refs and directory not in refs:
            continue
        for field in ("pid", "harnessPid"):
            try:
                if process_alive(payload.get(field)):
                    return True
            except Exception:
                continue
    return False


def archive_all_runs() -> dict:
    """Move every history run into runtime/archive (one-click tidy).

    Only genuinely live runs are kept (a verified live process, or a lock
    file tying one to the run) — stop them first. A run stuck at paper
    status "running" with no live process (Unknown in the history list) is
    history, not live, and moves along with the rest.
    Their session folders (runtime/<sessionId> with the frame PNGs) move
    along so runtime/ is clean; discovery skips runtime/archive, so the
    history list is empty afterwards. Restore one run with unarchive_run
    (the GUI Archived runs dialog); moving a folder back with the file
    manager restores it too.

    Every entry moves through _move_run_to_archive — the same road as the
    single-run archive_run — so batch and single moves can never diverge.
    """
    root = archive_root()
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        return {"moved": [], "sessions_moved": [], "skipped_live": [],
                "errors": [{"runId": "?", "error": str(error)}],
                "archive_dir": str(root)}
    entries = discover_runs(_fresh=True)
    moved: list[str] = []
    sessions_moved: list[str] = []
    skipped_live: list[str] = []
    errors: list[dict] = []
    for entry in entries:
        run_id = str(entry.get("runId") or Path(str(entry.get("directory", ""))).name or "?")
        # Verified-live only: paper "running" with no live process behind
        # it (Unknown in the history list) is stale history, not a live
        # agent, and is archived like everything else. Same live
        # definition as the single-run archive_run (_refuse_if_live).
        if _refuse_if_live(entry) is not None:
            skipped_live.append(run_id)
            continue
        src = Path(str(entry.get("directory", "")))
        try:
            if not src.is_dir() or _is_under_archive(src):
                continue
        except OSError:
            continue
        try:
            result = _move_run_to_archive(entry)
        except ValueError as error:
            errors.append({"runId": run_id, "error": str(error)})
            continue
        moved.append(run_id)
        if result["session_archived"]:
            sessions_moved.append(str(result["session_archived"]))
        errors.extend(result["errors"])
    _invalidate_discover_cache()
    try:
        entries = discover_runs(_fresh=True)
    except Exception:
        entries = []
    return {"moved": moved, "sessions_moved": sessions_moved,
            "skipped_live": skipped_live, "errors": errors,
            "archive_dir": str(root), "remaining": len(entries)}


def _unique_restore_target(name: str) -> Path:
    """Restore target under runtime/; never overwrites an existing folder."""
    parent = ROOT / "runtime"
    candidate = parent / name
    if not candidate.exists():
        return candidate
    index = 1
    while True:
        indexed = parent / ("%s-%d" % (name, index))
        if not indexed.exists():
            return indexed
        index += 1


def _archived_session_dir_for(entry: dict) -> Path | None:
    """Session folder (runtime/archive/<sessionId>) moved along with an archived run, if any."""
    session_id = entry.get("sessionId")
    if not session_id:
        session_id = (entry.get("run") or {}).get("sessionId")
    if not session_id:
        return None
    candidate = archive_root() / str(session_id)
    try:
        if candidate.is_dir():
            return candidate
    except OSError:
        return None
    return None


def discover_archived_runs(limit: int = 200) -> list[dict]:
    """Find archived run dirs (contain run.json) under runtime/archive. Newest first.

    Discovery never looks inside the archive for history; this is the only
    reader for the archived list behind the core ArchivedChatsSettingsDialog.
    No cache: the archived list is small and must show moves at once.
    """
    base = archive_root()
    runs: list[dict] = []
    if not base.is_dir():
        return runs
    try:
        candidates = sorted(base.rglob("run.json"))
    except OSError:
        return runs
    for run_file in candidates:
        if len(runs) >= limit:
            break
        try:
            entry = _describe_entry(run_file.parent)
        except OSError:
            continue
        if entry is not None:
            runs.append(entry)
    runs.sort(key=lambda r: r["startedAt"] or 0, reverse=True)
    return runs


def find_archived_run(run_id: str) -> dict | None:
    """Lookup one archived run by runId (read-only)."""
    rid = str(run_id or "").strip()
    if not rid:
        return None
    for entry in discover_archived_runs():
        if entry.get("runId") == rid:
            return entry
    return None


def _move_run_to_archive(entry: dict) -> dict:
    """Move one history entry (+ its session frames) into runtime/archive."""
    import shutil

    run_id = str(entry.get("runId") or Path(str(entry.get("directory", ""))).name or "?")
    src = Path(str(entry.get("directory", "")))
    if not src.is_dir() or _is_under_archive(src):
        raise ValueError("Run folder is gone already: %s" % run_id)
    root = archive_root()
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise ValueError("Could not open the archive folder: %s" % error)
    session_src = _session_dir_for(entry)
    errors: list[dict] = []
    try:
        target = _unique_archive_target(src.name or run_id)
        shutil.move(str(src), str(target))
    except (OSError, ValueError) as error:
        raise ValueError("Could not archive %s: %s" % (run_id, error))
    session_archived: str | None = None
    if session_src is not None:
        try:
            if session_src.is_dir():
                moved_session = _unique_archive_target(session_src.name)
                shutil.move(str(session_src), str(moved_session))
                session_archived = session_src.name
        except (OSError, ValueError) as error:
            errors.append({"runId": run_id, "error": "session %s: %s" % (session_src.name, error)})
    _clean_dangling_latest({str(src), str(src.resolve()) if src.exists() is False else str(src)})
    _invalidate_discover_cache()
    return {"target": target, "session_archived": session_archived, "errors": errors}


def archive_run(run_id: str) -> dict:
    """Archive one history run (run folder + its session frames).

    Same live definition as archive_all_runs: genuinely live runs are
    refused — stop them first. Archived runs hide from history and appear
    in the archived list behind the core ArchivedChatsSettingsDialog.
    """
    rid = str(run_id or "").strip()
    if not rid:
        raise ValueError("runId required")
    entry = find_run(rid, _fresh=True)
    if entry is None:
        raise ValueError("Unknown run: %s" % rid)
    live = _refuse_if_live(entry)
    if live is not None:
        raise ValueError("Run %s is still live - stop it first" % live)
    moved = _move_run_to_archive(entry)
    try:
        remaining = discover_runs(_fresh=True)
    except Exception:
        remaining = []
    return {"archived": str(entry.get("runId") or rid),
            "session_archived": moved["session_archived"],
            "archive_path": str(moved["target"]),
            "errors": moved["errors"],
            "remaining": len(remaining)}


def unarchive_run(run_id: str) -> dict:
    """Move one archived run back to runtime/ so history shows it again.

    The original nested parent is not recorded by archive moves, so the run
    (and its session frames) restore flat under runtime/ with a unique name.
    Discovery finds runs anywhere under runtime/, so the restored run reads
    exactly like before.
    """
    import shutil

    rid = str(run_id or "").strip()
    if not rid:
        raise ValueError("runId required")
    entry = find_archived_run(rid)
    if entry is None:
        raise ValueError("Unknown archived run: %s" % rid)
    src = Path(str(entry.get("directory", "")))
    if not src.is_dir() or not _is_under_archive(src):
        raise ValueError("Archived run folder is gone already: %s" % rid)
    try:
        target = _unique_restore_target(src.name or rid)
        shutil.move(str(src), str(target))
    except (OSError, ValueError) as error:
        raise ValueError("Could not unarchive %s: %s" % (rid, error))
    session_unarchived: str | None = None
    errors: list[dict] = []
    session_src = _archived_session_dir_for(entry)
    if session_src is not None:
        try:
            if session_src.is_dir():
                moved_session = _unique_restore_target(session_src.name)
                shutil.move(str(session_src), str(moved_session))
                session_unarchived = session_src.name
        except (OSError, ValueError) as error:
            errors.append({"runId": rid, "error": "session %s: %s" % (session_src.name, error)})
    _invalidate_discover_cache()
    try:
        remaining = discover_runs(_fresh=True)
    except Exception:
        remaining = []
    return {"unarchived": str(entry.get("runId") or rid),
            "session_unarchived": session_unarchived,
            "directory": str(target),
            "errors": errors,
            "remaining": len(remaining)}


def _invalidate_discover_cache() -> None:
    """Drop the cached history so a rename/delete shows at once."""
    global _DISCOVER_CACHE
    _DISCOVER_CACHE.update(at=0.0, key=None, value=None)


def _refuse_if_live(entry: dict) -> str | None:
    """runId when the run is genuinely live and must not be touched, else None.

    Same live definition as archive_all_runs: a verified Running status or a
    lock file tying a live process to the run. Paper-"running" with no live
    process behind it is stale history, not live.
    """
    try:
        if (str(entry.get("status") or "") == "Running"
                or _launcher_alive_for(entry)):
            return str(entry.get("runId") or Path(str(entry.get("directory", ""))).name or "?")
    except Exception:
        pass
    return None


def rename_run(run_id: str, title: str) -> dict:
    """Rename the human task label of one run (its config.json task, nothing else).

    The history list and the transcript title both read config.json, so both
    follow the rename. Never moves, deletes, or re-invokes anything.
    """
    name = " ".join(str(title or "").split())
    if not name:
        raise ValueError("New name must not be empty.")
    if len(name) > 200:
        raise ValueError("New name must be at most 200 characters.")
    entry = find_run(str(run_id or "").strip(), _fresh=True)
    if entry is None:
        raise ValueError("Unknown run: %s" % (run_id,))
    run_dir = Path(str(entry.get("directory", "")))
    config_path = run_dir / "config.json"
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ValueError("config.json is unreadable: %s" % error)
    if not isinstance(config, dict):
        raise ValueError("config.json is not an object.")
    config["task"] = name
    try:
        config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
    except OSError as error:
        raise ValueError("Could not save the new name: %s" % error)
    _invalidate_discover_cache()
    return {"runId": entry.get("runId"), "task": name}


def _remove_run_dirs(entry: dict) -> dict:
    """Delete one run folder (+ its session frames); the caller owns the live check."""
    import shutil

    run_id = str(entry.get("runId") or Path(str(entry.get("directory", ""))).name or "?")
    src = Path(str(entry.get("directory", "")))
    if not src.is_dir() or _is_under_archive(src):
        raise ValueError("Run folder is gone already: %s" % run_id)
    errors: list[dict] = []
    try:
        shutil.rmtree(src)
    except (OSError, ValueError) as error:
        raise ValueError("Could not delete %s: %s" % (run_id, error))
    session_deleted: str | None = None
    session_src = _session_dir_for(entry)
    if session_src is not None:
        try:
            if session_src.is_dir():
                shutil.rmtree(session_src)
                session_deleted = session_src.name
        except (OSError, ValueError) as error:
            errors.append({"runId": run_id, "error": "session %s: %s" % (session_src.name, error)})
    _clean_dangling_latest({str(src), str(src.resolve()) if src.exists() is False else str(src)})
    _invalidate_discover_cache()
    return {"deleted": run_id, "session_deleted": session_deleted, "errors": errors}


def delete_run(run_id: str) -> dict:
    """Delete one history run permanently (run folder + its session frames).

    Live runs are refused — stop them first. There is no undelete.
    """
    rid = str(run_id or "").strip()
    if not rid:
        raise ValueError("runId required")
    entry = find_run(rid, _fresh=True)
    if entry is None:
        raise ValueError("Unknown run: %s" % rid)
    live = _refuse_if_live(entry)
    if live is not None:
        raise ValueError("Run %s is still live - stop it first" % live)
    return _remove_run_dirs(entry)


def delete_all_runs() -> dict:
    """Delete every history run permanently (the destructive twin of archive_all_runs).

    Only genuinely live runs are kept (a verified live process, or a lock
    file tying one to the run) — stop them first. Their session folders go
    with them so runtime/ is clean afterwards.
    """
    entries = discover_runs(_fresh=True)
    deleted: list[str] = []
    sessions_deleted: list[str] = []
    skipped_live: list[str] = []
    errors: list[dict] = []
    for entry in entries:
        run_id = str(entry.get("runId") or Path(str(entry.get("directory", ""))).name or "?")
        try:
            if _refuse_if_live(entry) is not None:
                skipped_live.append(run_id)
                continue
        except Exception:
            pass
        try:
            result = _remove_run_dirs(entry)
        except ValueError as error:
            errors.append({"runId": run_id, "error": str(error)})
            continue
        deleted.append(run_id)
        if result.get("session_deleted"):
            sessions_deleted.append(str(result["session_deleted"]))
    _invalidate_discover_cache()
    try:
        entries = discover_runs(_fresh=True)
    except Exception:
        entries = []
    return {"deleted": deleted, "sessions_deleted": sessions_deleted,
            "skipped_live": skipped_live, "errors": errors,
            "remaining": len(entries)}
