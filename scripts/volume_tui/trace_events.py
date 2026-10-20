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
import re
from pathlib import Path

import agent_console as console_mod

_INDEX = re.compile(r"http-(\d+)")


def _http_index(path: Path) -> int:
    match = _INDEX.search(path.name)
    return int(match.group(1)) if match else 0


def _request_summary(run_dir: Path, index: int) -> str:
    """What was actually sent to the model for call #index (no base64).

    Reads the recorded request body (forwarded copy preferred) and reports
    model, message roles with text heads, tool calls with brief arguments,
    and tool outputs as text heads + image counts/byte sizes. Image bytes
    are never included.
    """
    for name in ("http-%03d.forwarded.bin" % index, "http-%03d.request.bin" % index):
        path = run_dir / "http" / name
        if not path.is_file():
            continue
        try:
            if path.stat().st_size > 32 * 1024 * 1024:
                return "request body too large to summarize (%d bytes)" % path.stat().st_size
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        lines = ["model=%s" % payload.get("model", "?")]
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
        return "\n".join(lines) if lines else "(empty request body)"
    return "(request body not recorded yet)"


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
        self.counters = {"images": 0, "tools": 0, "http": 0}

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
                out.append({"cat": "MODEL", "title": "MODEL #%d  Running" % self._turn,
                            "detail": "turn started", "ts": stamp})
            elif kind == "turn.completed":
                usage = record.get("usage") or {}
                if self._turn > 0:
                    out.append({"cat": "MODEL", "title": "MODEL #%d  Done" % self._turn,
                            "detail": "input=%s cached=%s output=%s" % (
                                usage.get("input_tokens"), usage.get("cached_input_tokens"),
                                usage.get("output_tokens")), "ts": stamp})
                else:
                    out.append({"cat": "MODEL", "title": "MODEL  Done (turn count unknown)",
                            "detail": "input=%s cached=%s output=%s" % (
                                usage.get("input_tokens"), usage.get("cached_input_tokens"),
                                usage.get("output_tokens")), "ts": stamp})
            elif kind == "item.completed":
                item = record.get("item") or {}
                if item.get("type") == "agent_message":
                    out.append({"cat": "MODEL", "title": "MODEL  message",
                                "detail": console_mod._brief(item.get("text", ""), 500), "ts": stamp})
                elif item.get("type") == "error":
                    out.append({"cat": "ERROR", "title": "WARN  harness",
                                "detail": console_mod._brief(item.get("message", ""), 320), "ts": stamp})
        return out

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
            label = ("Frame %d" % self.counters["images"]) if kind != "slice" else "Slice"
            return {"cat": "IMAGE", "title": "IMAGE %s  Native PNG saved" % label,
                    "detail": "rev=%s nonblack=%s\n%s" % (
                        rev, result.get("nonblackPixels"), image),
                    "image": image, "revision": rev, "ts": stamp,
                    "seq": record.get("seq"), "time": record.get("time")}
        elif kind == "external_tool_call":
            self.counters["tools"] += 1
            return {"cat": "TOOL", "title": "TOOL  %s  Started" % record.get("name"),
                    "detail": console_mod._brief(record.get("arguments", {}), 240),
                    "ts": stamp, "seq": record.get("seq"), "time": record.get("time"),
                    "name": record.get("name"), "arguments": record.get("arguments")}
        elif kind == "external_tool_return":
            try:
                seconds = " %.2fs" % float(record["seconds"])
            except (KeyError, TypeError, ValueError):
                seconds = ""
            return {"cat": "TOOL", "title": "TOOL  %s  Success (%s)" % (record.get("name"), seconds.strip() or "?"),
                    "detail": "", "ts": stamp, "seq": record.get("seq"), "time": record.get("time"),
                    "name": record.get("name"), "seconds": record.get("seconds")}
        elif kind == "tool_error":
            return {"cat": "ERROR", "title": "TOOL  %s  Failed" % record.get("name"),
                    "detail": console_mod._brief(record.get("message", ""), 300),
                    "ts": stamp, "seq": record.get("seq"), "time": record.get("time"),
                    "name": record.get("name")}
        elif kind == "finish":
            return {"cat": "FINISH", "title": "FINISH  selected revision %s" % record.get("selectedRevision"),
                    "detail": "verification=%s claim: %s" % (
                        record.get("externalTaskVerification"),
                        console_mod._brief(record.get("modelClaim", ""), 400)),
                    "ts": stamp, "seq": record.get("seq"), "time": record.get("time")}
        elif kind == "error":
            return {"cat": "ERROR", "title": "ERROR",
                    "detail": console_mod._brief(record.get("message", ""), 300),
                    "ts": stamp, "seq": record.get("seq"), "time": record.get("time")}
        return None

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
            out.append({"cat": "INFER", "title": "INFER  call #%d  Sent to model" % index,
                        "detail": _request_summary(self.run_dir, index), "ts": self._now()})
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
            if meta.get("error") or (isinstance(meta.get("status"), int) and meta.get("status") >= 400):
                out.append({"cat": "ERROR", "title": "INFER  call #%s  FAILED" % meta.get("index"),
                            "detail": "%s %s status=%s %s" % (meta.get("method"), meta.get("path"),
                                                             meta.get("status"), meta.get("error", "")),
                            "ts": stamp})
            else:
                seconds = meta.get("seconds")
                out.append({"cat": "INFER", "title": "INFER  call #%s  %s in %ss" % (
                    meta.get("index"), meta.get("status"),
                    ("%.2f" % seconds) if seconds is not None else "?"),
                    "detail": "%s %s" % (meta.get("method"), meta.get("path")), "ts": stamp})
        return out


def resolve_session_dir(run_dir: Path) -> Path | None:
    for key in ("session.json",):
        try:
            session_id = json.loads((run_dir / key).read_text(encoding="utf-8")).get("id")
        except (OSError, ValueError):
            continue
        if session_id:
            candidate = Path(__file__).resolve().parents[2] / "runtime" / session_id
            if candidate.is_dir():
                return candidate
    # Fallback: session id from run.json (runtime/<session> layout used by codex runs).
    try:
        session_id = json.loads((run_dir / "run.json").read_text(encoding="utf-8")).get("sessionId")
    except (OSError, ValueError):
        session_id = None
    if session_id:
        candidate = Path(__file__).resolve().parents[2] / "runtime" / session_id
        if candidate.is_dir():
            return candidate
    return None
