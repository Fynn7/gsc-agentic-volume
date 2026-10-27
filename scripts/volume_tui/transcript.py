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
import re
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
COMPACTION = "compaction"
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

# The mark in front of every row: the task is the user's, the words are the
# model's, the rest are facts the screen measured. The screen and the chat
# log use the same marks, so a line of the log can be read on the screen.
MARKS = {
    USER: "▌",
    ASSISTANT: text_mod.MODEL_MARK,
    COMPACTION: "◈",
    TOOL: "●",
    IMAGE: "▣",
    NOTICES: "⚠",
    WARN: "⚠",
    ERROR: "✗",
    FINISH: "✓",
    NOTICE: "·",
    META: "·",
}


def block_mark(kind: str) -> str:
    """The mark in front of a row (never empty, never a guess)."""
    return MARKS.get(str(kind), "·")


def is_plumbing(kind: str) -> bool:
    """Is this the transport (the model calls, the HTTP status)?

    The screen hides it until Ctrl+H asks for it, and the chat log leaves
    it out for the same reason: it belongs to the JSON log.
    """
    return str(kind) == META


def tool_row_parts(block: dict) -> list[tuple[str, str]]:
    """The tool row as parts: (text, role).

    Roles: "mark", "title", "sub" (the subtitle), "wait" (the call is
    still running), "ok" (it returned), "fail" (the renderer refused it),
    "time" (the seconds it took). The screen paints the roles, the chat
    log writes them - one rule, two presentations.
    """
    parts = [(block_mark(TOOL) + " ", "mark"), (str(block.get("title") or ""), "title")]
    subtitle = str(block.get("subtitle") or "")
    if subtitle:
        parts.append(("  %s" % subtitle, "sub"))
    status = block.get("status")
    data = block.get("data") or {}
    if status == "running":
        parts.append(("  …", "wait"))
    elif status == "failed":
        parts.append(("  ✗", "fail"))
    elif status == "done":
        parts.append(("  ✓", "ok"))
        seconds = data.get("seconds")
        if text_mod.is_number(seconds):
            parts.append((" %ds" % int(seconds + 0.5), "time"))
    return parts


def thinking_row_parts(block: dict) -> list[tuple[str, str]]:
    """The model turn as parts: (text, role).

    Roles: "spin" (the turn is still running), "ok" (it is over), "words"
    (Thinking / Thought / Working / Model answered), "sub" (the tokens).
    """
    mark, words = thinking_headline(block)
    running = block.get("status") == "running"
    head = (str(block.get("spinner") or mark) if running else mark)
    parts = [(head, "spin" if running else "ok"), (" %s" % words, "words")]
    subtitle = str(block.get("subtitle") or "")
    if subtitle:
        parts.append((" %s" % subtitle, "sub"))
    return parts


def thinking_headline(block: dict) -> tuple[str, str]:
    """The mark and the words of a model turn.

    "Thinking" / "Thought" when the model really reasoned, "Working" /
    "Model answered" when it did not (the Think setting is off, and the
    screen never claims reasoning the model did not produce). The screen
    and the chat log say the same thing.
    """
    status = block.get("status")
    reasoned = bool((block.get("data") or {}).get("reasoning"))
    if status == "running":
        return ("✳", "Thinking" if reasoned else "Working")
    return ("✓", "Thought" if reasoned else "Model answered")


# The head of a context checkpoint compaction: the model's summary of what
# the run knew when its context filled up. It is the model's own words, but
# it is housekeeping, not an answer — so it gets its own row, never a "»" one.
_COMPACTION_HEAD = "# Context Checkpoint"
_REMAINING_BEFORE = re.compile(r"(\d+)\s+remaining observations?", re.IGNORECASE)
_REMAINING_AFTER = re.compile(r"remaining observations\*?\*?\s*:?\s*(\d+)",
                              re.IGNORECASE)


def is_checkpoint_text(text) -> bool:
    """Is this the model's compaction summary rather than its answer?"""
    return str(text or "").lstrip().startswith(_COMPACTION_HEAD)


def compaction_remaining(text) -> str | None:
    """The observations the checkpoint says are left ("24"), if it says so."""
    match = _REMAINING_BEFORE.search(str(text or ""))
    if match:
        return match.group(1)
    match = _REMAINING_AFTER.search(str(text or ""))
    return match.group(1) if match else None


def compaction_row_parts(block: dict) -> list[tuple[str, str]]:
    """The compaction row as parts: (text, role).

    Roles mirror the thinking row: "spin" (the summary is still arriving),
    "ok" (it is complete), "words" (Context checkpoint), "sub" (the
    observations left).
    """
    running = block.get("status") == "running"
    mark = "◈"
    if running:
        head = str(block.get("spinner") or mark)
        parts = [(head, "spin"), (" Context checkpoint", "words")]
    else:
        parts = [(mark, "ok"), (" Context checkpoint", "words")]
    subtitle = str(block.get("subtitle") or "")
    if subtitle:
        parts.append(("  %s" % subtitle, "sub"))
    return parts


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


def _as_epoch(value) -> float | None:
    """The value as epoch seconds; None when it is neither a number nor a time."""
    if text_mod.is_number(value):
        return float(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return float(text)
        except ValueError:
            pass
        try:
            from datetime import datetime
            return datetime.fromisoformat(text).timestamp()
        except (ValueError, OSError):
            return None
    return None


def _with_event_time(event: dict) -> dict:
    """The event with the time the step happened, when the evidence knows it.

    The service log writes it ("time", a number or an ISO timestamp); "ts"
    is only the moment the TUI read the event. The chat log shows this
    time, so a run that is opened again still says when its steps ran.
    """
    if "time" not in event:
        return event
    stamp = _as_epoch(event.get("time"))
    if stamp is None:
        return event
    stamped = dict(event)
    stamped["ts"] = stamp
    return stamped


def _thinking_elapsed(block: dict, now: float | None = None) -> str:
    """The frozen duration of a finished thinking row ("for 45s").

    The row keeps the time it took, the way the tool rows keep theirs —
    only while it runs does the screen count. The reference clock must be
    the run clock (event times), never the wall clock: a reopened run
    replays old evidence long after it happened.
    """
    started = block.get("ts") or time.time()
    if now is None:
        now = time.time()
    try:
        elapsed = max(0.0, float(now) - float(started))
    except (TypeError, ValueError):
        elapsed = 0.0
    return "for " + text_mod.format_duration(elapsed)


def run_end_ts(blocks) -> float | None:
    """Latest run-clock stamp of the transcript.

    Freezing an open row (a thinking chain the evidence never closed)
    measures against the end of the evidence, never the wall clock of a
    later replay.
    """
    best: float | None = None
    for block in blocks or []:
        stamp = (block or {}).get("ts")
        if text_mod.is_number(stamp):
            value = float(stamp)
            best = value if best is None else max(best, value)
    return best


class Transcript:
    """The conversation with one run.

    Blocks are appended as the evidence arrives and updated in place when the
    same thing is reported twice (the harness and the service log both see a
    tool call) or when a running call finishes.
    """

    def __init__(self):
        self.blocks: list[dict] = []
        self._thinking = None      # the open "Thinking" block
        self._thinking_call = None  # the model call the open block reasons about
        self._tools: dict[str, dict] = {}   # tool name -> open tool block
        self._answer_text = ""    # the answer as the model stream delivers it
        self._notices: list[str] = []   # the harness notices, one row for all

    # -- block helpers ---------------------------------------------------
    def clear(self) -> None:
        self.blocks = []
        self._thinking = None
        self._thinking_call = None
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
            return handler(_with_event_time(event)) or []
        except Exception:
            return []

    def _ingest_thinking_start(self, event: dict) -> list[dict]:
        # The recorded stream may already reason about this turn: reusing
        # the open block keeps one Thinking row no matter which source the
        # merge reads first (stream deltas use run-clock stamps too, so
        # either order folds into the same row).
        if self._thinking is not None and self._thinking.get("status") == "running":
            return [self._thinking]
        block = self.add(THINKING, title="Thinking", status="running",
                         ts=event.get("ts"))
        self._thinking = block
        return [block]

    def _ingest_thinking_end(self, event: dict) -> list[dict]:
        block = self._thinking
        if block is None:
            # The chain of the last call already closed when its tool call
            # arrived: mark it done instead of opening an empty row.
            last = self.find(THINKING)
            if last is not None:
                last["status"] = "done"
                usage = event.get("usage") or {}
                last["subtitle"] = "in %s, out %s tokens" % (
                    text_mod.count(usage.get("input_tokens")),
                    text_mod.count(usage.get("output_tokens")))
                return [last]
            block = self.add(THINKING, title="Thinking", status="done",
                             ts=event.get("ts"))
        usage = event.get("usage") or {}
        tokens = "in %s, out %s tokens" % (
            text_mod.count(usage.get("input_tokens")),
            text_mod.count(usage.get("output_tokens")))
        block["status"] = "done"
        block["subtitle"] = tokens
        self._thinking = None
        self._thinking_call = None
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

    def _thinking_block(self, ts=None, call=None) -> dict:
        """The open Thinking block (created when the model starts working).

        One model call gets one block: when the stream of a new call arrives
        while the previous block is still open, the previous chain is
        complete and a new block starts. Otherwise the whole run would read
        as a single Thinking row.
        """
        block = self._thinking
        if block is not None:
            if call is None or self._thinking_call in (None, call):
                if call is not None:
                    self._thinking_call = call
                return block
            # A new model call reasons in a new row: the old chain is over,
            # and its elapsed time freezes into the row (an ended row never
            # counts, it keeps its final duration like the tool rows do).
            # The freeze uses the run clock of the event that ended the
            # chain, never the wall clock of a later replay.
            block["status"] = "done"
            block["subtitle"] = _thinking_elapsed(
                block, now=ts if text_mod.is_number(ts) else None)
            self._thinking = None
            self._thinking_call = None
            block = None
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
            self._thinking_call = call
        return block

    def _is_compaction(self, event: dict, text) -> bool:
        """Is this text the compaction summary, not an answer?

        The stream tags the events of a compaction call (read from the
        recorded request body); the harness text is recognised by its head,
        so a reopened run folds it the same way.
        """
        return bool(event.get("compaction")) or is_checkpoint_text(text)

    def _compaction_block(self, event: dict) -> dict:
        """The open compaction row, one per compaction call."""
        call = event.get("call")
        last = None
        for block in reversed(self.blocks):
            if block["kind"] == COMPACTION:
                last = block
                break
        if (last is not None and last.get("data", {}).get("open")
                and (call is None or last.get("data", {}).get("call") == call)):
            if event.get("ts") is not None:
                last["ts"] = event["ts"]
            return last
        if last is not None and last.get("data", {}).get("open"):
            last["data"]["open"] = False
            last["status"] = "done"
        block = self.add(COMPACTION, title="Context checkpoint", status="running",
                         ts=event.get("ts"))
        block["data"]["open"] = True
        block["data"]["call"] = call
        return block

    @staticmethod
    def _stamp_compaction(block: dict) -> None:
        """The row names what the checkpoint says is left."""
        left = compaction_remaining(block.get("body") or "")
        block["subtitle"] = ("%s observations left" % left) if left else ""

    def _ingest_compaction_delta(self, event: dict, delta: str) -> list[dict]:
        block = self._compaction_block(event)
        block["body"] = (block.get("body") or "") + delta
        self._stamp_compaction(block)
        return [block]

    def _ingest_compaction_full(self, event: dict, text: str) -> list[dict]:
        """The whole summary as the harness reports it (grows per update)."""
        text = str(text or "").strip()
        if not text:
            return []
        block = self._compaction_block(event)
        if block.get("body") == text and not block.get("data", {}).get("open"):
            return []
        block["body"] = text
        self._stamp_compaction(block)
        return [block]

    def _close_compaction(self, event: dict, text: str) -> list[dict]:
        text = str(text or "").strip()
        blocks = self._ingest_compaction_full(event, text)
        for block in blocks:
            block["status"] = "done"
            block["data"]["open"] = False
        return blocks

    def _ingest_reasoning_delta(self, event: dict) -> list[dict]:
        """A piece of the model's thinking: the block grows with it."""
        delta = str(event.get("delta") or "")
        if not delta:
            return []
        block = self._thinking_block(event.get("ts"), event.get("call"))
        block["body"] = (block.get("body") or "") + delta
        block["data"]["reasoning"] = True
        return [block]

    def _ingest_reasoning_text(self, event: dict) -> list[dict]:
        """The words the model wrote before it called a tool."""
        delta = str(event.get("delta") or "")
        if not delta:
            return []
        block = self._thinking_block(event.get("ts"), event.get("call"))
        block["body"] = (block.get("body") or "") + delta
        block["data"]["reasoning"] = True
        return [block]

    def _ingest_reasoning_done(self, event: dict) -> list[dict]:
        """The thinking is complete: the block holds the whole chain."""
        text = str(event.get("text") or "").strip()
        if not text:
            return []
        block = self._thinking_block(event.get("ts"), event.get("call"))
        block["body"] = text
        block["data"]["reasoning"] = True
        return [block]

    def _ingest_text_delta(self, event: dict) -> list[dict]:
        """A piece of the answer: the block on screen grows with it.

        The model stream and the harness both report the answer. Whichever
        arrives first owns the block; the other one is recognised and does not
        write it twice. The words the model writes before a tool call are its
        thinking - they move into the Thinking block when the call arrives.
        A compaction summary is not an answer: it grows its own row.
        """
        delta = str(event.get("delta") or "")
        if not delta:
            return []
        if self._is_compaction(event, delta):
            return self._ingest_compaction_delta(event, delta)
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
        if self._is_compaction(event, text):
            return self._close_compaction(event, text)
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
        if self._is_compaction(event, text):
            return self._ingest_compaction_full(event, text)
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
        if self._is_compaction(event, text):
            return self._ingest_compaction_full(event, text)
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
        # The service log knows when the call happened: the block carries
        # that time, not only the moment the TUI read it.
        stamp = event.get("ts")
        if stamp is not None:
            block["ts"] = stamp
        block["data"]["source"] = source
        block["data"]["tool"] = name
        self._tools[name] = block
        # The row leads with the step in words (present tense: it is still
        # running); the tool name stays in the data, so the screen can show
        # it quietly next to the sentence.
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
            block = self.add(TOOL, title=text_mod.tool_action_done(name),
                             status="done", ts=event.get("ts"))
            block["data"]["tool"] = name
        block["status"] = "done"
        # The work is over: the row says what was done, in the past tense.
        block["title"] = text_mod.tool_action_done(name)
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
            block = self.add(TOOL, title=text_mod.tool_action_done(name),
                             status="failed", ts=event.get("ts"))
            block["data"]["tool"] = name
        block["status"] = "failed"
        block["title"] = text_mod.tool_action_done(name)
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
        The row is filed by run-clock time, not by poll order, so a reopened
        run interleaves "model call #N" ahead of the work of call N and
        "HTTP ..." right after it instead of piling every transport row at
        the end of the conversation.
        """
        stamp = event.get("ts")
        if not text_mod.is_number(stamp):
            stamp = time.time()
        block = {
            "kind": META,
            "title": str(event.get("text") or ""),
            "subtitle": "",
            "body": "",
            "status": None,
            "ts": stamp,
            "data": {"plumbing": True},
        }
        index = len(self.blocks)
        for position, existing in enumerate(self.blocks):
            try:
                other = float(existing.get("ts"))
            except (TypeError, ValueError):
                continue
            if other > float(stamp):
                index = position
                break
        self.blocks.insert(index, block)
        return [block]

    def _ingest_done(self, event: dict) -> list[dict]:
        """The model call is over: the next stream starts a new answer."""
        self._answer_text = ""
        return []
