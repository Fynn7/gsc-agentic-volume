"""The recorded model stream -> the chunks the conversation shows.

The launcher records every model call exactly as it received it: the raw SSE
stream in `http/http-NNN.response.bin`, written chunk by chunk while the model
is still answering. The TUI tails those files, so the answer and the model's
reasoning reach the screen while they are being produced - the way a coding
agent app shows them.

Pure functions plus one small tailing helper - no Textual, no model.
"""
from __future__ import annotations

import json
import re as _re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
for _extra in (_ROOT / "scripts", Path(__file__).resolve().parent):
    if str(_extra) not in sys.path:
        sys.path.insert(0, str(_extra))

# The events of the OpenAI Responses API this app records. The reasoning
# events only appear when the model is asked to think (settings: Think).
_HTTP_INDEX = _re.compile(r"http-(\d+)")


def _http_index(path) -> int:
    """The model call number of a recorded http file (http-NNN.*)."""
    match = _HTTP_INDEX.search(str(getattr(path, "name", path)))
    return int(match.group(1)) if match else 0
_TEXT_DELTA = "response.output_text.delta"
_REASONING_DELTAS = ("response.reasoning_summary_text.delta",
                     "response.reasoning_text.delta")
_ARGUMENTS_DELTA = "response.function_call_arguments.delta"
_ITEM_DONE = "response.output_item.done"
_TEXT_DONE = "response.output_text.done"
_COMPLETED = "response.completed"


def _arguments(raw) -> dict:
    """The arguments of a tool call as a dict (the stream sends JSON text)."""
    if isinstance(raw, dict):
        return raw
    text = str(raw or "").strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except ValueError:
        return {"arguments": text}
    return parsed if isinstance(parsed, dict) else {"arguments": parsed}


class SseParser:
    """Parses the recorded SSE stream, chunk by chunk.

    The parser keeps the state of the turn: the words the model writes
    before it calls a tool are its thinking, the words after the call are
    its answer. The state has to survive between the chunks, because the
    file grows while the model is still answering.
    """

    def __init__(self):
        self._buffer = ""

    def feed(self, text: str) -> list[dict]:
        """The events of the new text (the half-written one waits)."""
        self._buffer += text
        parts = self._buffer.split("\n\n")
        # The last part is still being written: it waits for the next chunk.
        self._buffer = parts.pop()
        events: list[dict] = []
        for block in parts:
            events.extend(self._block(block))
        return events

    def _block(self, block: str) -> list[dict]:
        events: list[dict] = []
        for line in block.splitlines():
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if not payload or payload == "[DONE]":
                continue
            try:
                data = json.loads(payload)
            except ValueError:
                continue
            kind = data.get("type")
            if kind == _TEXT_DELTA:
                events.append({"kind": "text_delta", "delta": data.get("delta", "")})
            elif kind in _REASONING_DELTAS:
                events.append({"kind": "reasoning_delta", "delta": data.get("delta", "")})
            elif kind == _ITEM_DONE:
                item = data.get("item") or {}
                if item.get("type") == "function_call":
                    events.append({
                        "kind": "tool_call",
                        "name": item.get("name", "tool"),
                        "arguments": _arguments(item.get("arguments")),
                        "id": item.get("id"),
                        "source": "stream",
                    })
            elif kind == _TEXT_DONE:
                events.append({"kind": "message_done", "text": data.get("text", "")})
            elif kind == _COMPLETED:
                events.append({"kind": "done"})
        return events


def parse_sse(text: str) -> list[dict]:
    """The chunks of one recorded model response.

    Every "data:" line of the stream holds one JSON event. The events are
    normalised into the few kinds the conversation knows:

    - text_delta: a piece of the answer
    - reasoning_delta: a piece of the reasoning the model streamed (the
      reasoning summary of the model service)
    - tool_call: the finished tool call (name + arguments)
    - message_done: the finished answer
    - done: the model call is over
    """
    return SseParser().feed(text)


class StreamTail:
    """Tails the recorded model streams of one run.

    The files grow while the model answers, so the tail reads only what is
    new and never parses a half-written event. Every event carries the model
    call it came from (`call`, the http-NNN index) and whether that call is
    a context checkpoint compaction (`compaction`), read from the recorded
    request body — so the transcript can keep one thinking block per call
    and give the compaction summary its own row.
    """

    # The marker the launcher forwards when the model call is a context
    # checkpoint compaction, not a step of the run.
    COMPACTION_MARKER = "CONTEXT CHECKPOINT COMPACTION"

    def __init__(self, run_dir: Path):
        self.run_dir = Path(run_dir)
        self._offsets: dict[str, int] = {}
        self._parsers: dict[str, SseParser] = {}
        self._compaction: dict[str, bool] = {}
        self._counts: dict[str, int] = {}   # streamed events emitted per file
        self._last: dict[str, float] = {}   # last stamp per file (monotonic)
        self._windows: dict[int, tuple] = {}  # model call -> (started, ended)

    def _is_compaction(self, key: str) -> bool:
        """Is this recorded model call a context checkpoint compaction?"""
        if key in self._compaction:
            return self._compaction[key]
        index = _http_index(Path(key))
        found = False
        for name in ("http-%03d.forwarded.bin" % index, "http-%03d.request.bin" % index):
            path = self.run_dir / "http" / name
            try:
                if path.stat().st_size > 32 * 1024 * 1024:
                    continue
                head = path.read_bytes()[:4 * 1024 * 1024]
            except OSError:
                continue
            if self.COMPACTION_MARKER.encode("utf-8") in head:
                found = True
                break
        self._compaction[key] = found
        return found

    def _call_window(self, call: int) -> tuple[float | None, float | None]:
        """(sent, reply received) of one recorded model call, run clock.

        The streamed chunks arrive between the two; stamping them inside
        the window keeps the merge with the tool and transport evidence in
        execution order instead of piling every stream block at poll time.
        """
        if call in self._windows:
            return self._windows[call]
        started: float | None = None
        ended: float | None = None
        try:
            meta = json.loads(
                (self.run_dir / "http" / ("http-%03d.metadata.json" % call)).read_text(
                    encoding="utf-8"))
            if isinstance(meta, dict):
                raw_start, raw_end = meta.get("started"), meta.get("ended")
                if isinstance(raw_start, (int, float)) and not isinstance(raw_start, bool):
                    started = float(raw_start)
                if isinstance(raw_end, (int, float)) and not isinstance(raw_end, bool):
                    ended = float(raw_end)
        except (OSError, ValueError):
            pass
        if started is None:
            for name in ("http-%03d.request.bin" % call, "http-%03d.response.bin" % call):
                try:
                    started = float((self.run_dir / "http" / name).stat().st_mtime)
                    break
                except OSError:
                    continue
        self._windows[call] = (started, ended)
        return started, ended

    def poll(self) -> list[dict]:
        """The chunks that arrived since the last poll."""
        events: list[dict] = []
        directory = self.run_dir / "http"
        try:
            paths = sorted(directory.glob("http-*.response.bin"))
        except OSError:
            return events
        for path in paths:
            events.extend(self._read(path))
        return events

    def _read(self, path: Path) -> list[dict]:
        key = path.name
        parser = self._parsers.setdefault(key, SseParser())
        try:
            with path.open("r", encoding="utf-8", errors="replace") as handle:
                handle.seek(self._offsets.get(key, 0))
                data = handle.read()
                self._offsets[key] = handle.tell()
        except OSError:
            return []
        if not data:
            return []
        import time as _time
        call = _http_index(path)
        compaction = self._is_compaction(key)
        started, ended = self._call_window(call)
        try:
            fallback = float(path.stat().st_mtime)
        except OSError:
            fallback = _time.time()
        base = started if started is not None else fallback
        ceiling = ended if ended is not None else None
        out = []
        for event in parser.feed(data):
            count = self._counts.get(key, 0) + 1
            self._counts[key] = count
            stamp = base + count * 0.001
            if ceiling is not None and stamp > ceiling:
                stamp = ceiling
            last = self._last.get(key, 0.0)
            if stamp <= last:
                stamp = last + 0.001
                if ceiling is not None and stamp > ceiling:
                    stamp = ceiling
            self._last[key] = stamp
            out.append(dict(event, call=call, ts=stamp, time=stamp,
                            **({"compaction": True} if compaction else {})))
        return out
