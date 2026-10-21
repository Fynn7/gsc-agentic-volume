"""The chat record as a log: the conversation of a run in plain text.

The screen shows the conversation the way a coding agent app does: the
task, the model's thinking, the tool calls, the frames and the answer.
This module folds the same blocks into a plain-text log - no markup, no
Textual, no run - so the conversation can be copied to the clipboard or
written to a file.

The log carries the story of the run. The transport (the model calls, the
HTTP status) stays in the evidence: "Live: copy the JSON log" has it.
The run folder is never written to - an exported log lands in
runtime/tui/chat-logs/.

Pure functions plus one small writer - no model, no network, no run.
"""
from __future__ import annotations

import re
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
for _extra in (_ROOT / "scripts", Path(__file__).resolve().parent):
    if str(_extra) not in sys.path:
        sys.path.insert(0, str(_extra))

import event_text as text_mod
import transcript as transcript_mod

# Where an exported chat log lands (the run folder stays read-only).
LOG_DIR = _ROOT / "runtime" / "tui" / "chat-logs"

# The mark in front of every word the model itself wrote (the screen's mark).
MODEL_MARK = transcript_mod.block_mark(transcript_mod.ASSISTANT)


def _stamp(ts) -> str:
    """The local time of a row."""
    try:
        value = float(ts)
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(value))
    except (TypeError, ValueError, OSError, OverflowError):
        return "---------- --:--:--"


def _body_lines(body: str, mark: str = "") -> list[str]:
    """The lines under a row, indented like the screen indents them."""
    lines = []
    for line in str(body or "").splitlines():
        text = "%s %s" % (mark, line) if mark else line
        lines.append("  %s" % text)
    return lines


def _tool_head(block: dict) -> str:
    """The tool row: what the step does, what it changed and how it ended."""
    title = str(block.get("title") or "")
    subtitle = str(block.get("subtitle") or "")
    head = "%s %s" % (transcript_mod.block_mark(transcript_mod.TOOL), title)
    if subtitle:
        head += "  %s" % subtitle
    # How the step ended: the same parts the screen paints.
    head += "".join(part for part, _role in transcript_mod.tool_row_parts(block))
    return head


def _row_lines(mark: str, title: str) -> list[str]:
    """A row: the mark and the text; the lines under it are indented.

    A multi-line text (the model's answer, the welcome note) stays one
    row: the lines under it belong to it.
    """
    lines = str(title or "").splitlines() or [""]
    out = ["%s %s" % (mark, lines[0])]
    for line in lines[1:]:
        out.append("  %s" % line)
    return out


def block_lines(block: dict) -> list[str]:
    """One transcript block as the lines of the log (the screen's words).

    The same marks as the screen, so a line of the log can be read on the
    screen: the task is the user's, the words are the model's, the rest
    are facts.
    """
    kind = str(block.get("kind") or "")
    title = str(block.get("title") or "")
    subtitle = str(block.get("subtitle") or "")
    body = str(block.get("body") or "")
    data = block.get("data") or {}
    mark = transcript_mod.block_mark(kind)

    if kind == transcript_mod.THINKING:
        head_mark, words = transcript_mod.thinking_headline(block)
        head = "%s %s" % (head_mark, words)
        if subtitle:
            head += "  %s" % subtitle
        # The screen folds the chain away; the log opens it: the reasoning
        # is part of the record.
        return [head] + _body_lines(body, MODEL_MARK)
    if kind == transcript_mod.TOOL:
        return [_tool_head(block)] + _body_lines(body)
    if kind == transcript_mod.FINISH:
        return _row_lines(mark, title) + _body_lines(body, MODEL_MARK)
    if kind == transcript_mod.ERROR:
        return _row_lines(mark, title) + _body_lines(body)
    if kind == transcript_mod.NOTICES:
        # The screen folds the notices behind Ctrl+H; the log opens them
        # (a file has no keys). The label itself is the screen's.
        count = len(data.get("notices") or [])
        headline = text_mod.notices_label(count) if count else title
        return ["%s %s" % (mark, headline)] + _body_lines(body)
    if kind == transcript_mod.IMAGE:
        head = "%s %s" % (mark, title)
        if subtitle:
            head += "  %s" % subtitle
        return [head]
    if kind == transcript_mod.ASSISTANT:
        return _row_lines(mark, title)
    # The user's task and the quiet system lines: one row, the words as they are.
    return _row_lines(mark, title)


def _header(run_id=None, model=None, run_dir=None, exported_at=None) -> list[str]:
    """The head of the log: which run, which model, where the evidence is."""
    lines = ["# Volume Agent chat log"]
    if run_id:
        lines.append("# run: %s" % run_id)
    if run_dir:
        lines.append("# evidence: %s" % run_dir)
    if model:
        lines.append("# model: %s" % model)
    stamp = time.localtime(exported_at) if exported_at else time.localtime()
    lines.append("# exported: %s" % time.strftime("%Y-%m-%d %H:%M:%S", stamp))
    lines.append("#")
    lines.append("# the technical rows (the model calls, the HTTP status) are not")
    lines.append("# in this file: \"Live: copy the JSON log\" has the whole evidence.")
    return lines


def chat_log_text(blocks, *, run_id=None, model=None, run_dir=None,
                  exported_at=None) -> str:
    """The chat record of a run as plain text.

    The blocks are the ones the screen shows (the transcript), so the log
    and the screen never tell different stories.
    """
    lines = _header(run_id=run_id, model=model, run_dir=run_dir,
                    exported_at=exported_at)
    lines.append("#")
    for block in blocks or []:
        if not isinstance(block, dict):
            continue
        # The transport (the model calls, the HTTP status) is not part of
        # the conversation: the screen hides it until Ctrl+H asks for it.
        if transcript_mod.is_plumbing(str(block.get("kind") or "")):
            continue
        stamp = _stamp(block.get("ts"))
        row = block_lines(block)
        if not row:
            continue
        # The time belongs to the row; the lines under it continue it.
        lines.append("[%s] %s" % (stamp, row[0]))
        lines.extend(row[1:])
    return "\n".join(lines) + "\n"


def _safe_name(text: str) -> str:
    """A file name every file system accepts."""
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", str(text or "").strip())
    return cleaned.strip("-.") or "chat"


def export_chat_log(blocks, *, run_id=None, model=None, run_dir=None) -> Path:
    """Write the chat record to a log file; returns the path of the file.

    The file lands in runtime/tui/chat-logs/ (the run folder is never
    written to). Every export keeps its own file: a second export in the
    same second gets its own number.
    """
    folder = Path(LOG_DIR)
    folder.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    base = "%s-%s" % (_safe_name(run_id or "chat"), stamp)
    path = folder / ("%s.log" % base)
    number = 2
    while path.exists():
        path = folder / ("%s-%d.log" % (base, number))
        number += 1
    text = chat_log_text(blocks, run_id=run_id, model=model, run_dir=run_dir)
    path.write_text(text, encoding="utf-8")
    return path
