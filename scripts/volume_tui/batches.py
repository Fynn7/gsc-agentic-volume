"""One-shot batch runs: same frozen config executed N times sequentially.

Each member run keeps its normal independent run/evidence directory (created by
the existing launcher). The batch directory only holds batch.json with the
frozen config snapshot, planned count, batch status and per-item results.

Batch statuses (exact strings, part of the contract):
  running / completed / completed with failures / interrupted
Item statuses:
  running / success / failed / skipped  (``finished`` is accepted as success
  when reading hand-written records)
Only summaries are stored (config, member status, seconds, error, evidence
references); no model task quality scoring is derived.
"""
from __future__ import annotations

import copy
import json
import secrets
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

BATCHES_DIRNAME = "batches"


def batches_root(root=None) -> Path:
    """Directory holding all batch records (runtime/tui/batches)."""
    base = Path(root) if root is not None else ROOT
    # root may be the repo ROOT or already runtime/tui; handle both.
    candidate = Path(base)
    if candidate.name == "batches" and candidate.parent.name == "tui":
        return candidate
    return candidate / "runtime" / "tui" / BATCHES_DIRNAME


def batch_dir(batch_id: str, root=None) -> Path:
    return batches_root(root) / str(batch_id)


def batch_file(batch_id: str, root=None) -> Path:
    return batch_dir(batch_id, root) / "batch.json"


def new_batch_id() -> str:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    return "batch-%s-%s" % (stamp, secrets.token_hex(3))


def parse_count(raw) -> int:
    """Positive int N (v1: no extra upper limit). Raises ValueError otherwise."""
    if isinstance(raw, bool):
        raise ValueError("count must be a positive integer")
    if isinstance(raw, int):
        count = raw
    elif isinstance(raw, float):
        if not raw.is_integer():
            raise ValueError("count must be a positive integer")
        count = int(raw)
    else:
        text = str(raw or "").strip()
        if not text:
            raise ValueError("count must be a positive integer")
        try:
            count = int(text, 10)
        except (TypeError, ValueError):
            raise ValueError("count must be a positive integer")
        # Reject non-canonical forms like "3.0" / "+3 " handled above; ensure
        # the text is a plain integer.
        if str(count) != text.lstrip("+"):
            # Allow leading zeros? "03" -> 3 is fine, keep strict but accept.
            try:
                if int(text) != count:
                    raise ValueError("count must be a positive integer")
            except ValueError:
                raise ValueError("count must be a positive integer")
    if count < 1:
        raise ValueError("count must be a positive integer")
    return count


def validate_batch(count_raw, task_text: str) -> list[str]:
    """Human-readable problems for the batch form; empty means runnable."""
    problems: list[str] = []
    try:
        parse_count(count_raw)
    except ValueError as error:
        problems.append(str(error))
    if not str(task_text or "").strip():
        problems.append("task must not be empty")
    return problems


def _now() -> float:
    return time.time()


def create_batch_record(batch_id: str, config_snapshot: dict, planned: int,
                        root=None) -> dict:
    """Write a fresh batch.json (status running, no items yet). Returns record."""
    planned = parse_count(planned)
    directory = batch_dir(batch_id, root)
    directory.mkdir(parents=True, exist_ok=True)
    record = {
        "batchId": str(batch_id),
        "config": copy.deepcopy(dict(config_snapshot or {})),
        "planned": int(planned),
        "status": "running",
        "items": [],
        "createdAt": _now(),
        "updatedAt": _now(),
    }
    save_batch(record, root=root)
    return record


def save_batch(record: dict, root=None) -> Path:
    batch_id = str(record.get("batchId") or "")
    if not batch_id:
        raise ValueError("batch record needs a batchId")
    target = batch_file(batch_id, root)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(target)
    return target


def load_batch(batch_id: str, root=None) -> dict | None:
    try:
        data = json.loads(batch_file(batch_id, root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def list_batches(root=None) -> list[dict]:
    """All batch records, newest first. Batch dir survives Archive all."""
    base = batches_root(root)
    out: list[dict] = []
    try:
        candidates = sorted(base.iterdir())
    except OSError:
        return []
    for child in candidates:
        if not child.is_dir():
            continue
        data = load_batch(child.name, root=root)
        if not isinstance(data, dict) or not data.get("batchId"):
            continue
        data = dict(data)
        data["directory"] = str(child)
        out.append(data)
    def _key(entry: dict):
        created = entry.get("createdAt")
        if isinstance(created, (int, float)) and not isinstance(created, bool):
            return float(created)
        try:
            return float(Path(str(entry.get("directory", ""))).stat().st_mtime)
        except OSError:
            return 0.0
    out.sort(key=_key, reverse=True)
    return out


def find_batch(batch_id: str, root=None) -> dict | None:
    for entry in list_batches(root=root):
        if str(entry.get("batchId")) == str(batch_id):
            return entry
    return load_batch(batch_id, root=root)


def _item_ok(status: str) -> bool:
    return str(status or "").lower() in ("success", "finished", "ok")


def finalize_status(record: dict) -> str:
    """Terminal batch status from its items (no quality scoring)."""
    if str(record.get("status") or "") == "interrupted":
        return "interrupted"
    items = record.get("items") or []
    if any(str(i.get("status") or "").lower() == "skipped" for i in items):
        # Skipped only happens on cancel; keep interrupted if flagged,
        # otherwise still report interruption semantics via caller. Here be
        # conservative: skipped implies interrupted.
        return "interrupted"
    if any(not _item_ok(i.get("status")) for i in items if str(i.get("status") or "").lower() not in ("", "running", "pending")):
        return "completed with failures"
    return "completed"


def append_item(record: dict, item: dict) -> None:
    record.setdefault("items", []).append(item)
    record["updatedAt"] = _now()


def batch_summary_text(record: dict) -> list[str]:
    """Config summary + member counts for the detail view (no scoring)."""
    config = record.get("config") or {}
    lines = [
        "batch=%s status=%s planned=%s" % (
            record.get("batchId", "?"), record.get("status", "?"),
            record.get("planned", "?")),
        "model=%s budget=%s task=%s" % (
            config.get("model", "?"), config.get("renderBudget", "?"),
            str(config.get("task", ""))[:120]),
    ]
    ok = 0
    fail = 0
    for item in record.get("items") or []:
        status = str(item.get("status", "?"))
        if status == "success":
            ok += 1
        elif status == "failed" or "error" in status:
            fail += 1
    lines.append("members=%d ok=%d failed=%d (see table below)" % (len(record.get("items") or []), ok, fail))
    return lines


def find_member_dir(run_id: str, fallback: str = "", root=None) -> Path | None:
    """Resolve a batch member's evidence dir by runId, including archive.

    The stored directory is preferred; after Archive all the run lives under
    runtime/archive (discovery skips it), so fall back to a runId search.
    """
    if fallback:
        try:
            if Path(fallback).is_dir():
                return Path(fallback)
        except OSError:
            pass
    base = Path(root) if root is not None else ROOT
    runtime = base / "runtime" if (base / "runtime").is_dir() or not (base / "archive").exists() else base
    # Search normal runtime + archive for a run.json with this runId.
    search_roots = [runtime]
    archive = runtime / "archive"
    if archive.is_dir():
        search_roots.append(archive)
    # Also handle root already being runtime/.
    if base.name == "runtime":
        search_roots = [base, base / "archive"]
    for search in search_roots:
        try:
            candidates = list(search.rglob("run.json"))
        except OSError:
            continue
        for run_file in candidates:
            try:
                data = json.loads(run_file.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(data, dict) and str(data.get("runId") or "") == str(run_id):
                return run_file.parent
    return Path(fallback) if fallback else None


def run_batch_sequential(batch_id: str, effective_config: dict, planned: int,
                         launch_fn, should_cancel=None, on_item=None,
                         root=None) -> dict:
    """Run N members sequentially with the SAME frozen config.

    launch_fn(seq, frozen_config) -> dict with optional runId/directory/
    exit_code/error. Any exception/failure is recorded and the loop continues
    unless should_cancel() is true, in which case remaining items are marked
    skipped and the batch becomes interrupted. Never raises for member
    failures; always persists batch.json.
    """
    planned = parse_count(planned)
    frozen = copy.deepcopy(dict(effective_config or {}))
    record = load_batch(batch_id, root=root)
    if record is None:
        record = create_batch_record(batch_id, frozen, planned, root=root)
    else:
        record["status"] = "running"
        record["updatedAt"] = _now()
        save_batch(record, root=root)

    def _cancelled() -> bool:
        try:
            return bool(should_cancel and should_cancel())
        except Exception:
            return False

    for seq in range(1, planned + 1):
        if _cancelled():
            for rest in range(seq, planned + 1):
                append_item(record, {"seq": rest, "runId": None,
                                     "directory": None, "status": "skipped",
                                     "seconds": 0, "error": "cancelled"})
            record["status"] = "interrupted"
            record["updatedAt"] = _now()
            save_batch(record, root=root)
            if on_item is not None:
                try:
                    on_item(record)
                except Exception:
                    pass
            return record
        # Mark running for visibility, then launch.
        started = _now()
        run_id = None
        directory = None
        error = None
        exit_code = None
        try:
            result = launch_fn(seq, copy.deepcopy(frozen))
            if isinstance(result, dict):
                run_id = result.get("runId") or result.get("run_id")
                directory = result.get("directory")
                exit_code = result.get("exitCode", result.get("exit_code"))
                error = result.get("error")
                if exit_code is None and error is None:
                    # launch_fn returning None-ish success
                    pass
            if directory is not None:
                directory = str(directory)
        except Exception as exc:  # noqa: BLE001 - recorded, loop continues
            error = str(exc)
        seconds = round(_now() - started, 3)
        if _cancelled() and not run_id and not directory:
            # Cancelled while waiting to start: this item never ran.
            append_item(record, {"seq": seq, "runId": run_id,
                                 "directory": directory, "status": "skipped",
                                 "seconds": seconds, "error": "cancelled"})
            for rest in range(seq + 1, planned + 1):
                append_item(record, {"seq": rest, "runId": None,
                                     "directory": None, "status": "skipped",
                                     "seconds": 0, "error": "cancelled"})
            record["status"] = "interrupted"
            record["updatedAt"] = _now()
            save_batch(record, root=root)
            if on_item is not None:
                try:
                    on_item(record)
                except Exception:
                    pass
            return record
        failed = False
        if error:
            failed = True
        elif exit_code is not None:
            try:
                failed = int(exit_code) != 0
            except (TypeError, ValueError):
                failed = False
        # A member that produced no run dir and no explicit success is a failure.
        if not run_id and not directory and not error:
            failed = True
            error = "launch produced no run"
        status = "failed" if failed else "success"
        # A cancel that landed mid-run still counts this item by its outcome;
        # remaining items are skipped and the batch is interrupted.
        item = {"seq": seq, "runId": run_id, "directory": directory,
                "status": status, "seconds": seconds, "error": error,
                "exitCode": exit_code}
        append_item(record, item)
        record["updatedAt"] = _now()
        save_batch(record, root=root)
        if on_item is not None:
            try:
                on_item(record)
            except Exception:
                pass
        if _cancelled():
            for rest in range(seq + 1, planned + 1):
                append_item(record, {"seq": rest, "runId": None,
                                     "directory": None, "status": "skipped",
                                     "seconds": 0, "error": "cancelled"})
            record["status"] = "interrupted"
            record["updatedAt"] = _now()
            save_batch(record, root=root)
            return record
    record["status"] = finalize_status(record)
    # finalize_status returns completed/completed with failures (no skips left).
    if record["status"] == "interrupted":
        # No cancel happened but items contain skipped (should not happen);
        # keep honest terminal state.
        pass
    record["updatedAt"] = _now()
    save_batch(record, root=root)
    return record
