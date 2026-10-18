"""Structured event tailing that reuses agent_console semantics.

Same three sources as scripts/agent_console.py (harness.jsonl,
runtime/<session>/events.jsonl, http/*.metadata.json), same dedup/order
(harness -> events -> http per poll), same partial-line handling: the last
line without a trailing newline is buffered, never parsed. No base64 bodies
are ever loaded; request/response *.bin files are only counted, and pixel
matching stays inside verify_local_agent.py.
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


class EventTail:
    """Incremental, read-only tail producing compact display dicts."""

    def __init__(self, run_dir: Path, session_dir: Path | None = None):
        self.run_dir = Path(run_dir)
        self.session_dir = Path(session_dir) if session_dir else None
        self._files: dict[str, dict] = {}
        self._seen_request: set[str] = set()
        self._seen_metadata: set[str] = set()
        self._turn = 0
        self.counters = {"images": 0, "tools": 0, "http": 0}

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
            kind = record.get("type")
            if kind == "turn.started":
                self._turn += 1
                out.append({"cat": "MODEL", "title": "MODEL #%d  Running" % self._turn,
                            "detail": "turn started"})
            elif kind == "turn.completed":
                usage = record.get("usage") or {}
                if self._turn > 0:
                    out.append({"cat": "MODEL", "title": "MODEL #%d  Done" % self._turn,
                            "detail": "input=%s cached=%s output=%s" % (
                                usage.get("input_tokens"), usage.get("cached_input_tokens"),
                                usage.get("output_tokens"))})
                else:
                    out.append({"cat": "MODEL", "title": "MODEL  Done (turn count unknown)",
                            "detail": "input=%s cached=%s output=%s" % (
                                usage.get("input_tokens"), usage.get("cached_input_tokens"),
                                usage.get("output_tokens"))})
            elif kind == "item.completed":
                item = record.get("item") or {}
                if item.get("type") == "agent_message":
                    out.append({"cat": "MODEL", "title": "MODEL  message",
                                "detail": console_mod._brief(item.get("text", ""), 500)})
                elif item.get("type") == "error":
                    out.append({"cat": "ERROR", "title": "WARN  harness",
                                "detail": console_mod._brief(item.get("message", ""), 320)})
        return out

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
            kind = record.get("kind")
            if kind in ("initial_render", "render", "slice"):
                result = record.get("result") or {}
                image = result.get("image")
                scene = result.get("scene") or {}
                rev = scene.get("revision")
                self.counters["images"] += 1
                label = ("Frame %d" % self.counters["images"]) if kind != "slice" else "Slice"
                out.append({"cat": "IMAGE", "title": "IMAGE %s  Native PNG saved" % label,
                            "detail": "rev=%s nonblack=%s\n%s" % (
                                rev, result.get("nonblackPixels"), image),
                            "image": image, "revision": rev})
            elif kind == "external_tool_call":
                self.counters["tools"] += 1
                out.append({"cat": "TOOL", "title": "TOOL  %s  Started" % record.get("name"),
                            "detail": console_mod._brief(record.get("arguments", {}), 240)})
            elif kind == "external_tool_return":
                try:
                    seconds = " %.2fs" % float(record["seconds"])
                except (KeyError, TypeError, ValueError):
                    seconds = ""
                out.append({"cat": "TOOL", "title": "TOOL  %s  Success (%s)" % (record.get("name"), seconds.strip() or "?"),
                            "detail": ""})
            elif kind == "tool_error":
                out.append({"cat": "ERROR", "title": "TOOL  %s  Failed" % record.get("name"),
                            "detail": console_mod._brief(record.get("message", ""), 300)})
            elif kind == "finish":
                out.append({"cat": "FINISH", "title": "FINISH  selected revision %s" % record.get("selectedRevision"),
                            "detail": "verification=%s claim: %s" % (
                                record.get("externalTaskVerification"),
                                console_mod._brief(record.get("modelClaim", ""), 400))})
            elif kind == "error":
                out.append({"cat": "ERROR", "title": "ERROR",
                            "detail": console_mod._brief(record.get("message", ""), 300)})
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
            out.append({"cat": "INFER", "title": "INFER  call #%d  Waiting for model" % _http_index(path),
                        "detail": "request sent; elapsed time shown live, no fake percent"})
        for path in sorted(directory.glob("http-*.metadata.json"), key=_http_index):
            if path.name in self._seen_metadata:
                continue
            self._seen_metadata.add(path.name)
            try:
                meta = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            self.counters["http"] += 1
            if meta.get("error") or (isinstance(meta.get("status"), int) and meta.get("status") >= 400):
                out.append({"cat": "ERROR", "title": "INFER  call #%s  FAILED" % meta.get("index"),
                            "detail": "%s %s status=%s %s" % (meta.get("method"), meta.get("path"),
                                                             meta.get("status"), meta.get("error", ""))})
            else:
                seconds = meta.get("seconds")
                out.append({"cat": "INFER", "title": "INFER  call #%s  %s in %ss" % (
                    meta.get("index"), meta.get("status"),
                    ("%.2f" % seconds) if seconds is not None else "?"),
                    "detail": "%s %s" % (meta.get("method"), meta.get("path"))})
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
