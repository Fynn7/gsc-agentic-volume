"""Coding-agent transcript: run evidence -> the blocks a coding agent shows.

The TUI shows one conversation with the run: the task the user asked for, the
model's thinking, the tool calls it made (with the arguments and what came
back), the frames it produced, its answer and the errors. The raw evidence
(harness.jsonl, session.json, the http records) is folded into those blocks
here, so the screen reads like a coding agent app instead of a log table.

Pure functions plus one small stateful builder - no Textual, no files, no
model - so the wording can be checked without a run.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
for _extra in (_ROOT / "scripts", Path(__file__).resolve().parent):
    if str(_extra) not in sys.path:
        sys.path.insert(0, str(_extra))

import event_text as text_mod

# The block kinds the transcript can hold. The UI maps every kind to one
# line style, so a new kind is one place to add.
USER = "user"
THINKING = "thinking"
ASSISTANT = "assistant"
TOOL = "tool"
IMAGE = "image"
NOTICE = "notice"
WARN = "warn"
ERROR = "error"
FINISH = "finish"
META = "meta"

# What the agent says when it calls the renderer, in one line.
TOOL_MEANING = {
    "inspect_scene": "read the scene",
    "render_scene": "render a frame",
    "inspect_slice": "read a slice",
    "finish_task": "save the result",
}


def brief(text, limit=200) -> str:
    """One line, capped - the transcript never wraps a single line twice."""
    return text_mod.brief(text, limit)


def tool_summary(name: str, arguments: dict) -> str:
    """The one-line summary of a tool call: what it changed, in numbers."""
    arguments = arguments if isinstance(arguments, dict) else {}
    if name == "inspect_slice":
        return "axis %s, index %s" % (arguments.get("axis", "?"),
                                      arguments.get("index", "?"))
    if name == "finish_task":
        return "frame revision %s" % arguments.get("frameRevision", "?")
    changed = text_mod.parameter_lines(arguments.get("parameters"))
    if changed:
        return ", ".join(changed[:2])
    rest = {key: value for key, value in arguments.items()
            if key not in ("parameters", "reason")}
    if rest:
        return brief(json.dumps(rest, ensure_ascii=False, sort_keys=True), 120)
    return TOOL_MEANING.get(name, "call the renderer")


def tool_detail(name: str, arguments: dict) -> str:
    """The lines under a tool call: the agent's reason and the change."""
    arguments = arguments if isinstance(arguments, dict) else {}
    lines = []
    changed = text_mod.parameter_lines(arguments.get("parameters"))
    if changed:
        lines.append("changed: %s" % ", ".join(changed))
    reason = arguments.get("reason")
    if reason:
        lines.append("why: %s" % brief(reason, 300))
    if name == "finish_task" and arguments.get("summary"):
        lines.append("summary: %s" % brief(arguments.get("summary"), 300))
    return "\n".join(lines)


def tool_error_detail(message: str, changes=None) -> str:
    """Why the renderer refused the call, in plain words."""
    lines = []
    if changes:
        lines.append("refused change: %s" % ", ".join(changes))
    hint = text_mod.ERROR_HINT.get(str(message or "").strip())
    if hint:
        lines.append(hint)
    lines.append(str(message or ""))
    return "\n".join(lines)


def image_caption(source: str, revision, lit, size) -> str:
    """What a frame block says above the picture."""
    if source == "slice":
        return "slice %s=%s" % (revision, lit)
    parts = ["frame revision %s" % revision]
    if lit not in (None, "?"):
        parts.append("%s lit px" % lit)
    if size and size != "?":
        parts.append(str(size))
    return " · ".join(parts)


class Transcript:
    """The conversation with one run.

    Blocks are appended as the evidence arrives and updated in place when the
    same thing is reported twice (the harness and the service log both see a
    tool call) or when a running call finishes.
    """

    def __init__(self):
        self.blocks: list[dict] = []
        self._thinking = None      # the open "Thinking" block
        self._tools: dict[str, dict] = {}   # tool name -> open tool block
        self._answer_text = ""    # the answer as the model stream delivers it

    # -- block helpers ---------------------------------------------------
    def clear(self) -> None:
        self.blocks = []
        self._thinking = None
        self._tools = {}
        self._answer_text = ""

    def add(self, kind: str, title: str = "", subtitle: str = "", body: str = "",
            status: str | None = None, ts: float | None = None,
            data: dict | None = None) -> dict:
        block = {
            "kind": kind,
            "title": title,
            "subtitle": subtitle,
            "body": body,
            "status": status,
            "ts": ts if ts is not None else time.time(),
            "data": data or {},
        }
        self.blocks.append(block)
        return block

    def find(self, kind: str) -> dict | None:
        """The newest block of a kind (the one on screen right now)."""
        for block in reversed(self.blocks):
            if block["kind"] == kind:
                return block
        return None

    def last_tool(self, name: str) -> dict | None:
        for block in reversed(self.blocks):
            if block["kind"] == TOOL and block["title"] == name:
                return block
        return None

    def _last_assistant(self) -> dict | None:
        """The newest answer block (the one the model is writing)."""
        for block in reversed(self.blocks):
            if block["kind"] == ASSISTANT:
                return block
        return None

    def _tool_blocks(self, name: str, source: str) -> list[dict]:
        return [block for block in self.blocks
                if block["kind"] == TOOL and block["title"] == name
                and (block.get("data") or {}).get("source") == source]

    def _has_tool(self, name: str, source: str) -> bool:
        return bool(self._tool_blocks(name, source))

    # -- folding the evidence in ----------------------------------------
    def ingest(self, event: dict) -> list[dict]:
        """Fold one event into the transcript.

        Returns the blocks that were added or changed, so the UI can mount
        and re-render exactly those.
        """
        kind = str(event.get("kind") or "")
        handler = getattr(self, "_ingest_" + kind, None)
        if handler is None:
            return []
        try:
            return handler(event) or []
        except Exception:
            return []

    def _ingest_thinking_start(self, event: dict) -> list[dict]:
        block = self.add(THINKING, title="Thinking", status="running",
                         ts=event.get("ts"))
        self._thinking = block
        return [block]

    def _ingest_thinking_end(self, event: dict) -> list[dict]:
        block = self._thinking
        if block is None:
            block = self.add(THINKING, title="Thinking", status="done",
                             ts=event.get("ts"))
        usage = event.get("usage") or {}
        tokens = "in %s, out %s tokens" % (
            text_mod.count(usage.get("input_tokens")),
            text_mod.count(usage.get("output_tokens")))
        block["status"] = "done"
        block["subtitle"] = tokens
        self._thinking = None
        return [block]

    def _ingest_thinking(self, event: dict) -> list[dict]:
        """The model's own reason for the next call (its reasoning)."""
        reason = str(event.get("reason") or "").strip()
        block = self._thinking_block(event.get("ts"))
        if reason and not block.get("data", {}).get("reasoning"):
            # The real reasoning (when the model streams it) wins over the
            # reason the agent states for the next call.
            block["body"] = brief(reason, 600)
        tool = event.get("tool")
        if tool:
            block["data"]["next"] = tool
        return [block]

    def _thinking_block(self, ts=None) -> dict:
        """The open Thinking block (created when the model starts working)."""
        block = self._thinking
        if block is None:
            last = self.find(THINKING)
            if last is not None and not str(last.get("body") or "").strip():
                # The turn that is already on screen is the one the model is
                # thinking in - do not start a second one.
                block = last
                block["status"] = "running"
            else:
                block = self.add(THINKING, title="Thinking", status="running", ts=ts)
            self._thinking = block
        return block

    def _ingest_reasoning_delta(self, event: dict) -> list[dict]:
        """A piece of the model's thinking: the block grows with it."""
        delta = str(event.get("delta") or "")
        if not delta:
            return []
        block = self._thinking_block(event.get("ts"))
        block["body"] = (block.get("body") or "") + delta
        block["data"]["reasoning"] = True
        return [block]

    def _ingest_reasoning_text(self, event: dict) -> list[dict]:
        """The words the model wrote before it called a tool."""
        delta = str(event.get("delta") or "")
        if not delta:
            return []
        block = self._thinking_block(event.get("ts"))
        block["body"] = (block.get("body") or "") + delta
        block["data"]["reasoning"] = True
        return [block]

    def _ingest_reasoning_done(self, event: dict) -> list[dict]:
        """The thinking is complete: the block holds the whole chain."""
        text = str(event.get("text") or "").strip()
        if not text:
            return []
        block = self._thinking_block(event.get("ts"))
        block["body"] = text
        block["data"]["reasoning"] = True
        return [block]

    def _ingest_text_delta(self, event: dict) -> list[dict]:
        """A piece of the answer: the block on screen grows with it.

        The model stream and the harness both report the answer. Whichever
        arrives first owns the block; the other one is recognised and does not
        write it twice. The words the model writes before a tool call are its
        thinking - they move into the Thinking block when the call arrives.
        """
        delta = str(event.get("delta") or "")
        if not delta:
            return []
        self._answer_text += delta
        last = self._last_assistant()
        if last is not None:
            if (last.get("data") or {}).get("open"):
                last["title"] = (last.get("title") or "") + delta
                return [last]
            if str(last.get("title") or "").startswith(self._answer_text):
                # The answer is already on screen (the harness reported it).
                return []
        block = self.add(ASSISTANT, title=delta, ts=event.get("ts"))
        block["data"]["open"] = True
        return [block]

    def _move_open_answer_to_thinking(self) -> None:
        """The words before a tool call are the model's thinking.

        The model says what it is about to do ("let me inspect the scene")
        and then calls the tool. Those words are its thinking, not its
        answer, so they belong in the Thinking block.
        """
        last = self._last_assistant()
        if last is None:
            return
        if not (last.get("data") or {}).get("open"):
            return  # a finished answer stays an answer
        text = (last.get("title") or "").strip()
        if not text:
            return
        self.blocks.remove(last)
        self._answer_text = ""
        block = self._thinking_block()
        block["body"] = ((block.get("body") or "") + text).strip()
        block["data"]["reasoning"] = True

    def _ingest_message_done(self, event: dict) -> list[dict]:
        """The finished answer: the streamed block is complete."""
        text = str(event.get("text") or "").strip()
        last = self._last_assistant()
        if last is not None:
            if (last.get("data") or {}).get("open"):
                if text:
                    last["title"] = text
                last["data"]["open"] = False
                self._answer_text = ""
                return [last]
            if text and str(last.get("title") or "").strip() == text:
                self._answer_text = ""
                return []
        self._answer_text = ""
        if not text:
            return []
        block = self.add(ASSISTANT, title=text, ts=event.get("ts"))
        return [block]

    def _ingest_assistant(self, event: dict) -> list[dict]:
        """The model's answer, as the harness reports it.

        When the provider streams the answer, the chunks arrive first and
        this event closes the block that is already on screen.
        """
        text = str(event.get("text") or "").strip()
        if not text:
            return []
        last = self._last_assistant()
        if last is not None:
            if (last.get("data") or {}).get("open"):
                last["title"] = text
                last["data"]["open"] = False
                return [last]
            if str(last.get("title") or "").strip() == text:
                # The stream already wrote this answer.
                return []
        block = self.add(ASSISTANT, title=text, ts=event.get("ts"))
        return [block]

    def _ingest_assistant_update(self, event: dict) -> list[dict]:
        """A streamed chunk: the answer on screen grows with every chunk."""
        text = str(event.get("text") or "").strip()
        if not text:
            return []
        last = self._last_assistant()
        if last is not None and (last.get("data") or {}).get("open"):
            last["title"] = text
            return [last]
        block = self.add(ASSISTANT, title=text, ts=event.get("ts"))
        block["data"]["open"] = True
        return [block]

    def _ingest_tool_call(self, event: dict) -> list[dict]:
        """One tool call.

        Three sources see the call: the model stream (the moment the model
        decides), the service log (the authoritative one) and the harness.
        The call is shown exactly once: the strongest source wins.
        """
        name = str(event.get("name") or "tool")
        arguments = event.get("arguments") or {}
        source = str(event.get("source") or "service")
        # The words the model wrote before the call are its thinking.
        self._move_open_answer_to_thinking()
        if source != "service" and self._has_tool(name, "service"):
            # The service log already showed this call.
            return []
        block = self._tools.get(name)
        if block is None:
            last = self.last_tool(name)
            if last is not None and (last.get("data") or {}).get("source") in ("harness", "stream"):
                # Take over the block a weaker source opened.
                block = last
            else:
                block = self.add(TOOL, title=name, status="running", ts=event.get("ts"))
        block["data"]["source"] = source
        self._tools[name] = block
        block["subtitle"] = tool_summary(name, arguments)
        block["body"] = tool_detail(name, arguments)
        block["data"]["changes"] = text_mod.parameter_lines(arguments.get("parameters"))
        return [block]

    def _ingest_tool_result(self, event: dict) -> list[dict]:
        name = str(event.get("name") or "tool")
        source = str(event.get("source") or "service")
        seconds = event.get("seconds")
        block = self._tools.get(name)
        if (source != "service" and block is not None
                and (block.get("data") or {}).get("source") == "service"):
            # The service log already closed this call.
            return []
        if block is None:
            block = self.last_tool(name)
        if block is None:
            block = self.add(TOOL, title=name, status="done", ts=event.get("ts"))
        block["status"] = "done"
        if isinstance(seconds, (int, float)) and not isinstance(seconds, bool):
            block["data"]["seconds"] = seconds
        self._tools.pop(name, None)
        return [block]

    def _ingest_tool_error(self, event: dict) -> list[dict]:
        name = str(event.get("name") or "tool")
        source = str(event.get("source") or "service")
        message = str(event.get("message") or "")
        block = self._tools.get(name)
        if (source != "service" and block is not None
                and (block.get("data") or {}).get("source") == "service"):
            return []
        if block is None:
            block = self.last_tool(name)
        if block is None:
            block = self.add(TOOL, title=name, status="failed", ts=event.get("ts"))
        block["status"] = "failed"
        block["body"] = tool_error_detail(message, block.get("data", {}).get("changes"))
        self._tools.pop(name, None)
        return [block]

    def _ingest_image(self, event: dict) -> list[dict]:
        block = self.add(IMAGE, title=image_caption(
            event.get("source", "render"), event.get("revision"),
            event.get("lit"), event.get("size")),
            subtitle=str(event.get("image") or ""),
            ts=event.get("ts"),
            data={"image": event.get("image"), "revision": event.get("revision")})
        return [block]

    def _ingest_notice(self, event: dict) -> list[dict]:
        return [self.add(NOTICE, title=str(event.get("text") or ""),
                         ts=event.get("ts"))]

    def _ingest_warn(self, event: dict) -> list[dict]:
        return [self.add(WARN, title=str(event.get("message") or ""),
                         ts=event.get("ts"))]

    def _ingest_error(self, event: dict) -> list[dict]:
        return [self.add(ERROR, title=str(event.get("message") or ""),
                         ts=event.get("ts"))]

    def _ingest_finish(self, event: dict) -> list[dict]:
        revision = event.get("revision", "?")
        verification = event.get("verification", "?")
        title = "Run finished · picked frame revision %s · task check: %s" % (
            revision, verification)
        body = ""
        claim = str(event.get("claim") or "").strip()
        if claim:
            body = "the agent's claim: %s" % brief(claim, 400)
        return [self.add(FINISH, title=title, body=body, status="done",
                         ts=event.get("ts"))]

    def _ingest_meta(self, event: dict) -> list[dict]:
        return [self.add(META, title=str(event.get("text") or ""),
                         ts=event.get("ts"))]

    def _ingest_done(self, event: dict) -> list[dict]:
        """The model call is over: the next stream starts a new answer."""
        self._answer_text = ""
        return []

    def _ingest_tool_args_delta(self, event: dict) -> list[dict]:
        """The arguments of a tool call, piece by piece.

        The finished call carries the whole argument set, so the pieces are
        not needed - they only tell that the model is still writing.
        """
        return []
