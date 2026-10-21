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
NOTICES = "notices"
WARN = "warn"
ERROR = "error"
FINISH = "finish"
META = "meta"

# The wording of every tool call lives in event_text, so the screen and the
# event log can never drift apart.


def brief(text, limit=200) -> str:
    """One line, capped - the transcript never wraps a single line twice."""
    return text_mod.brief(text, limit)


def tool_summary(name: str, arguments: dict) -> str:
    """The facts of the call: what goes in, what comes out, what changes."""
    arguments = arguments if isinstance(arguments, dict) else {}
    facts = text_mod.tool_facts(name, arguments)
    if facts:
        return facts
    rest = {key: value for key, value in arguments.items()
            if key not in ("parameters", "reason")}
    if rest:
        return brief(json.dumps(rest, ensure_ascii=False, sort_keys=True), 120)
    return ""


def tool_detail(name: str, arguments: dict) -> str:
    """The lines under a tool call: the model's own words, then the facts.

    The row above already says what the call does; the body only adds what
    the model said and what the changed numbers mean.
    """
    lines = text_mod.model_words(name, arguments)
    lines.extend(text_mod.effect_lines(arguments.get("parameters")))
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
        share = _lit_share(lit, size)
        if share:
            parts.append("%s lit px (%s of the picture)" % (lit, share))
        else:
            parts.append("%s lit px" % lit)
    if size and size != "?":
        parts.append(str(size))
    return " · ".join(parts)


def _lit_share(lit, size) -> str:
    """How much of the picture is not black, in percent ("56.6%")."""
    try:
        lit_value = float(lit)
        width, height = str(size).lower().split("x", 1)
        total = float(width) * float(height)
    except (TypeError, ValueError, ZeroDivisionError):
        return ""
    if total <= 0:
        return ""
    return "%.1f%%" % (100.0 * lit_value / total)


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
        self._notices: list[str] = []   # the harness notices, one row for all

    # -- block helpers ---------------------------------------------------
    def clear(self) -> None:
        self.blocks = []
        self._thinking = None
        self._tools = {}
        self._answer_text = ""
        self._notices = []

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
        """The open (or last) block of one tool call.

        The title is the step in words, so the tool name lives in the data.
        """
        for block in reversed(self.blocks):
            if block["kind"] != TOOL:
                continue
            if (block.get("data") or {}).get("tool") == name:
                return block
            if block.get("title") == name:  # a block from before the rewrite
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
                if block["kind"] == TOOL
                and (block.get("data") or {}).get("tool") == name
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
        """The agent's reason for the next call: the model's own words.

        It is not reasoning, so it does not go into the foldable block: it
        is a line of the model's own, like its answer.
        """
        reason = str(event.get("reason") or "").strip()
        if not reason:
            return []
        return [self.add(ASSISTANT, title=reason, ts=event.get("ts"))]

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
        """The words the model wrote before it called a tool.

        When the model really reasoned, those words join the reasoning:
        the foldable block is the only place they belong. When it did not
        (Think is off) there is no foldable block - the words stay a line
        of the model's own, right where they were written.
        """
        last = self._last_assistant()
        if last is None:
            return
        if not (last.get("data") or {}).get("open"):
            return  # a finished answer stays an answer
        text = (last.get("title") or "").strip()
        if not text:
            return
        block = self._thinking
        if block is not None and (block.get("data") or {}).get("reasoning"):
            self.blocks.remove(last)
            self._answer_text = ""
            block["body"] = ((block.get("body") or "") + text).strip()
            block["data"]["preamble"] = True
            return
        # No reasoning: the line stays, closed - there is nothing to open.
        last["data"]["open"] = False

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
        block["data"]["tool"] = name
        self._tools[name] = block
        # The row leads with the step in words; the tool name stays in the
        # data, so the screen can show it quietly next to the sentence.
        block["title"] = text_mod.tool_action(name, arguments)
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
            block = self.add(TOOL, title=text_mod.tool_action(name),
                             status="done", ts=event.get("ts"))
            block["data"]["tool"] = name
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
            block = self.add(TOOL, title=text_mod.tool_action(name),
                             status="failed", ts=event.get("ts"))
            block["data"]["tool"] = name
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
        """The harness notices: one row for all of them.

        The harness reports its own housekeeping (an under-development
        feature, a missing model description). Those are not errors and they
        do not stop the run, so they get one quiet row instead of a wall of
        jargon at the top of the screen.
        """
        message = str(event.get("message") or "")
        if not message:
            return []
        self._notices.append(message)
        block = self.find(NOTICES)
        if block is None:
            block = self.add(NOTICES, ts=event.get("ts"))
        block["title"] = text_mod.notice_headline(len(self._notices))
        block["body"] = "\n".join(text_mod.brief(item, 300) for item in self._notices)
        block["data"]["notices"] = list(self._notices)
        return [block]

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
            body = brief(claim, 400)
        return [self.add(FINISH, title=title, body=body, status="done",
                         ts=event.get("ts"))]

    def _ingest_meta(self, event: dict) -> list[dict]:
        """Plumbing (the model calls, the HTTP status).

        The evidence stays complete, but the screen hides it by default: a
        reader who never saw this system reads the steps, not the transport.
        """
        block = self.add(META, title=str(event.get("text") or ""),
                         ts=event.get("ts"))
        block["data"]["plumbing"] = True
        return [block]

    def _ingest_done(self, event: dict) -> list[dict]:
        """The model call is over: the next stream starts a new answer."""
        self._answer_text = ""
        return []
