"""Structured event tailing that reuses agent_console semantics.

Sources: harness.jsonl, the run's own session.json (embedded events array,
first choice — always in the run dir), runtime/<session>/events.jsonl
(gap-fill by sequence number), http/*.metadata.json, plus the recorded
request bodies for per-call request summaries. Same partial-line handling
as agent_console: the last line without a trailing newline is buffered,
never parsed. Image bytes are never loaded into the view; request/response
bodies are summarized (model, text heads, tool names/args, image counts)
and base64 is never printed; pixel matching stays inside
verify_local_agent.py.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
for _extra in (_ROOT / "scripts", Path(__file__).resolve().parent):
    if str(_extra) not in sys.path:
        sys.path.insert(0, str(_extra))

import agent_console as console_mod
import event_text as text_mod

_INDEX = re.compile(r"http-(\d+)")


def _http_index(path: Path) -> int:
    match = _INDEX.search(path.name)
    return int(match.group(1)) if match else 0


def _request_summary(run_dir: Path, index: int) -> tuple[str, str]:
    """What was actually sent to the model for call #index (no base64).

    Reads the recorded request body (forwarded copy preferred) and reports
    model, message roles with text heads, tool calls with brief arguments,
    and tool outputs as text heads + image counts/byte sizes. Image bytes
    are never included. Returns (model name, summary text).
    """
    for name in ("http-%03d.forwarded.bin" % index, "http-%03d.request.bin" % index):
        path = run_dir / "http" / name
        if not path.is_file():
            continue
        try:
            if path.stat().st_size > 32 * 1024 * 1024:
                return "?", "request body too large to summarize (%d bytes)" % path.stat().st_size
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        model = str(payload.get("model", "?"))
        lines = ["model=%s" % model]
        tools = payload.get("tools") or []
        if tools:
            lines.append("tools=%s" % ",".join(str(t.get("name", "?")) for t in tools if isinstance(t, dict)))
        for item in (payload.get("input") or []):
            if not isinstance(item, dict):
                continue
            kind = item.get("type")
            if kind == "message":
                texts = [c.get("text", "") for c in (item.get("content") or [])
                         if isinstance(c, dict) and isinstance(c.get("text"), str)]
                head = console_mod._brief(" ".join(texts), 160)
                lines.append("%s: %s" % (item.get("role", "?"), head))
            elif kind == "function_call":
                lines.append("call %s %s" % (
                    item.get("name", "?"), console_mod._brief(item.get("arguments", ""), 200)))
            elif kind == "function_call_output":
                texts, images, image_bytes = [], 0, 0
                for chunk in (item.get("output") or []):
                    if not isinstance(chunk, dict):
                        continue
                    if isinstance(chunk.get("text"), str):
                        texts.append(chunk["text"])
                    if chunk.get("type") == "input_image":
                        images += 1
                        url = chunk.get("image_url") or ""
                        image_bytes += len(url) if isinstance(url, str) else 0
                if texts:
                    lines.append("output: %s" % console_mod._brief(" ".join(texts), 200))
                if images:
                    lines.append("output: %d image(s) ~%d bytes (bytes not shown)" % (images, image_bytes))
                if not texts and not images:
                    lines.append("output: (empty)")
        return model, ("\n".join(lines) if lines else "(empty request body)")
    return "?", "(request body not recorded yet)"


class EventTail:
    """Incremental, read-only tail producing compact display dicts."""

    def __init__(self, run_dir: Path, session_dir: Path | None = None):
        self.run_dir = Path(run_dir)
        self.session_dir = Path(session_dir) if session_dir else None
        self._files: dict[str, dict] = {}
        self._seen_request: set[str] = set()
        self._seen_metadata: set[str] = set()
        self._seen_seq: set = set()
        self._session_offset = 0
        self._session_mtime: float = 0.0
        self._session_size: int = -1
        self._turn = 0
        # What the last call of each tool changed, so a rejection can name it.
        self._last_call: dict = {}
        self.counters = {"images": 0, "tools": 0, "http": 0}
        # Task and observation budget of the watched run, read from the run's
        # own session.json. Shown in the Live header so the log can be read
        # without knowing what the run was for.
        self.meta: dict = {}

    @staticmethod
    def _now() -> float:
        import time as _time
        return _time.time()

    def _lines(self, key: str, path: Path):
        state = self._files.setdefault(key, {"path": None, "offset": 0, "buffer": ""})
        if state["path"] != str(path):
            state.update(path=str(path), offset=0, buffer="")
        if not path.exists():
            return
        try:
            if path.stat().st_size < state["offset"]:
                state.update(offset=0, buffer="")
            with path.open("r", encoding="utf-8", errors="replace") as handle:
                handle.seek(state["offset"])
                data = handle.read()
                state["offset"] = handle.tell()
        except OSError:
            return
        if not data:
            return
        text = state["buffer"] + data
        parts = text.split("\n")
        state["buffer"] = parts.pop()  # incomplete trailing line stays buffered
        for line in parts:
            line = line.strip()
            if line:
                yield line

    def poll(self) -> list[dict]:
        events: list[dict] = []
        events += self._poll_harness()
        events += self._poll_session_json()
        events += self._poll_live_meta()
        events += self._poll_events()
        events += self._poll_http()
        return events

    # -- harness.jsonl (model turns) -------------------------------------
    def _poll_harness(self) -> list[dict]:
        out = []
        for line in self._lines("harness", self.run_dir / "harness.jsonl"):
            try:
                record = json.loads(line)
            except ValueError:
                continue  # partial/corrupt line skipped, same as agent_console
            stamp = self._now()
            kind = record.get("type")
            if kind == "turn.started":
                self._turn += 1
                event = text_mod.model_turn_started(self._turn)
                out.append({"cat": "MODEL", "kind": "thinking_start",
                            "title": event["title"], "detail": event["detail"],
                            "turn": self._turn, "ts": stamp})
            elif kind == "turn.completed":
                usage = record.get("usage") or {}
                event = text_mod.model_turn_done(self._turn, usage)
                out.append({"cat": "MODEL", "kind": "thinking_end",
                            "title": event["title"], "detail": event["detail"],
                            "turn": self._turn, "usage": usage, "ts": stamp})
            elif kind == "turn.failed":
                message = str((record.get("error") or {}).get("message")
                              or record.get("message") or "the turn failed")
                out.append({"cat": "ERROR", "kind": "error", "title": "ERROR  %s" % message,
                            "detail": message, "message": message, "ts": stamp})
            elif kind in ("item.started", "item.updated", "item.completed"):
                item = record.get("item") or {}
                item_type = item.get("type")
                if item_type == "agent_message":
                    event = text_mod.model_message(item.get("text", ""))
                    # The raw text is kept: the transcript shows the whole
                    # answer, not the shortened log title. A streamed answer
                    # arrives as item.updated - the block grows with it.
                    event_kind = ("assistant_update"
                                  if record.get("type") == "item.updated"
                                  else "assistant")
                    out.append({"cat": "MODEL", "kind": event_kind,
                                "title": event["title"], "detail": event["detail"],
                                "text": item.get("text", ""), "ts": stamp})
                elif item_type == "error":
                    # The codex harness reports its own notices this way; they
                    # do not stop the run, so they are warnings, not errors.
                    message = item.get("message", "")
                    event = text_mod.harness_warning(message)
                    out.append({"cat": "WARN", "kind": "warn", "title": event["title"],
                                "detail": event["detail"], "message": message,
                                "ts": stamp})
                elif item_type == "mcp_tool_call":
                    out.extend(self._harness_tool_event(record, item, stamp))
        return out

    def _harness_tool_event(self, record: dict, item: dict, stamp: float) -> list[dict]:
        """The harness view of a renderer tool call.

        The service log reports the same call; the transcript folds both into
        one block, so the evidence stays complete without showing the call
        twice.
        """
        name = str(item.get("tool") or "tool")
        status = str(item.get("status") or "")
        if record.get("type") in ("item.started", "item.updated"):
            return [{"cat": "TOOL", "kind": "tool_call", "title": "TOOL  %s" % name,
                     "detail": "", "name": name, "source": "harness",
                     "arguments": item.get("arguments") or {}, "ts": stamp}]
        failed = bool(item.get("error")) or status == "failed"
        if failed:
            message = str(item.get("error") or "the tool call failed")
            return [{"cat": "ERROR", "kind": "tool_error", "title": "TOOL  %s  FAILED" % name,
                     "detail": message, "name": name, "source": "harness",
                     "message": message, "ts": stamp}]
        return [{"cat": "TOOL", "kind": "tool_result", "title": "TOOL  %s  returned" % name,
                 "detail": "", "name": name, "source": "harness", "ts": stamp}]

    # -- run_dir/session.json (embedded events array; always in the run dir) --
    def _poll_session_json(self) -> list[dict]:
        """Tool/image/finish evidence from the run's own session.json.

        The service event log under runtime/<session>/events.jsonl may be
        unreachable while a run is live (session id unknown until the copy
        lands). The run directory's session.json embeds the same event
        sequence, so poll it first; the events.jsonl tail below only fills
        gaps by sequence number.
        """
        out = []
        path = self.run_dir / "session.json"
        try:
            stat = path.stat()
        except OSError:
            return out
        # mtime/size gate: a live session.json is rewritten wholesale on every
        # event, so skip the full JSON parse when nothing changed.
        if stat.st_mtime == self._session_mtime and stat.st_size == self._session_size:
            return out
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return out
        self._session_mtime = stat.st_mtime
        self._session_size = stat.st_size
        self.meta = {key: data.get(key) for key in ("goal", "model", "budget", "used", "state")}
        events = data.get("events")
        if not isinstance(events, list):
            return out
        start = self._session_offset
        if start > len(events):
            start = 0
        for record in events[start:]:
            self._session_offset += 1
            if not isinstance(record, dict):
                continue
            seq = record.get("seq")
            if seq is not None:
                if seq in self._seen_seq:
                    continue
                self._seen_seq.add(seq)
            event = self._service_record(record)
            if event is not None:
                out.append(event)
        return out

    def _service_record(self, record: dict) -> dict | None:
        """One service event (shared by session.json and events.jsonl tails)."""
        import time as _time
        stamp = _time.time()
        kind = record.get("kind")
        if kind in ("initial_render", "render", "slice"):
            result = record.get("result") or {}
            image = result.get("image")
            scene = result.get("scene") or {}
            rev = scene.get("revision")
            self.counters["images"] += 1
            event = text_mod.image_event(kind, result)
            return {"cat": "IMAGE", "kind": "image", "title": event["title"],
                    "detail": event["detail"],
                    "image": image, "revision": rev, "ts": stamp,
                    "source": kind, "lit": self._lit_pixels(result),
                    "size": self._output_size(scene),
                    "seq": record.get("seq"), "time": record.get("time")}
        elif kind == "external_tool_call":
            self.counters["tools"] += 1
            event = text_mod.tool_call(record.get("name"), record.get("arguments"))
            # Remember what the call changed, so a later rejection can name it.
            self._last_call[record.get("name")] = event.get("changes") or []
            return {"cat": "TOOL", "kind": "tool_call", "title": event["title"],
                    "detail": event["detail"], "source": "service",
                    "ts": stamp, "seq": record.get("seq"), "time": record.get("time"),
                    "name": record.get("name"), "arguments": record.get("arguments")}
        elif kind == "external_tool_return":
            event = text_mod.tool_return(record.get("name"), record.get("seconds"))
            return {"cat": "TOOL", "kind": "tool_result", "title": event["title"],
                    "detail": event["detail"], "source": "service",
                    "ts": stamp, "seq": record.get("seq"), "time": record.get("time"),
                    "name": record.get("name"), "seconds": record.get("seconds")}
        elif kind == "tool_error":
            event = text_mod.tool_error(record.get("name"), record.get("message", ""),
                                        self._last_call.get(record.get("name")))
            return {"cat": "ERROR", "kind": "tool_error", "title": event["title"],
                    "detail": event["detail"], "source": "service",
                    "ts": stamp, "seq": record.get("seq"), "time": record.get("time"),
                    "name": record.get("name"), "message": record.get("message", "")}
        elif kind == "finish":
            event = text_mod.finish_event(record)
            return {"cat": "FINISH", "kind": "finish", "title": event["title"],
                    "detail": event["detail"],
                    "ts": stamp, "seq": record.get("seq"), "time": record.get("time"),
                    "revision": record.get("selectedRevision"),
                    "verification": record.get("externalTaskVerification"),
                    "claim": record.get("modelClaim", "")}
        elif kind == "error":
            message = record.get("message", "")
            event = text_mod.error_event(message)
            return {"cat": "ERROR", "kind": "error", "title": event["title"],
                    "detail": event["detail"], "message": message,
                    "ts": stamp, "seq": record.get("seq"), "time": record.get("time")}
        elif kind == "model_action":
            action = record.get("action") or {}
            event = text_mod.model_action(action)
            return {"cat": "MODEL", "kind": "thinking", "title": event["title"],
                    "detail": event["detail"],
                    "reason": action.get("reason", ""),
                    "tool": action.get("tool", ""),
                    "ts": stamp, "seq": record.get("seq"), "time": record.get("time")}
        elif kind == "pause":
            event = text_mod.paused_event(record)
            return {"cat": "MODEL", "kind": "notice", "title": event["title"],
                    "detail": event["detail"],
                    "text": "the agent stopped early - %s observations left"
                            % text_mod.count(record.get("remaining", "?")),
                    "ts": stamp, "seq": record.get("seq"), "time": record.get("time")}
        return None

    @staticmethod
    def _lit_pixels(result: dict):
        """How many pixels of the frame are not black (the frame's content)."""
        value = result.get("nonblackPixels")
        return text_mod.count(value) if value is not None else None

    @staticmethod
    def _output_size(scene: dict) -> str:
        """The frame size, e.g. '512x512'."""
        output = scene.get("output") or []
        if len(output) == 2:
            return "%sx%s" % (output[0], output[1])
        return "?"

    def _poll_live_meta(self) -> list[dict]:
        """The live budget, read from the renderer's own session.json.

        The run directory's session.json is only written when the run ends;
        while the run is live the renderer rewrites
        runtime/<session>/session.json on every event (goal, model, budget,
        used, state). Without this the status line falls back to
        config.json and shows 0 observations used for the whole run.
        """
        if self.session_dir is None:
            return []
        try:
            data = json.loads((self.session_dir / "session.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        if not isinstance(data, dict):
            return []
        live = {key: data.get(key) for key in ("goal", "model", "budget", "used", "state")
                if data.get(key) is not None}
        if live:
            merged = dict(self.meta)
            merged.update(live)
            self.meta = merged
        return []

    # -- runtime/<session>/events.jsonl (tools + images) ------------------
    def _poll_events(self) -> list[dict]:
        out = []
        if self.session_dir is None:
            return out
        for line in self._lines("events", self.session_dir / "events.jsonl"):
            try:
                record = json.loads(line)
            except ValueError:
                continue
            seq = record.get("seq")
            if seq is not None:
                if seq in self._seen_seq:
                    continue
                self._seen_seq.add(seq)
            event = self._service_record(record)
            if event is not None:
                out.append(event)
        return out

    # -- http/*.metadata.json (inference) ----------------------------------
    def _poll_http(self) -> list[dict]:
        out = []
        directory = self.run_dir / "http"
        if not directory.is_dir():
            return out
        for path in sorted(directory.glob("http-*.request.bin"), key=_http_index):
            if path.name in self._seen_request:
                continue
            self._seen_request.add(path.name)
            index = _http_index(path)
            model, summary = _request_summary(self.run_dir, index)
            event = text_mod.infer_request(index, model, summary)
            out.append({"cat": "INFER", "kind": "meta", "title": event["title"],
                        "detail": event["detail"],
                        "text": "model call #%d%s" % (
                            index, (" · %s" % model) if model else ""),
                        "ts": self._now()})
        for path in sorted(directory.glob("http-*.metadata.json"), key=_http_index):
            if path.name in self._seen_metadata:
                continue
            self._seen_metadata.add(path.name)
            try:
                meta = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            self.counters["http"] += 1
            stamp = self._now()
            event = text_mod.infer_response(meta)
            failed = "FAILED" in event["title"]
            seconds = meta.get("seconds")
            elapsed = ("%ds" % int(seconds + 0.5)) if isinstance(seconds, (int, float)) else "?"
            out.append({"cat": "ERROR" if failed else "INFER",
                        "kind": "error" if failed else "meta",
                        "title": event["title"], "detail": event["detail"],
                        "message": event["title"] if failed else "",
                        "text": "HTTP %s in %s" % (meta.get("status", "?"), elapsed),
                        "ts": stamp})
        return out


def resolve_session_dir(run_dir: Path) -> Path | None:
    for key in ("session.json",):
        try:
            session_id = json.loads((run_dir / key).read_text(encoding="utf-8")).get("id")
        except (OSError, ValueError):
            continue
        if session_id:
            candidate = _ROOT / "runtime" / session_id
            if candidate.is_dir():
                return candidate
    # Fallback: session id from run.json (runtime/<session> layout used by codex runs).
    try:
        session_id = json.loads((run_dir / "run.json").read_text(encoding="utf-8")).get("sessionId")
    except (OSError, ValueError):
        session_id = None
    if session_id:
        candidate = _ROOT / "runtime" / session_id
        if candidate.is_dir():
            return candidate
    # Fallback: the launcher's lock file. While the run is live the lock
    # holds the session id (written right after /api/tools/open) together
    # with the run directory and run id, so the live session resolves even
    # before run.json carries the session id.
    try:
        locks = sorted((_ROOT / "runtime" / "locks").glob("*.lock"))
    except OSError:
        locks = []
    wanted = {run_dir.name, str(run_dir)}
    try:
        wanted.add(str(run_dir.resolve()))
    except OSError:
        pass
    wanted = {os.path.normcase(item) for item in wanted}
    for lock_path in locks:
        try:
            payload = json.loads(lock_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(payload, dict):
            continue
        candidates = {os.path.normcase(str(payload.get("runId") or "")),
                      os.path.normcase(str(payload.get("directory") or ""))}
        if candidates & wanted:
            session_id = payload.get("sessionId")
            if session_id:
                candidate = _ROOT / "runtime" / session_id
                if candidate.is_dir():
                    return candidate
    return None
