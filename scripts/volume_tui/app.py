"""Coding-agent style TUI for the Volume Agent.

One screen, like a coding agent app: a status line, the conversation with the
run (the task, the model's thinking, its tool calls, the frames it rendered and
its answer) and a composer that starts the next task.

Control/display only: runs start via scripts/run_local_agent.py, evidence is
read-only except new runs, verification.json and tui-settings.json.
"""
from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    import chat_log as chat_log_mod
    import config as cfg
    import env as env_mod
    import event_text as event_text_mod
    import runs as runs_mod
    import runner as runner_mod
    import services as services_mod
    import trace_events as trace_mod
    import transcript as transcript_mod
    import stream as stream_mod
    import volumes as volumes_mod
    from context import AppContext
    import evidence as evidence_mod
    import flows as flows_mod
    import imaging as imaging_mod
    import telemetry as telemetry_mod
except Exception:  # pragma: no cover - import path for tests
    chat_log_mod = None  # type: ignore
    cfg = None  # type: ignore
    env_mod = None  # type: ignore
    event_text_mod = None  # type: ignore
    runs_mod = None  # type: ignore
    runner_mod = None  # type: ignore
    services_mod = None  # type: ignore
    trace_mod = None  # type: ignore
    transcript_mod = None  # type: ignore
    stream_mod = None  # type: ignore
    volumes_mod = None  # type: ignore
    AppContext = None  # type: ignore
    evidence_mod = None  # type: ignore
    flows_mod = None  # type: ignore
    imaging_mod = None  # type: ignore
    telemetry_mod = None  # type: ignore

import re as _re

_ESCAPE_TAG_RE = _re.compile(r"(\\*)(\[[A-Za-z#/@/][^[]*?])")


def _escape_markup(text) -> str:
    """Escape literal [...] so terminal markup never eats it.

    Rich's own escape() only covers lowercase-led tags, but rendered output
    also drops uppercase-led tags such as [FROZEN] (task text, model claims,
    and error strings reach the UI verbatim). Mirror Rich's backslash logic
    with a case-insensitive tag head instead.
    """
    text = str(text)

    def _fix(match):
        backslashes, tag = match.groups()
        return "%s%s\\%s" % (backslashes, backslashes, tag)

    text = _ESCAPE_TAG_RE.sub(_fix, text)
    if text.endswith("\\") and not text.endswith("\\\\"):
        return text + "\\"
    return text


try:
    from textual import events as _events
except Exception:  # pragma: no cover
    _events = None  # type: ignore

try:
    from textual.app import App, ComposeResult
    from textual.binding import Binding
    from textual.containers import Horizontal, Vertical, VerticalScroll
    from textual.geometry import Offset
    from textual.message import Message
    from textual.screen import ModalScreen
    from textual.selection import Selection
    from textual.widgets import (
        Button,
        DataTable,
        Footer,
        Input,
        Label,
        LoadingIndicator,
        Select,
        Static,
        TextArea,
    )
    _TEXTUAL = True
except Exception:
    _TEXTUAL = False
    App = object  # type: ignore
    ComposeResult = None  # type: ignore
    Message = object  # type: ignore

# The accent colour of the whole app: the model, the task and the tool names
# are drawn with it, exactly like a coding agent app marks its own output.
ACCENT = "cyan"

# How the screen paints the roles of a tool row (the row itself is built
# once, in transcript.tool_row_parts; the chat log writes the same parts).
TOOL_ROW_STYLES = {
    "mark": ACCENT,
    "title": "bold %s" % ACCENT,
    "sub": "dim",
    "wait": "dim",
    "ok": "green",
    "fail": "red",
    "time": "dim",
}

# Settings form fields: (label, widget id, setting key, kind). One list, used
# by the form, by "Save settings" and by "Reset to defaults", so they cannot
# drift apart.
SETTINGS_ROWS = (
    ("Mode", "set-mode", "mode", "text"),
    ("Model", "set-model", "model", "text"),
    ("Local endpoint", "set-endpoint", "endpoint", "text"),
    ("H200 endpoint", "set-h200", "h200Endpoint", "text"),
    ("Renderer", "set-renderer", "rendererService", "text"),
    ("Context size", "set-context", "contextSize", "int"),
    ("Render budget", "set-budget", "renderBudget", "int"),
    ("Temperature", "set-temp", "temperature", "float"),
    ("Timeout (s)", "set-timeout", "timeoutSeconds", "int"),
    ("Task timeout (s)", "set-tasktimeout", "taskTimeoutSeconds", "int"),
    ("Max output tokens", "set-maxtokens", "maxOutputTokens", "int"),
)

# Preview budget: a frame in the conversation is fitted into this many text
# rows and this many columns, so the whole picture is visible at once (never
# a cut-off corner) and never swallows the conversation.
IMAGE_PREVIEW_MAX_ROWS = 14
IMAGE_PREVIEW_MAX_COLS = 64

# Two clocks. The screen ticks fast, so the spinner and the seconds stay
# alive; the backend is read at its own pace, so the evidence is not
# re-read ten times a second.
UI_TICK_SECONDS = 0.1
BACKEND_TICK_SECONDS = 0.5

# The spinner the thinking block and the status line share.
SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


# In front of every word the model wrote itself. event_text owns the mark.
MODEL_MARK = getattr(event_text_mod, "MODEL_MARK", "»")
if transcript_mod is not None:
    MODEL_MARK = transcript_mod.block_mark(transcript_mod.ASSISTANT)


def _spinner_frame() -> str:
    return SPINNER[int(time.time() * 10) % len(SPINNER)]


def render_block(block: dict):
    """One transcript entry as a Rich renderable.

    The transcript is the app's voice: the task is the user's, the tool calls
    and the answers are the agent's, and every line says what happened.
    """
    from rich.text import Text

    if transcript_mod is None:  # pragma: no cover - the transcript always loads
        return Text(str(block.get("title") or ""))

    kind = str(block.get("kind") or "")
    title = str(block.get("title") or "")
    subtitle = str(block.get("subtitle") or "")
    body = str(block.get("body") or "")
    data = block.get("data") or {}

    text = Text()
    if kind == transcript_mod.USER:
        text.append("%s " % transcript_mod.block_mark(kind), style="bold %s" % ACCENT)
        text.append(title, style="bold")
        return text
    if kind == transcript_mod.THINKING:
        return render_thinking(block, False)
    if kind == transcript_mod.ASSISTANT:
        # The model's own words, marked as such.
        text.append("%s " % transcript_mod.block_mark(kind), style=ACCENT)
        text.append(title)
        return text
    if kind == transcript_mod.TOOL:
        # The row is built once (transcript.tool_row_parts); the screen
        # paints the roles.
        for part, role in transcript_mod.tool_row_parts(block):
            text.append(part, style=TOOL_ROW_STYLES.get(role, "dim"))
        if body:
            text.append("\n  %s" % body.replace("\n", "\n  "), style="dim")
        return text
    if kind == transcript_mod.IMAGE:
        text.append("%s " % transcript_mod.block_mark(kind), style="green")
        text.append(title, style="green")
        if subtitle:
            text.append("  %s" % subtitle, style="dim")
        return text
    if kind == transcript_mod.NOTICES:
        # The harness notices: one row, quiet, with the text behind Ctrl+H.
        text.append("%s " % transcript_mod.block_mark(kind), style="yellow")
        text.append(title, style="yellow")
        if body and data.get("plumbing_visible"):
            text.append("\n  %s" % body.replace("\n", "\n  "), style="dim")
        return text
    if kind == transcript_mod.WARN:
        text.append("%s " % transcript_mod.block_mark(kind), style="yellow")
        text.append(title, style="yellow")
        return text
    if kind == transcript_mod.ERROR:
        text.append("%s " % transcript_mod.block_mark(kind), style="red")
        text.append(title, style="red")
        if body:
            text.append("\n  %s" % body.replace("\n", "\n  "), style="dim")
        return text
    if kind == transcript_mod.FINISH:
        text.append("%s " % transcript_mod.block_mark(kind), style="green")
        text.append(title, style="green")
        if body:
            text.append("\n  %s %s" % (MODEL_MARK, body), style="dim")
        return text
    # Notice and meta lines stay quiet: they carry facts, not the story.
    text.append("%s " % transcript_mod.block_mark(kind), style="dim")
    text.append(title, style="dim")
    return text


def render_thinking(block: dict, expanded: bool = False):
    """The model turn: one line that opens into the reasoning chain.

    The line names what it is. "Thinking" / "Thought" when the model
    really reasoned; "Working" / "Model answered" when it did not
    (the Think setting is off, and the model obeyed it). Closed it shows
    the arrow only - the words stay inside the chain. A click opens the
    model's own thinking (the reasoning the model streamed, or - when the
    model does not think out loud - the reason the agent states for its
    next call). The arrow says which one it is: ▸ closed, ▾ open.
    """
    from rich.text import Text

    text = Text()
    body = str(block.get("body") or "")
    running = block.get("status") == "running"
    # The row is built once (transcript.thinking_row_parts); the screen
    # paints the roles.
    for part, role in transcript_mod.thinking_row_parts(block):
        if role == "spin":
            text.append(part, style=ACCENT)
        elif role == "ok":
            text.append(part, style="green")
        elif role == "words":
            text.append(part, style="italic dim" if running else "dim")
        else:
            text.append(part, style="dim")
    if not expanded:
        # Closed: the arrow says the chain is there, the words stay inside.
        if body:
            text.append("  ▸", style="dim")
        return text
    # Open: the arrow points down, so the line still says that it can be closed.
    text.append("  ▾", style="dim")
    if body:
        # The model's own words, marked as such - no label in front.
        for line in body.splitlines():
            text.append("\n  %s %s" % (MODEL_MARK, line), style="italic dim")
    return text


class DismissibleModal(ModalScreen if _TEXTUAL else object):
    """Modal screen that closes with Esc.

    Textual's ModalScreen ships no escape binding of its own, so the help
    screen and the dialogs ignored Esc even though they advertise it. The
    binding lives on the screen, so it wins over the app bindings while the
    modal is up.
    """

    if _TEXTUAL:
        BINDINGS = [Binding("escape", "dismiss", "Close", show=False)]


class HelpScreen(DismissibleModal):
    def compose(self):
        with Vertical(classes="dlg"):
            with VerticalScroll():
                yield Static(
                    "Volume Agent — a coding agent for one volume\n"
                    "\n"
                    "The screen is one conversation with the run: your task at the top,\n"
                    "then the model's thinking, its tool calls, the frames it rendered\n"
                    "and its answer. The status line above says which run is open and\n"
                    "how much of the observation budget is already spent.\n"
                    "\n"
                    "KEYS\n"
                    "  Enter          send the composer text: it starts a new run\n"
                    "  Ctrl+Up/Down   scroll the conversation (the composer keeps the cursor)\n"
                    "  Esc            stop the running agent (evidence already written stays)\n"
                    "  /              slash commands with a menu (like opencode: type\n"
                    "                 / to list them, Enter runs the match)\n"
                    "  F1             this help\n"
                    "  F2             the glossary: what the words of the screen mean\n"
                    "  Ctrl+H         the technical rows (model calls, HTTP status)\n"
                    "  Ctrl+T         light / dark\n"
                    "  Ctrl+Q         quit (asks first while a run is live)\n"
                    "  Ctrl+C         copy the selected text (the terminal default)\n"
                    "\n"
                    "  While the agent works the Send button turns into a red Stop;\n"
                    "  it stops the run the same way Esc does.\n"
                    "\n"
                    "  Click a line that starts with ✳ Thinking to open the model's\n"
                    "  reasoning chain; click it again to close it.\n"
                    "\n"
                    "WHAT THE TRANSCRIPT SHOWS\n"
                    "  ▌ <task>       what you asked for\n"
                    "  » <model>      the model's own words: its answer, its reason,\n"
                    "                 its claim - never written by the screen\n"
                    "  ✳ Thinking      the model reasoned; the line opens the chain\n"
                    "  ✓ Thought       the chain is closed; click it to read it\n"
                    "  ✳ Working       a model turn that did not reason (Think is off)\n"
                    "  ✓ Model answered the turn is over, there is nothing to open\n"
                    "  ● <step>       what the agent does now, then what goes in,\n"
                    "                 what comes out and what changes; ✓ when it\n"
                    "                 returned, ✗ when the renderer refused it\n"
                    "  ▣ frame        a PNG the renderer wrote (the picture is inline)\n"
                    "  ✓ Run finished what the agent picked and what it claims\n"
                    "  ⚠ / ✗          a notice and a failure\n"
                    "\n"
                    "  The model calls and the HTTP status are technical detail:\n"
                    "  Ctrl+H shows them, the evidence always has them.\n"
                    "\n"
                    "EVIDENCE\n"
                    "  Evidence is read-only except new runs, verification.json and\n"
                    "  tui-settings.json.\n"
                    "\n"
                    "LEAVING\n"
                    "  Ctrl+Q stops the agent run, the local Ollama and the services\n"
                    "  this TUI started, then leaves. The hard stop (/hard-stop)\n"
                    "  also stops a local Ollama this TUI did not start.\n"
                    "  Closing the window stops everything as well.\n"
                    "\n"
                    "Press Esc to close.",
                    id="help-text",
                    markup=False,
                )


class GlossaryScreen(DismissibleModal):
    """The words of the screen, explained (F2).

    Someone who never saw this pipeline reads the run: the words the
    screen uses are the ones the glossary explains.
    """

    def compose(self):
        lines = ["WHAT THE WORDS MEAN\n"]
        for term, meaning in _glossary_entries():
            lines.append("  %-24s %s" % (term, meaning))
        lines.append("")
        lines.append("Press Esc to close.")
        with Vertical(classes="dlg"):
            with VerticalScroll():
                yield Static("\n".join(lines), id="help-text", markup=False)


def _glossary_entries():
    """The glossary of event_text, or nothing if the module is missing."""
    if event_text_mod is None:  # pragma: no cover - the module always loads
        return []
    return list(getattr(event_text_mod, "GLOSSARY", []))


class ConfirmScreen(DismissibleModal):
    def __init__(self, message: str, on_yes=None):
        super().__init__()
        self._message = message
        self._on_yes = on_yes

    def compose(self):
        with Vertical(classes="dlg"):
            yield Static(self._message, id="confirm-message", markup=False)
            yield Static("Esc = No (cancel)", id="confirm-hint")
            with Horizontal():
                yield Button("Yes", id="confirm-yes", variant="error",
                             tooltip="Do the action now. Esc is the same as No.")
                yield Button("No", id="confirm-no", variant="primary",
                             tooltip="Do nothing and go back. Esc does the same.")

    async def action_dismiss(self, result=None) -> None:
        """Esc cancels: the same as clicking No, never the Yes action."""
        await super().action_dismiss(False)

    async def on_button_pressed(self, event):
        if event.button.id == "confirm-yes" and self._on_yes:
            result = self._on_yes()
            if hasattr(result, "__await__"):
                await result
        self.dismiss(result=(event.button.id == "confirm-yes"))


class CommandScreen(DismissibleModal):
    """Retired modal palette (kept for COMMANDS data + old tests).

    The UI no longer opens it: '/' opens the inline slash menu in
    the composer instead. COMMANDS stays the single source of labels/tooltips.
    """
    # (label, action id, tooltip) — the id is stable, the label spells out the
    # effect and the tooltip says it once more (every button carries one).
    COMMANDS = [
        ("Run: stop the agent", "live-stop",
         "Ask the agent to stop now; evidence already written is kept."),
        ("Live: verify the evidence (read-only)", "live-verify",
         "Check this run's evidence; writes verification.json, never calls the model."),
        ("Live: copy the JSON log", "live-copy",
         "Copy the whole run's JSON log to the clipboard (read-only)."),
        ("Live: copy the chat log", "live-copy-chat",
         "Copy the conversation of this run as plain text (the task, the "
         "model's thinking, the tool calls, the frames and the answer)."),
        ("Live: export the chat log", "live-export-chat",
         "Write the conversation of this run to runtime/tui/chat-logs/ as a "
         ".log file (the run folder stays read-only)."),
        ("Live: view the current PNG", "live-open-png",
         "Open the previewed PNG in your image viewer (full size)."),
        ("Live: open the PNG folder", "live-open",
         "Open the file explorer at the folder that holds the PNGs."),
        ("Runs: open another run", "runs-open",
         "Pick one of the runs on this machine and open it here."),
        ("Volume: switch the volume", "volume-switch",
         "Pick the volume the next run works on; writes runtime/tui-settings.json."),
        ("Settings: model, endpoints, budget", "settings",
         "Show the form that writes runtime/tui-settings.json."),
        ("Theme: light or dark", "theme",
         "Switch the colour theme of the whole app."),
        ("Help: what every key does", "help",
         "Open this help screen."),
        ("Hard stop: kill everything this TUI owns", "hard-stop",
         "Stop the agent tree, the local Ollama and the owned services now, "
         "then leave. The hard stop also stops a local Ollama this TUI did not start."),
        ("Quit the TUI", "quit",
         "Stop everything the TUI owns, then leave. Asks first while a run is live."),
    ]

    def compose(self):
        with Vertical(classes="dlg"):
            yield Static("Command palette (Enter runs, Esc closes)", id="cmd-title")
            with VerticalScroll():
                for label, action, tooltip in self.COMMANDS:
                    yield Button(label, id="cmd-%s" % action, tooltip=tooltip)

    async def on_button_pressed(self, event):
        self.dismiss(result=event.button.id)


# Slash commands (opencode-style): typed in the composer, picked from a menu.
# The labels/tooltips stay on CommandScreen.COMMANDS (single source); this only
# names the slash form of each stable action id.
SLASH_NAMES = {
    "live-stop": "/stop",
    "live-verify": "/verify",
    "live-copy": "/copy-json",
    "live-copy-chat": "/copy-chat",
    "live-export-chat": "/export-chat",
    "live-open-png": "/open-png",
    "live-open": "/open-folder",
    "runs-open": "/runs",
    "volume-switch": "/volume",
    "settings": "/settings",
    "theme": "/theme",
    "help": "/help",
    "glossary": "/glossary",
    "hard-stop": "/hard-stop",
    "quit": "/quit",
}


def slash_commands() -> list[tuple[str, str, str]]:
    """All slash commands as (slash, action, tooltip)."""
    out: list[tuple[str, str, str]] = []
    for _label, action, tooltip in CommandScreen.COMMANDS:
        slash = SLASH_NAMES.get(action)
        if slash:
            out.append((slash, action, tooltip))
    # Glossary has no palette entry of its own; it is still a slash command.
    if "glossary" not in [action for _slash, action, _tip in out]:
        out.append(("/glossary", "glossary", "What the words of the screen mean."))
    return out


def _fuzzy_match(needle: str, haystack: str) -> bool:
    """VSCode-style: every char of needle appears in haystack, in order."""
    pos = 0
    for char in needle:
        pos = haystack.find(char, pos)
        if pos < 0:
            return False
        pos += 1
    return True


def match_slash_commands(prefix: str) -> list[tuple[str, str, str]]:
    """Fuzzy filter like VSCode: prefix chars must appear in order.

    /rify, /rfy, /eiy all hit /verify; /var (wrong char 'a') hits nothing.
    Matching is against the command name only, case-insensitive.
    """
    needle = (prefix or "").strip().lower()
    if needle.startswith("/"):
        needle = needle[1:]
    items = slash_commands()
    if not needle:
        return items
    return [item for item in items if _fuzzy_match(needle, item[0][1:].lower())]


def parse_slash_command(text: str):
    """The action id for a '/command ...' composer line, or None."""
    if text is None:
        return None
    stripped = str(text).lstrip()
    if not stripped.startswith("/"):
        return None
    token = stripped.split(None, 1)[0].lower()
    for slash, action, _tooltip in slash_commands():
        if slash.lower() == token:
            return action
    return None


class RunsScreen(DismissibleModal):
    """The runs on this machine; one opens in the conversation."""

    def __init__(self, runs: list[dict]):
        super().__init__()
        self._runs = list(runs or [])

    def compose(self):
        with Vertical(classes="dlg"):
            yield Static("Runs on this machine (Enter opens one, Esc closes)",
                         id="runs-title")
            # cursor_type="row": Enter picks the run, not a single cell.
            yield DataTable(id="runs-modal-table", cursor_type="row")

    def on_mount(self) -> None:
        table = self.query_one("#runs-modal-table", DataTable)
        table.add_columns("run", "status", "model", "frames", "task")
        for run in self._runs:
            table.add_row(_escape_markup(str(run.get("runId", "?"))),
                          _escape_markup(str(run.get("status", "?"))),
                          _escape_markup(str(run.get("model", "?"))),
                          str(run.get("frames", "?")),
                          _escape_markup(str(run.get("task", "?"))[:60]))

    async def on_data_table_row_selected(self, event):
        row = event.cursor_row
        if 0 <= row < len(self._runs):
            self.dismiss(result=self._runs[row])


class VolumesScreen(DismissibleModal):
    """The volumes on this machine; one becomes the next run's volume."""

    def __init__(self, volumes: list[dict], current: str = ""):
        super().__init__()
        self._volumes = list(volumes or [])
        self._current = str(current or "")

    def compose(self):
        with Vertical(classes="dlg"):
            yield Static("Volumes (Enter switches, Esc closes)",
                         id="volumes-title")
            # cursor_type="row": Enter picks the volume, not a single cell.
            yield DataTable(id="volumes-modal-table", cursor_type="row")

    def on_mount(self) -> None:
        table = self.query_one("#volumes-modal-table", DataTable)
        table.add_columns("volume", "dimensions", "note")
        for item in self._volumes:
            dims = item.get("dimensions")
            dims_text = "x".join(str(v) for v in dims) if dims else "dims unknown"
            path = str(item.get("path") or "")
            if path and path == self._current:
                note = "in use"
            elif item.get("default"):
                note = "default"
            else:
                note = ""
            table.add_row(_escape_markup(str(item.get("label") or path or "?")),
                          _escape_markup(dims_text),
                          _escape_markup(note))

    async def on_data_table_row_selected(self, event):
        row = event.cursor_row
        if 0 <= row < len(self._volumes):
            self.dismiss(result=self._volumes[row])


class SettingsScreen(DismissibleModal):
    """The settings form (model, volume, endpoints, budget, timeouts).

    It is a dialog, not a page: the app is one screen, like a coding agent
    app, and the settings live behind one command.
    """

    def __init__(self, settings: dict):
        super().__init__()
        self.settings = dict(settings or {})

    def compose(self):
        with Vertical(classes="dlg"):
            with VerticalScroll():
                with Vertical(classes="panel"):
                    yield Static("Saved to runtime/tui-settings.json (Save settings writes it)",
                                 classes="scroll-hint")
                    for label, wid, key, _kind in SETTINGS_ROWS:
                        with Horizontal(classes="row"):
                            yield Label(label, classes="field-label")
                            yield Input(str(self.settings.get(key, "")), id=wid)
                    with Horizontal(classes="row"):
                        yield Label("Volume", classes="field-label")
                        options, volume_value = self._volume_options()
                        yield Select(options, value=volume_value, id="set-volume")
                    with Horizontal(classes="row"):
                        yield Label("Think", classes="field-label")
                        yield Select([("off", "false"), ("on", "true")],
                                     value="true" if self.settings.get("think") else "false",
                                     id="set-think")
                    with Horizontal(classes="row"):
                        yield Button("Save settings", id="btn-settings-save", variant="primary",
                                     tooltip="Write these settings to runtime/tui-settings.json.")
                        yield Button("Reset to defaults", id="btn-settings-reset",
                                     tooltip="Fill the form with the built-in defaults. Nothing is written yet.")
                        yield Button("Test H200 endpoint", id="btn-h200-probe",
                                     tooltip="Send one small probe request to the H200 endpoint. "
                                             "Never falls back silently.")
                    yield Static("", id="settings-status", markup=False)

    # -- the volume ------------------------------------------------------
    def _volume_options(self, settings: dict | None = None) -> tuple[list[tuple[str, str]], str]:
        """The volumes this machine offers, and the one in use.

        The volume is what the agent works on; it used to live on the
        deleted Volumes page, so the settings dialog carries it now. The
        settings can be given, so the form can be filled with the
        defaults and not with what is in use.
        """
        settings = self.settings if settings is None else settings
        current = ""
        try:
            selected = cfg.selected_volume(settings) if cfg else None
            current = str(selected) if selected else ""
        except Exception:
            current = ""
        options: list[tuple[str, str]] = []
        try:
            items = volumes_mod.discover_volumes(current or None) if volumes_mod else []
        except Exception:
            items = []
        for item in items:
            path = str(item.get("path") or "")
            if not path:
                continue
            try:
                label = volumes_mod.option_text(item)
            except Exception:
                label = path
            options.append((label, path))
        if current and not any(value == current for _label, value in options):
            options.insert(0, (current, current))
        if not options:
            options.append(("(the audited gate volume)", ""))
        values = [value for _label, value in options]
        value = current if current in values else values[0]
        return options, value

    # -- the form --------------------------------------------------------
    def _collect(self) -> dict:
        settings = dict(self.settings)
        for _label, widget, key, kind in SETTINGS_ROWS:
            raw = self.query_one("#" + widget, Input).value.strip()
            if kind == "int":
                if raw:
                    settings[key] = int(raw)
            elif kind == "float":
                if raw:
                    settings[key] = float(raw)
            else:
                settings[key] = raw
        try:
            settings["think"] = str(self.query_one("#set-think", Select).value) == "true"
        except Exception:
            pass
        try:
            settings["volumePath"] = str(self.query_one("#set-volume", Select).value or "")
        except Exception:
            pass
        return settings

    async def on_button_pressed(self, event):
        bid = event.button.id
        if bid == "btn-settings-save":
            self._save()
        elif bid == "btn-settings-reset":
            self._reset()
        elif bid == "btn-h200-probe":
            self._probe()

    def _status(self, text: str) -> None:
        try:
            self.query_one("#settings-status", Static).update(text)
        except Exception:
            pass

    def _save(self) -> None:
        try:
            settings = self._collect()
        except ValueError as error:
            self._status("Invalid: %s" % error)
            return
        problems = cfg.validate_settings(settings) if cfg else []
        if problems:
            self._status("Invalid:\n" + "\n".join("- %s" % p for p in problems))
            return
        if cfg:
            cfg.save_settings(settings)
        self.settings = settings
        self._status("Saved to runtime/tui-settings.json.")
        self.dismiss(result=settings)

    def _reset(self) -> None:
        """Fill the form with the built-in defaults. Writes nothing."""
        defaults = dict(cfg.DEFAULTS) if cfg else {}
        for _label, widget, key, _kind in SETTINGS_ROWS:
            try:
                self.query_one("#" + widget, Input).value = str(defaults.get(key, ""))
            except Exception:
                pass
        try:
            self.query_one("#set-think", Select).value = (
                "true" if defaults.get("think") else "false")
        except Exception:
            pass
        try:
            # The volume goes back to the default one (the audited gate volume).
            _options, value = self._volume_options(defaults)
            self.query_one("#set-volume", Select).value = value
        except Exception:
            pass
        self._status("Form filled with the built-in defaults. Nothing was written — "
                     "press Save settings to keep them.")

    def _probe(self) -> None:
        """The probe runs in a worker: the screen must not freeze on it."""
        # The field, not the saved settings: the probe answers about what
        # is written in the form right now.
        try:
            endpoint = str(self.query_one("#set-h200", Input).value).rstrip("/")
        except Exception:
            endpoint = str(self.settings.get("h200Endpoint", "")).rstrip("/")
        # The app is picked here, on the UI thread: a worker thread has no
        # active app of its own.
        app = self.app

        def _work() -> None:
            import urllib.request
            try:
                with urllib.request.urlopen(endpoint + "/api/version", timeout=5) as response:
                    version = json.load(response)
                message = "H200 OK: %s" % str(version)[:120]
            except Exception as error:
                message = "H200 DOWN: %s" % error
            try:
                app.call_from_thread(self._probe_done, message)
            except Exception:
                pass

        self._status("Probing %s ..." % endpoint)
        button = None
        try:
            button = self.query_one("#btn-h200-probe", Button)
        except Exception:
            pass
        app._run_guarded("Probing H200", _work, button)

    def _probe_done(self, message: str) -> None:
        """The probe answered: the text shows the result."""
        self._status(message)


class TranscriptScroll(VerticalScroll if _TEXTUAL else object):
    """The conversation scroller.

    It tells the app when the view moves, so a user who scrolls up to read
    the history is not yanked back to the newest entry.
    """

    if _TEXTUAL:
        def watch_scroll_y(self, old_value: float, new_value: float) -> None:
            super().watch_scroll_y(old_value, new_value)
            try:
                app = self.app
            except Exception:
                return
            notify = getattr(app, "_user_scrolled", None)
            if notify is not None:
                notify()


class SafeSelection:
    """A text selection that never points past the content.

    A block can be one layout behind its content - the answer grows while the
    model writes it - and Textual's own extraction then reads past the last
    line, which crashes the copy. The selection is clipped to the lines the
    block really has, so copying always works.
    """

    def get_selection(self, selection):
        try:
            return super().get_selection(selection)
        except IndexError:
            pass
        try:
            text = str(self.visual)
        except Exception:
            return None
        lines = text.splitlines()
        if not lines:
            return ""
        start_y, start_x = selection.start.transpose
        end_y, end_x = selection.end.transpose
        start_y = min(max(start_y, 0), len(lines) - 1)
        end_y = min(max(end_y, 0), len(lines) - 1)
        if end_y < start_y:
            return None
        clipped = Selection(start=Offset(start_x, start_y), end=Offset(end_x, end_y))
        try:
            return clipped.extract(text), "\n"
        except IndexError:
            return None


class SafeStatic(SafeSelection, Static if _TEXTUAL else object):
    """A plain static whose selection never points past its content."""


class FramePreview(SafeSelection, Static if _TEXTUAL else object):
    """Preview pane for a frame in the conversation.

    The frame is drawn as half-block cells whose width must match the widget
    exactly, otherwise the terminal folds the row and the picture is cut in
    half. The widget therefore asks the app to re-fit the frame whenever its
    own size changes (window resize, scrollbar appearing).
    """

    def on_resize(self, event) -> None:
        try:
            app = self.app
        except Exception:
            return
        refresh = getattr(app, "_refresh_frame_display", None)
        if refresh is not None:
            refresh()


class TranscriptBlock(SafeSelection, Static if _TEXTUAL else object):
    """One entry of the conversation.

    The content is pushed with update(), so the block grows with the text
    (the answer arrives word by word).
    """

    def __init__(self, block: dict):
        super().__init__()
        self.block = block
        self.add_class("block")
        self.add_class(str(block.get("kind") or "note"))

    def set_block(self, block: dict) -> None:
        self.block = block
        self.update(render_block(block))


class ThinkingBlock(SafeSelection, Static if _TEXTUAL else object):
    """The model's thinking: one line, a click opens the reasoning chain.

    The block is the same one the model streams its reasoning into, so the
    chain grows while the model works - open or closed.
    """

    def __init__(self, block: dict):
        super().__init__()
        self.block = block
        self.expanded = False
        self.add_class("block")
        self.add_class("thinking")

    def set_block(self, block: dict) -> None:
        self.block = block
        self.update(render_thinking(block, self.expanded))

    def on_click(self, event=None) -> None:
        """A click opens the reasoning chain, a second click closes it."""
        if not self._can_open():
            return
        self.expanded = not self.expanded
        self.update(render_thinking(self.block, self.expanded))
        if event is not None:
            try:
                event.stop()
            except Exception:
                pass

    def action_toggle(self) -> None:
        """The same, from the keyboard."""
        if not self._can_open():
            return
        self.expanded = not self.expanded
        self.update(render_thinking(self.block, self.expanded))

    def _can_open(self) -> bool:
        """Only reasoning opens: without it the line is a plain fact row."""
        return bool(str((self.block.get("body") or "")).strip())


class ImageBlock(Vertical if _TEXTUAL else object):
    """A frame in the conversation: the caption, the path and the picture."""

    def __init__(self, block: dict):
        super().__init__(classes="image-block block")
        self.block = block
        self._preview_key = None

    def compose(self):
        yield SafeStatic("", classes="image-caption")
        yield FramePreview("(no preview)", classes="image-preview")

    def on_mount(self) -> None:
        self.set_block(self.block)

    def set_block(self, block: dict) -> None:
        self.block = block
        try:
            self.query_one(".image-caption", SafeStatic).update(render_block(block))
        except Exception:
            pass
        self.refresh_preview()

    def refresh_preview(self) -> None:
        """Fit the whole frame into the widget (never a cut-off corner)."""
        try:
            preview = self.query_one(".image-preview", FramePreview)
        except Exception:
            return
        width = preview.size.width or 0
        if width < 8:
            return  # not laid out yet; the resize handler comes later
        width = min(width, IMAGE_PREVIEW_MAX_COLS)
        rows = IMAGE_PREVIEW_MAX_ROWS
        image = (self.block.get("data") or {}).get("image")
        key = (str(image), width, rows)
        if key == self._preview_key:
            return
        self._preview_key = key
        try:
            preview.update(imaging_mod.preview_fitted_or_message(
                image, width=width, height=rows))
        except Exception:
            pass


class Composer(TextArea if _TEXTUAL else object):
    """The chat input: Enter sends the task, Shift+Enter adds a line."""

    if _TEXTUAL:
        class Submitted(Message):
            """The user pressed Enter in the composer."""

            def __init__(self, value: str) -> None:
                super().__init__()
                self.value = value

        def __init__(self, *args, **kwargs):
            kwargs.setdefault("soft_wrap", True)
            super().__init__(*args, **kwargs)

        async def _on_key(self, event) -> None:
            try:
                app = self.app
            except Exception:
                app = None
            slash_open = bool(app is not None and getattr(app, "_slash_open", False))
            if slash_open:
                if event.key == "escape":
                    event.stop()
                    event.prevent_default()
                    try:
                        app._close_slash_menu()
                    except Exception:
                        pass
                    return
                if event.key in ("up", "down"):
                    event.stop()
                    event.prevent_default()
                    try:
                        app._slash_move(-1 if event.key == "up" else 1)
                    except Exception:
                        pass
                    return
                if event.key == "tab":
                    event.stop()
                    event.prevent_default()
                    try:
                        app._slash_complete()
                    except Exception:
                        pass
                    return
            if event.key == "enter":
                event.stop()
                event.prevent_default()
                self.post_message(self.Submitted(self.text))
                return
            if event.key == "shift+enter":
                event.stop()
                event.prevent_default()
                self.insert("\n")
                return
            await super()._on_key(event)


def start_owned_services(settings: dict) -> None:
    """Start the missing owned services and keep the ownership record fresh.

    The heartbeat is what tells another TUI session that these services still
    belong to a live session, so it must keep running while we own them.
    """
    if services_mod is None:
        return
    print("Starting owned background services (Local loopback only)...")
    started_at = time.monotonic()
    try:
        report = services_mod.ensure_services(settings)
        for line in services_mod.report_lines(report):
            print(line)
        print("Service check done in %.1fs." % (time.monotonic() - started_at))
    except Exception as error:
        print("Service auto-start skipped: %s" % error)
    try:
        services_mod.heartbeat_start()
    except Exception:
        pass


if _TEXTUAL:
    class VolumeAgentApp(App):
        TITLE = "Volume Agent"
        # No Ctrl+P anywhere: slash commands in the composer are the only
        # menu. Disabling the palette also stops Textual from auto-adding
        # its own ctrl+p system-palette binding.
        ENABLE_COMMAND_PALETTE = False
        CSS = """
        Screen { layout: vertical; }
        ModalScreen { align: center middle; background: $background 80%; }
        .dlg {
            width: 90%;
            max-width: 100;
            height: auto;
            max-height: 92%;
            border: round $accent;
            background: $surface;
            padding: 1 2;
        }
        #status-row { height: 1; }
        #busy-spinner { width: 3; height: 1; display: none; }
        #busy-spinner.busy { display: block; }
        #status-bar { height: 1; content-align: left middle; }
        #transcript { height: 1fr; }
        #blocks { height: auto; padding: 0 2; }
        .block { height: auto; margin-bottom: 1; }
        #composer-row { height: auto; padding: 0 1; }
        #composer { height: 3; border: round $accent; }
        #btn-send { height: 3; min-width: 10; }
        #slash-menu { height: auto; max-height: 10; padding: 0 1; display: none; }
        #slash-menu.open { display: block; }
        #slash-title { height: 1; padding: 0 1; color: $text-muted; }
        #slash-list { height: auto; max-height: 8; }
        .slash-item { height: 1; width: 1fr; min-width: 0; border: none; padding: 0 1; text-align: left; content-align: left middle; align: left middle; }
        .slash-item.selected { background: $accent; text-style: bold; }
        .slash-item:focus { border: none; }
        .panel { height: auto; padding: 1 2; }
        .row { height: 3; }
        .row > Input { width: 1fr; }
        .row > Select { width: 1fr; }
        .field-label { width: 24; content-align: right middle; padding-right: 1; }
        .scroll-hint { color: $text-muted; }
        #help-text { height: auto; padding: 1 2; }
        #cmd-title { height: auto; padding: 1 2; }
        #confirm-message { height: auto; padding: 1 2; }
        #confirm-hint { height: auto; color: $text-muted; padding: 0 2; }
        #runs-title { height: auto; padding: 1 2; }
        #runs-modal-table { height: 20; }
        #settings-status { height: auto; min-height: 3; padding: 1 2; }
        .image-caption { height: auto; }
        .image-preview { height: auto; }
        .image-block { height: auto; }
        """

        BINDINGS = [
            Binding("f1", "help", "Help"),
            Binding("f2", "glossary", "Glossary"),
            Binding("ctrl+h", "toggle_plumbing", "Technical"),
            Binding("ctrl+t", "toggle_dark", "Theme"),
            Binding("ctrl+q", "quit", "Quit"),
            Binding("escape", "interrupt_run", "Stop"),
            Binding("ctrl+up", "scroll_history", "Scroll up", show=False),
            Binding("ctrl+down", "scroll_history_down", "Scroll down", show=False),
        ]

        def __init__(self, context=None):
            super().__init__()
            self.ctx = context
            self.settings = dict(cfg.DEFAULTS) if cfg else {}
            try:
                if cfg:
                    self.settings = cfg.load_settings()
                if context is not None and getattr(context, "settings", None):
                    self.settings = dict(context.settings)
            except Exception:
                pass
            self.runs: list[dict] = []
            self.live_run_id: str | None = None
            self.live_dir: Path | None = None
            self.live_tail = None
            self.stream_tail = None
            self.live_events: list[dict] = []
            self.live_frozen = False
            self._live_following = True
            self._programmatic_scroll = False
            self._tail_scroll_pending = False
            self._stop_requested_for: str | None = None
            self._end_announced = False
            self.live_image_path: str | None = None
            self.live_image_revision = None
            self._launcher_thread: threading.Thread | None = None
            self._launcher_result: dict = {}
            self._start_locked = False
            self._busy_count = 0
            self.transcript = transcript_mod.Transcript() if transcript_mod else None
            self._widgets: list[tuple[dict, object]] = []
            # One read of the evidence at a time. The swap lock is short
            # (it only picks the tail); the read lock is held while the
            # files are read, never on the UI thread.
            self._poll_running = False
            self._poll_lock = threading.Lock()
            self._read_lock = threading.Lock()
            # The technical rows (model calls, HTTP status) stay hidden until
            # Ctrl+H asks for them: the steps of the run are the story.
            self.show_plumbing = False
            # Slash menu (opencode-style): typed in the composer, not a modal.
            self._slash_open = False
            self._slash_selected = 0
            self._slash_items: list[tuple[str, str, str]] = []

        # -- busy feedback --------------------------------------------------
        def _busy_inc(self) -> None:
            self._busy_count += 1
            self._refresh_busy()

        def _busy_dec(self) -> None:
            self._busy_count = max(0, self._busy_count - 1)
            self._refresh_busy()

        def _refresh_busy(self) -> None:
            """The spinner is widget state: a worker must not touch it directly."""
            self._on_ui(self._set_busy_class, self._busy_count > 0)

        def _set_busy_class(self, busy: bool) -> None:
            try:
                self.query_one("#busy-spinner", LoadingIndicator).set_class(busy, "busy")
            except Exception:
                pass

        def _on_ui(self, func, *args) -> None:
            """Run a widget update on the UI thread (workers call this)."""
            try:
                self.call_from_thread(func, *args)
            except RuntimeError:
                # Already on the UI thread: Textual refuses the hop.
                func(*args)

        def _run_guarded(self, label: str, func, button=None) -> None:
            """Run a blocking callable in a worker with visible feedback.

            The pressed button (if given) shows its spinner and locks against
            double clicks; the status line spinner animates while any worker
            is active; failures surface as error toasts instead of vanishing
            silently.
            """
            if button is not None:
                try:
                    button.loading = True
                    # Textual only blocks a press on a disabled button, so
                    # the spinner alone would not stop a double click.
                    button.disabled = True
                except Exception:
                    pass
            self._busy_inc()

            def _finish(message=None, error=False):
                if button is not None:
                    try:
                        button.loading = False
                        button.disabled = False
                    except Exception:
                        pass
                self._busy_dec()
                if message is not None:
                    try:
                        if error:
                            self.notify(message, severity="error", timeout=6, markup=False)
                        else:
                            self.notify(message, timeout=3, markup=False)
                    except Exception:
                        pass

            def _wrapped():
                try:
                    func()
                except Exception as err:  # noqa: BLE001 - surfaced as toast
                    self.call_from_thread(_finish, "%s failed: %s" % (label, err), True)
                else:
                    self.call_from_thread(_finish)
            self.run_worker(_wrapped, thread=True)

        # -- helpers to allow injection in tests -------------------------
        def _cfg(self):
            return cfg

        def _runs_mod(self):
            if self.ctx is not None and getattr(self.ctx, "discover_runs", None):
                return self.ctx
            return runs_mod

        def discover_runs(self, fresh: bool = False) -> list[dict]:
            mod = self._runs_mod()
            try:
                if mod is self.ctx:
                    return list(mod.discover_runs(fresh=fresh))
                return list(mod.discover_runs(limit=200, _fresh=fresh))
            except Exception:
                return []

        def _volumes_mod(self):
            if self.ctx is not None and getattr(self.ctx, "discover_volumes", None):
                return self.ctx
            return volumes_mod

        def discover_volumes_list(self) -> list[dict]:
            """Volumes from volumes/ + current selection (read-only)."""
            current = ""
            try:
                selected = cfg.selected_volume(self.settings) if cfg else None
                current = str(selected) if selected else ""
            except Exception:
                current = ""
            mod = self._volumes_mod()
            try:
                if mod is self.ctx:
                    return list(mod.discover_volumes(current or None))
                if mod is None:
                    return []
                return list(mod.discover_volumes(current or None))
            except Exception:
                return []

        # -- layout -------------------------------------------------------
        def compose(self) -> ComposeResult:
            with Horizontal(id="status-row"):
                yield LoadingIndicator(id="busy-spinner")
                yield Static("", id="status-bar")
            with TranscriptScroll(id="transcript"):
                yield Vertical(id="blocks")
            with Vertical(id="slash-menu"):
                yield Static("Commands (Up/Down moves, Tab completes, Enter runs, Esc closes)",
                             id="slash-title")
                with VerticalScroll(id="slash-list"):
                    # Built once: filtering only toggles visibility, so each
                    # keystroke updates instantly with no remount flicker.
                    for _slash, _action, _tooltip in slash_commands():
                        yield Button("%s  - %s" % (_slash, _tooltip), id="slash-%s" % _action,
                                     classes="slash-item", tooltip=_tooltip)
            with Horizontal(id="composer-row"):
                yield Composer(id="composer")
                yield Button("Send", id="btn-send", variant="primary",
                             tooltip="Start a run with this task (Enter does the same). "
                             "A line starting with / runs a slash command.")
            yield Footer()

        # -- lifecycle ----------------------------------------------------
        async def on_mount(self):
            # The screen has its own fast clock; the backend keeps its own.
            self.set_interval(UI_TICK_SECONDS, self._animate_thinking)
            self.set_interval(BACKEND_TICK_SECONDS, self._tick)
            self._say_welcome()

        def _say_welcome(self) -> None:
            """The empty state: what this screen is and how to use it."""
            if self.transcript is None:
                return
            self.transcript.add(transcript_mod.NOTICE, title=(
                "Volume Agent - you write a task, the model asks the renderer for\n"
                "pictures and picks one as the result.\n"
                "Each picture costs one observation (the top line shows the budget).\n"
                "F2 the words - / the commands - Ctrl+H the technical rows"))
            self._sync_transcript()

        # -- the conversation ----------------------------------------------
        def _sync_transcript(self) -> None:
            """Mount a widget per block and re-render the ones that changed."""
            if self.transcript is None:
                return
            try:
                container = self.query_one("#blocks", Vertical)
            except Exception:
                return
            live = {id(block) for block in self.transcript.blocks}
            added = False
            for block in self.transcript.blocks:
                # The technical view is a screen setting: every block follows it.
                data = block.get("data")
                if isinstance(data, dict):
                    data["plumbing_visible"] = self.show_plumbing
            for block, widget in list(self._widgets):
                if id(block) not in live:
                    # The block was reclassified (the words before a tool call
                    # moved into the Thinking block): its widget goes away.
                    try:
                        widget.remove()
                    except Exception:
                        pass
                    self._widgets.remove((block, widget))
            # The mounted widget of a block is the one that is on screen: a
            # new instance would update nothing the reader sees.
            mounted = {id(block): widget for block, widget in self._widgets}
            for block in self.transcript.blocks:
                widget = mounted.get(id(block))
                if widget is None:
                    widget = self._widget_for(block)
                    container.mount(widget)
                    self._widgets.append((block, widget))
                    added = True
                self._set_visible(widget, self._block_visible(block))
                try:
                    # The content is pushed to every block: the height follows
                    # the text, so a growing answer is never clipped.
                    widget.set_block(block)
                except Exception:
                    pass
            if added and self._live_following:
                # The new widgets are laid out after this call, so the tail is
                # reached once the screen knows how tall the conversation is.
                self._tail_scroll_pending = True
                try:
                    self.call_after_refresh(self._scroll_to_tail)
                except Exception:
                    self._scroll_to_tail()

        def _widget_for(self, block: dict):
            kind = str(block.get("kind"))
            if kind == transcript_mod.THINKING:
                return ThinkingBlock(block)
            if kind == transcript_mod.IMAGE:
                return ImageBlock(block)
            return TranscriptBlock(block)

        def _block_visible(self, block: dict) -> bool:
            """Is this row part of the story?

            The transport (the model calls, the HTTP status) is plumbing: it
            is written to the evidence, but it only appears on screen when
            Ctrl+H asks for the technical view.
            """
            if not transcript_mod.is_plumbing(str(block.get("kind"))):
                return True
            return bool(self.show_plumbing)

        def _set_visible(self, widget, visible: bool) -> None:
            """Show or hide one row (the plumbing rows are hidden by default)."""
            try:
                widget.display = bool(visible)
            except Exception:
                try:
                    widget.styles.display = "block" if visible else "none"
                except Exception:
                    pass

        def _refresh_block_widget(self, block: dict) -> None:
            for known, widget in self._widgets:
                if known is block:
                    try:
                        widget.set_block(block)
                    except Exception:
                        pass
                    return

        def _clear_transcript(self) -> None:
            self.transcript.clear() if self.transcript is not None else None
            self._widgets = []
            try:
                self.query_one("#blocks", Vertical).remove_children()
            except Exception:
                pass

        def _say(self, kind: str, title: str, body: str = "") -> None:
            """Put one line into the conversation (system notices)."""
            if self.transcript is None:
                return
            self.transcript.add(kind, title=title, body=body)
            self._sync_transcript()

        def _follow_tail(self) -> None:
            """Follow the newest entry while the view sits at the bottom.

            The decision is made before the new entry is mounted: the content
            grows under the view, and a view that was at the tail stays there.
            """
            try:
                scroller = self.query_one("#transcript", VerticalScroll)
            except Exception:
                return
            if not self._at_bottom():
                self._live_following = False
                return
            self._live_following = True
            self._scroll_to_tail()

        def _at_bottom(self) -> bool:
            """Is the view still at the tail of the conversation?"""
            try:
                scroller = self.query_one("#transcript", TranscriptScroll)
                return scroller.scroll_offset.y >= scroller.max_scroll_y - 1
            except Exception:
                return True

        def _user_scrolled(self) -> None:
            """The user moved the view: the follow follows the new position.

            Scrolling up to read the history stops the follow; scrolling back
            to the tail starts it again (there is no Follow button).
            """
            if self._programmatic_scroll:
                return
            self._tail_scroll_pending = False
            self._live_following = self._at_bottom()

        def _scroll_to_tail(self, retries: int = 4) -> None:
            """Scroll to the newest entry once the screen knows its height.

            The conversation is built from widgets, so the height of a new
            entry is only known after the next layout. The scroll therefore
            runs again after every refresh until the view has settled - unless
            the user scrolls away, which ends the run of scrolls.
            """
            if not self._tail_scroll_pending:
                return
            try:
                scroller = self.query_one("#transcript", TranscriptScroll)
                self._programmatic_scroll = True
                try:
                    # immediate=True: the scroll must not wait for another
                    # refresh, or a user scroll in between would be lost.
                    scroller.scroll_end(animate=False, immediate=True)
                finally:
                    self._programmatic_scroll = False
            except Exception:
                return
            if retries > 0:
                try:
                    self.call_after_refresh(self._scroll_to_tail, retries - 1)
                except Exception:
                    pass
            else:
                self._tail_scroll_pending = False

        def _tick(self) -> None:
            """Read the backend: the run, the status line, the button.

            The evidence is read in a worker, so a slow backend never
            freezes the spinner - the screen paints at its own speed.
            """
            if self.live_dir and not self.live_frozen and not self._poll_running:
                self._start_poll()
            self._render_status()
            self._sync_send_button()

        def _start_poll(self) -> None:
            """Start one read of the evidence (in a worker thread)."""
            self._poll_running = True

            def _work() -> None:
                try:
                    payload = self._read_evidence()
                except Exception:
                    payload = (None, [], [], None)
                try:
                    self.call_from_thread(self._apply_poll, payload)
                except Exception:
                    pass

            self.run_worker(_work, thread=True)

        def _read_evidence(self):
            """Read the run's evidence. Worker thread: no UI here.

            The lock keeps one read at a time, so two readers never walk
            the same tail at once. The run id is read under the lock, so
            the payload always belongs to the run it was read from.
            """
            with self._poll_lock:
                tail = self.live_tail
                stream = self.stream_tail
                run_id = self.live_run_id
                live_dir = self.live_dir
            assert tail is not None and live_dir is not None
            with self._read_lock:
                # Re-resolve session dir once the launcher writes session.json.
                if tail.session_dir is None:
                    found = trace_mod.resolve_session_dir(live_dir)
                    if found:
                        tail.session_dir = found
                fresh = tail.poll()
                # The recorded model stream: the answer and the reasoning
                # arrive while the model is still writing them.
                streamed = []
                if stream is not None:
                    try:
                        streamed = stream.poll()
                    except Exception:
                        streamed = []
                # Finished/failed freezes the view (no more polling).
                try:
                    status = json.loads((live_dir / "run.json").read_text(encoding="utf-8")).get("status")
                except (OSError, ValueError):
                    status = None
                if status in ("finished", "failed"):
                    for _ in range(2):
                        fresh.extend(tail.poll())
                return run_id, fresh, streamed, status

        def _apply_poll(self, payload) -> None:
            """Put the new evidence on the screen. UI thread."""
            run_id, fresh, streamed, status = payload
            self._poll_running = False
            if run_id is not None and run_id != self.live_run_id:
                return  # the run changed while the read was running
            if fresh:
                self.live_events.extend(fresh)
            if status in ("finished", "failed"):
                self.live_frozen = True
                self._close_thinking()
                self._announce_the_end()
            self._ingest_events(fresh)
            self._ingest_events(streamed)
            self._render_live()
            # The poll learned the state of the run: the line and the button
            # follow it right away, not at the next tick.
            self._render_status()
            self._sync_send_button()

        def _animate_thinking(self) -> None:
            """The screen's own clock: the spinner and the seconds."""
            if self.transcript is None:
                return
            block = self.transcript.find(transcript_mod.THINKING)
            if block is None or block.get("status") != "running":
                return
            started = block.get("ts") or time.time()
            block["spinner"] = _spinner_frame()
            block["subtitle"] = "%ds" % int(max(0.0, time.time() - started) + 0.5)
            self._refresh_block_widget(block)

        # -- live ---------------------------------------------------------
        def start_live(self, run_dir: str | Path, run_id: str | None = None):
            """Open a run: the conversation starts with the task again."""
            with self._poll_lock:
                self.live_dir = Path(run_dir)
                self.live_run_id = run_id or self.live_dir.name
                self.live_tail = trace_mod.EventTail(
                    self.live_dir, trace_mod.resolve_session_dir(self.live_dir))
                self.stream_tail = (stream_mod.StreamTail(self.live_dir)
                                    if stream_mod else None)
            self.live_events = []
            self.live_frozen = False
            self._live_following = True
            self._stop_requested_for = None
            self._end_announced = False
            self.live_image_path = None
            self.live_image_revision = None
            self._clear_transcript()
            if self.transcript is None:
                return
            task = self._task_text(self.live_dir)
            if task:
                self.transcript.add(transcript_mod.USER, title=task,
                                    ts=self._run_started_at(self.live_dir))
            self._sync_transcript()
            self._sync_send_button()
            self._render_status()

        def _run_started_at(self, run_dir: Path | None) -> float | None:
            """When the run started (the task comes first in the log).

            The steps of the run carry the time they happened; the task
            row carries the time the run began, so the log reads in order.
            """
            if run_dir is None or runs_mod is None:
                return None
            try:
                return runs_mod.run_started_at(run_dir)
            except Exception:
                return None

        def _run_config(self, run_dir: Path | None) -> dict:
            """The config of the run (the task and the model it ran with)."""
            if run_dir is None:
                return {}
            try:
                config = json.loads((run_dir / "config.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return {}
            return config if isinstance(config, dict) else {}

        def _task_text(self, run_dir: Path | None) -> str:
            """What the run was asked to do (the task of its config)."""
            return str(self._run_config(run_dir).get("task") or "").strip()

        def _run_model(self) -> str:
            """The model of the open run (not the model of the TUI).

            An older run was made with another model: the log has to name
            the model that did the work.
            """
            model = str(self._run_config(self.live_dir).get("model") or "").strip()
            if model:
                return model
            return str(self.settings.get("model") or "")

        def _poll_now(self):
            """Read the evidence right now, on this thread.

            The app itself never does this: the 0.5s tick reads the
            evidence in a worker. This is the synchronous path, for the
            tests and for a caller that wants the evidence at once.
            """
            self._apply_poll(self._read_evidence())

        def _announce_the_end(self) -> None:
            """Say how the run ended - a stop must be visible, not guessed."""
            if self._end_announced:
                return
            self._end_announced = True
            state = self._run_state()
            if state == "stopped":
                self._say(transcript_mod.NOTICE,
                          "Run stopped - the harness and its children were terminated, "
                          "the evidence stays on disk.")
            elif state == "failed":
                self._say(transcript_mod.NOTICE, "Run failed - see failure.json in the run folder.")

        def _close_thinking(self) -> None:
            """The run is over: the thinking block does not spin any more."""
            if self.transcript is None:
                return
            block = self.transcript.find(transcript_mod.THINKING)
            if block is not None and block.get("status") == "running":
                block["status"] = "done"
                self._refresh_block_widget(block)

        def _ingest_events(self, events: list[dict]) -> None:
            """Fold the new evidence into the conversation."""
            if self.transcript is None or not events:
                return
            changed: list[dict] = []
            for event in events:
                changed.extend(self.transcript.ingest(event))
            if not changed:
                return
            for block in changed:
                if block.get("kind") == transcript_mod.IMAGE:
                    self.live_image_path = (block.get("data") or {}).get("image")
                    self.live_image_revision = (block.get("data") or {}).get("revision")
            self._sync_transcript()

        def _render_live(self):
            """The conversation is the live view: nothing else to paint."""
            self._sync_transcript()

        def _render_status(self) -> None:
            """The status line: which run, which model, how much budget is spent.

            The model works in the conversation, not here: the Thinking block
            inside it says that, so the line stays a plain status line.
            """
            from rich.text import Text

            text = Text()
            text.append("● ", style="bold %s" % ACCENT)
            text.append("volume-agent", style="bold")
            text.append("  %s" % self.settings.get("model", "?"), style="dim")
            volume = self._volume_name()
            if volume:
                text.append("  ·  %s" % volume, style="dim")
            if self.live_run_id:
                text.append("  ·  %s" % self.live_run_id, style="dim")
                text.append("  ·  %s" % self._run_state(), style="dim")
            used, budget = self._budget()
            if budget:
                text.append("  ·  %s of %s observations used" % (used, budget),
                           style="dim")
            try:
                self.query_one("#status-bar", Static).update(text)
            except Exception:
                pass

        def _volume_name(self) -> str:
            """The volume the agent works on, in words.

            A file name like present246x246x221.dat means nothing to a
            newcomer; "246x246x221 voxels" does.
            """
            try:
                selected = cfg.selected_volume(self.settings) if cfg else None
            except Exception:
                selected = None
            if not selected:
                return ""
            name = Path(selected).name
            match = _re.search(r"(\d+)x(\d+)x(\d+)", name)
            if match:
                return "volume %sx%sx%s voxels" % match.groups()
            return name

        def _run_state(self) -> str:
            """What the open run is doing.

            Read from the run's own run.json, so the line never claims that a
            stopped run is still running: running / stopping / stopped /
            finished / failed / idle.
            """
            if self.live_dir is None:
                return "idle"
            record = {}
            try:
                record = json.loads((self.live_dir / "run.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                record = {}
            if record:
                if record.get("interrupted"):
                    return "stopped"
                status = str(record.get("status") or "running")
                if status != "running":
                    return status
            if self._stop_requested_for == self.live_run_id:
                return "stopping"
            return "running"

        def _run_is_live(self) -> bool:
            """Is the agent still working? (a stop on its way counts as not)"""
            if not self.live_run_id:
                return False
            if self._stop_requested_for == self.live_run_id:
                return False
            return self._run_state() == "running"

        def _sync_send_button(self) -> None:
            """While the agent works the button stops it instead of sending."""
            try:
                button = self.query_one("#btn-send", Button)
            except Exception:
                return
            state = self._run_state()
            if state == "running":
                label, variant = "Stop", "error"
                tooltip = "Stop the running agent now (Esc does the same)."
            elif state == "stopping":
                label, variant = "Stopping…", "error"
                tooltip = "The agent is stopping; the evidence stays on disk."
            else:
                label, variant = "Send", "primary"
                tooltip = "Start a run with this task (Enter does the same)."
            if str(button.label) != label:
                button.label = label
                button.variant = variant
                button.tooltip = tooltip

        def _budget(self) -> tuple[int, int]:
            """Observations used and allowed, read from the run's own session."""
            meta = getattr(self.live_tail, "meta", None) or {}
            used = meta.get("used")
            budget = meta.get("budget")
            if not isinstance(budget, (int, float)) and self.live_dir is not None:
                try:
                    config = json.loads((self.live_dir / "config.json").read_text(encoding="utf-8"))
                    budget = config.get("renderBudget")
                except (OSError, ValueError):
                    budget = None
            return (int(used) if isinstance(used, (int, float)) else 0,
                    int(budget) if isinstance(budget, (int, float)) else 0)

        # -- actions ------------------------------------------------------
        def action_commands(self):
            # The only menu: slash commands in the composer (no Ctrl+P).
            self._open_slash_menu()

        def _command_chosen(self, result):
            if not result:
                return
            # Both the retired modal ids (cmd-...) and the slash ids
            # (slash-...) route here.
            if result.startswith("slash-"):
                result = "cmd-" + result[len("slash-"):]
            if result == "cmd-live-stop":
                self._stop_run()
            elif result == "cmd-live-verify":
                self._verify_live()
            elif result == "cmd-live-copy":
                self._copy_run_json_log()
            elif result == "cmd-live-copy-chat":
                self._copy_chat_log()
            elif result == "cmd-live-export-chat":
                self._export_chat_log()
            elif result == "cmd-live-open-png":
                self._open_live_png()
            elif result == "cmd-live-open":
                self._open_live_folder()
            elif result == "cmd-runs-open":
                self._open_runs()
            elif result == "cmd-volume-switch":
                self._open_volumes()
            elif result == "cmd-settings":
                self._open_settings()
            elif result == "cmd-theme":
                self.action_toggle_dark()
            elif result == "cmd-help":
                self.action_help()
            elif result in ("cmd-glossary", "glossary"):
                self.action_glossary()
            elif result == "cmd-hard-stop":
                self._hard_stop()
            elif result == "cmd-quit":
                self.action_quit_confirm()

        # -- slash menu (opencode-style) ------------------------------------
        def _slash_prefix(self):
            """What was typed after '/' in the composer, or None."""
            try:
                text = self.query_one("#composer", Composer).text
            except Exception:
                return None
            stripped = str(text).lstrip()
            if not stripped.startswith("/"):
                return None
            first = stripped.split(None, 1)[0]
            return first[1:]

        def _refresh_slash_menu(self) -> None:
            """Show the opencode-style menu while the composer holds '/...'.

            The buttons are built once in compose(); filtering only toggles
            their visibility, so every keystroke (even a single char like /v)
            updates instantly with no remount flicker or lost frames.
            """
            try:
                menu = self.query_one("#slash-menu")
            except Exception:
                return
            prefix = self._slash_prefix()
            if prefix is None:
                self._slash_open = False
                self._slash_items = []
                try:
                    menu.remove_class("open")
                except Exception:
                    pass
                return
            items = match_slash_commands(prefix)
            self._slash_items = items
            if self._slash_selected >= len(items):
                self._slash_selected = 0
            self._slash_open = True
            visible = {action for _slash, action, _tip in items}
            selected_action = items[self._slash_selected][1] if items else None
            try:
                menu.add_class("open")
                try:
                    if items:
                        self.query_one("#slash-title", Static).update(
                            "%d command%s (Up/Down moves, Tab completes, Enter runs, Esc closes)"
                            % (len(items), "" if len(items) == 1 else "s"))
                    else:
                        self.query_one("#slash-title", Static).update(
                            "No match - try /help (Esc closes)")
                except Exception:
                    pass
                for button in self.query("#slash-list .slash-item"):
                    try:
                        action = str(getattr(button, "id", "") or "")
                        if action.startswith("slash-"):
                            action = action[len("slash-"):]
                    except Exception:
                        continue
                    try:
                        button.display = action in visible
                    except Exception:
                        try:
                            button.styles.display = "block" if action in visible else "none"
                        except Exception:
                            pass
                    try:
                        button.set_class(action == selected_action, "selected")
                    except Exception:
                        pass
                if selected_action:
                    try:
                        widget = self.query_one("#slash-%s" % selected_action, Button)
                        self.query_one("#slash-list", VerticalScroll).scroll_to_widget(widget)
                    except Exception:
                        pass
            except Exception:
                pass

        def _open_slash_menu(self) -> None:
            """Focus the composer with '/' so the menu lists every command."""
            try:
                composer = self.query_one("#composer", Composer)
            except Exception:
                return
            try:
                text = str(composer.text or "")
            except Exception:
                text = ""
            if not text.lstrip().startswith("/"):
                try:
                    composer.text = "/"
                except Exception:
                    pass
            self._slash_selected = 0
            try:
                composer.focus()
            except Exception:
                pass
            self._refresh_slash_menu()

        def _close_slash_menu(self) -> None:
            self._slash_open = False
            self._slash_selected = 0
            try:
                self.query_one("#slash-menu").remove_class("open")
            except Exception:
                pass

        def _slash_move(self, delta: int) -> None:
            if not self._slash_open or not self._slash_items:
                return
            self._slash_selected = (self._slash_selected + delta) % len(self._slash_items)
            self._refresh_slash_menu()

        def _slash_complete(self) -> None:
            """Tab completes the composer with the highlighted command."""
            if not self._slash_open or not self._slash_items:
                return
            if not (0 <= self._slash_selected < len(self._slash_items)):
                return
            slash = self._slash_items[self._slash_selected][0]
            try:
                composer = self.query_one("#composer", Composer)
                rest = ""
                try:
                    current = str(composer.text or "")
                    parts = current.lstrip().split(None, 1)
                    if len(parts) > 1:
                        rest = " " + parts[1]
                except Exception:
                    rest = ""
                composer.text = slash + (rest if rest else " ")
            except Exception:
                pass
            self._refresh_slash_menu()

        def _run_slash_text(self, text: str) -> bool:
            """Run a '/command' composer line. True when the line was slash."""
            if not str(text or "").lstrip().startswith("/"):
                return False
            action = parse_slash_command(text)
            if action is None:
                prefix = str(text).lstrip().split(None, 1)[0]
                if prefix.strip() == "/":
                    # Bare '/': keep the menu open, nothing to run.
                    self._refresh_slash_menu()
                    return True
                self._say(transcript_mod.NOTICE,
                          "Unknown command %s - type / to list every command." % prefix)
                self._close_slash_menu()
                return True
            try:
                self.query_one("#composer", Composer).clear()
            except Exception:
                pass
            self._close_slash_menu()
            self._command_chosen("cmd-" + action)
            return True

        def on_text_area_changed(self, event) -> None:
            try:
                changed = getattr(event, "text_area", None)
                if changed is not None and getattr(changed, "id", "") != "composer":
                    return
            except Exception:
                pass
            self._refresh_slash_menu()

        def action_help(self):
            self.push_screen(HelpScreen())

        def action_glossary(self) -> None:
            """F2: the words the screen uses, explained."""
            self.push_screen(GlossaryScreen())

        def action_toggle_plumbing(self) -> None:
            """Ctrl+H: the technical rows (model calls, HTTP status).

            The steps of the run are the default view. The transport is one
            key press away, for whoever needs it.
            """
            self.show_plumbing = not self.show_plumbing
            self._sync_transcript()
            self.notify("Technical rows: %s" % ("on" if self.show_plumbing else "off"),
                        timeout=3, markup=False)

        def action_toggle_dark(self) -> None:
            """Ctrl+T switches between the light and the dark theme."""
            try:
                self.theme = ("textual-light" if self.current_theme.dark
                              else "textual-dark")
            except Exception:
                self.dark = not getattr(self, "dark", True)

        def action_scroll_history(self) -> None:
            self._scroll_transcript(-8)

        def action_scroll_history_down(self) -> None:
            self._scroll_transcript(8)

        def _scroll_transcript(self, delta: int) -> None:
            try:
                scroller = self.query_one("#transcript", TranscriptScroll)
            except Exception:
                return
            scroller.scroll_relative(y=delta, animate=False, immediate=True)
            # The user moved the view: the follow follows the new position, so
            # reading the history is not interrupted by the next entry.
            self._live_following = self._at_bottom()

        def action_interrupt_run(self) -> None:
            """Esc closes the slash menu first, else stops the agent."""
            if getattr(self, "_slash_open", False):
                self._close_slash_menu()
                return
            self._stop_run()

        def action_quit(self) -> None:
            """Ctrl+Q asks first while a run is live, then leaves."""
            self.action_quit_confirm()

        def action_quit_confirm(self):
            """Quit: stop every process this TUI owns, then leave."""
            live = self._live_run_ids()
            if live:
                self.push_screen(ConfirmScreen(
                    "Quit the TUI and stop everything it owns?\n\n"
                    "  - agent run(s): %s (launcher, harness, MCP children)\n"
                    "  - the renderer service this TUI started\n"
                    "  - the local Ollama this TUI started (model unloaded from VRAM)\n\n"
                    "Nothing is left running. The run keeps its evidence on disk."
                    % ", ".join(live),
                    on_yes=self._quit_and_stop))
            else:
                self._quit_and_stop()

        def _live_run_ids(self) -> list[str]:
            """Run ids that are still running (the runs on disk and the open one)."""
            ids = []
            for entry in self.runs:
                if runs_mod and runs_mod.is_live(entry) and entry.get("runId"):
                    ids.append(str(entry["runId"]))
            if self.live_run_id and self.live_run_id not in ids:
                ids.append(str(self.live_run_id))
            return ids

        def _hard_stop(self) -> None:
            """The hard stop: kill the agent tree, the local Ollama, the services.

            Textual uses Ctrl+C for copying the selection, so the hard stop
            lives in the slash menu (/hard-stop) and in closing the window.
            """
            self._busy_inc()
            try:
                services_mod.block_interrupts()
            except Exception:
                pass

            def _work():
                try:
                    services_mod.shutdown_all(self.settings, hard=True, cache=False)
                except Exception:
                    pass
                try:
                    self.call_from_thread(self._busy_dec)
                    self.call_from_thread(self.exit, 0)
                except Exception:
                    pass

            self.run_worker(_work, thread=True)

        def _quit_and_stop(self) -> None:
            """Leave no process behind, then exit.

            The agent run is stopped cooperatively first (the launcher still
            records interrupted/failure.json), everything that survives is
            killed, and the services this TUI started are stopped with it.
            """
            self._busy_inc()
            # The cleanup runs in a worker, so SIGINT is ignored up front (main
            # thread) — a second Ctrl+C must not leave half the tree running.
            try:
                services_mod.block_interrupts()
            except Exception:
                pass

            def _work():
                try:
                    services_mod.shutdown_all()
                except Exception:
                    pass
                try:
                    self.call_from_thread(self._busy_dec)
                    self.call_from_thread(self.exit, 0)
                except Exception:
                    pass

            self.run_worker(_work, thread=True)

        def _stop_run(self) -> None:
            """Ask the agent to stop now; the evidence already written stays."""
            if not self.live_run_id:
                self.notify("No run is open.", severity="warning", timeout=4, markup=False)
                return
            run_id = self.live_run_id
            if self._stop_requested_for == run_id:
                # Stop asked for this run already: one request, one line.
                return
            self._stop_requested_for = run_id
            try:
                runner_mod.request_stop(run_id)
                self._say(transcript_mod.NOTICE,
                          "Stop requested for %s - the harness and its children are "
                          "terminated now, the evidence stays on disk." % run_id)
            except Exception as error:
                self._stop_requested_for = None
                self._say(transcript_mod.ERROR, "Stop failed: %s" % error)
            self._sync_send_button()
            self._render_status()

        def _verify_live(self, button=None):
            if not self.live_dir:
                self._say(transcript_mod.NOTICE,
                          "Verify evidence: no run is open - open one first.")
                return
            run_dir = str(self.live_dir)
            self._run_guarded("Verifying evidence", lambda: self._run_verify(run_dir), button)

        def _run_verify(self, run_dir: str):
            try:
                result = runs_mod.run_verify(run_dir)
                lines = runs_mod.verify_summary(result.get("report"))
                text = "Verify evidence (read-only) for %s:\n%s" % (
                    Path(run_dir).name, "\n".join(lines[:6]))
                self.call_from_thread(self._say, transcript_mod.NOTICE, text)
            except Exception as error:
                self.call_from_thread(self._say, transcript_mod.ERROR,
                                      "Verify evidence failed: %s" % error)

        def _copy_run_json_log(self, button=None) -> None:
            """Copy the whole run's JSON log to the clipboard (read-only).

            The payload is one JSON document: the run's meta data, every
            event of the timeline and every JSON log file of the run folder.
            Base64 image bytes become a size note and files that could not
            be read are listed inside the payload — nothing is dropped
            silently.
            """
            if not self.live_dir:
                self.notify("No run is open - open one first.",
                            severity="warning", timeout=4, markup=False)
                return
            run_dir = str(self.live_dir)
            events = list(self.live_events)

            def _work() -> None:
                document = runs_mod.run_json_log(run_dir, events=events)
                text = json.dumps(document, indent=2, ensure_ascii=False)
                message = evidence_mod.copy_to_clipboard(text)
                self.call_from_thread(self._copy_finished, text, message)

            self._run_guarded("Copying the JSON log", _work, button)

        def _copy_finished(self, text: str, message: str) -> None:
            if message.startswith("Clipboard copy failed"):
                # Fall back to the terminal's own clipboard escape (OSC 52).
                try:
                    self.copy_to_clipboard(text)
                except Exception:
                    pass
                self.notify(message + " - the terminal clipboard escape was used instead.",
                            severity="warning", timeout=6, markup=False)
                return
            self.notify(message, timeout=4, markup=False)

        # -- the chat record as a log ---------------------------------------
        def _chat_log_blocks(self) -> list[dict]:
            """The conversation as it is on screen (the chat record)."""
            if self.transcript is None:
                return []
            return list(self.transcript.blocks)

        def _chat_log_text(self) -> str:
            """The chat record as plain text - the words the screen shows.

            The same blocks, without the markup: the task, the model's
            thinking, the tool calls, the frames and the answer.
            """
            return chat_log_mod.chat_log_text(
                self._chat_log_blocks(),
                run_id=self.live_run_id,
                model=self._run_model(),
                run_dir=str(self.live_dir) if self.live_dir else None)

        def _copy_chat_log(self, button=None) -> None:
            """Copy the chat record to the clipboard (read-only).

            The conversation in plain text: the task, the model's thinking,
            the tool calls with their numbers, the frames and the answer.
            """
            if not self.live_dir:
                self.notify("No run is open - open one first.",
                            severity="warning", timeout=4, markup=False)
                return
            if not self._chat_log_blocks():
                self.notify("Nothing to copy - the conversation is empty.",
                            severity="warning", timeout=4, markup=False)
                return
            text = self._chat_log_text()

            def _work() -> None:
                message = evidence_mod.copy_to_clipboard(text)
                self.call_from_thread(self._copy_finished, text, message)

            self._run_guarded("Copying the chat log", _work, button)

        def _export_chat_log(self, button=None) -> None:
            """Write the chat record to a log file (the run folder stays read-only).

            The file lands in runtime/tui/chat-logs/, one file per export.
            """
            if not self.live_dir:
                self.notify("No run is open - open one first.",
                            severity="warning", timeout=4, markup=False)
                return
            blocks = self._chat_log_blocks()
            if not blocks:
                self.notify("Nothing to export - the conversation is empty.",
                            severity="warning", timeout=4, markup=False)
                return
            run_id = self.live_run_id
            model = self._run_model()
            run_dir = str(self.live_dir) if self.live_dir else None

            def _work() -> None:
                path = chat_log_mod.export_chat_log(
                    blocks, run_id=run_id, model=model, run_dir=run_dir)
                self.call_from_thread(self._chat_log_exported, path)

            self._run_guarded("Exporting the chat log", _work, button)

        def _chat_log_exported(self, path) -> None:
            self.notify("Chat log written: %s" % path, timeout=6, markup=False)

        # -- where the frames live -----------------------------------------
        def _live_png_path(self) -> Path | None:
            """Absolute path of the frame PNG that is currently previewed."""
            image = getattr(self, "live_image_path", None)
            return Path(image) if image else None

        def _live_frames_folder(self) -> Path | None:
            """Folder that actually holds the PNGs.

            The run directory only holds logs and JSON, so opening it hides
            every screenshot. The frames live in the session folder
            (runtime/<sessionId>/frame-N/image.png); fall back to it when no
            frame has been rendered yet.
            """
            png = self._live_png_path()
            if png is not None:
                parent = png.parent
                if parent.name.startswith("frame-") and parent.parent.is_dir():
                    return parent.parent
                return parent
            if self.live_dir is not None:
                try:
                    session = trace_mod.resolve_session_dir(self.live_dir)
                except Exception:
                    session = None
                if session:
                    return session
                return self.live_dir
            return None

        def _open_live_png(self) -> None:
            png = self._live_png_path()
            if png is None:
                self.notify("No frame PNG yet - wait for the first render.",
                            severity="warning", timeout=4, markup=False)
                return
            message = evidence_mod.open_path(str(png))
            self.notify(message, timeout=5, markup=False)

        def _open_live_folder(self) -> None:
            folder = self._live_frames_folder()
            if folder is None:
                self.notify("No folder to open yet - start or open a run first.",
                            severity="warning", timeout=4, markup=False)
                return
            message = evidence_mod.open_path(str(folder))
            self.notify(message, timeout=5, markup=False)

        def _refresh_frame_display(self) -> None:
            """Re-fit every frame in the conversation to its current width."""
            for _block, widget in list(self._widgets):
                try:
                    widget.refresh_preview()
                except Exception:
                    pass

        # -- runs -----------------------------------------------------------
        def _open_runs(self) -> None:
            """Open one of the runs on this machine in the conversation."""
            runs = self.discover_runs(fresh=True)
            self.runs = runs
            if not runs:
                self.notify("No runs on disk yet.", severity="warning", timeout=4, markup=False)
                return
            self.push_screen(RunsScreen(runs), callback=self._run_chosen)

        def _run_chosen(self, run) -> None:
            if not run:
                return
            self.start_live(run["directory"], run.get("runId"))
            self._start_poll()

        # -- volumes --------------------------------------------------------
        def _open_volumes(self) -> None:
            """Pick the volume the next run works on."""
            items = self.discover_volumes_list()
            if not items:
                self.notify("No volumes found in volumes/.",
                            severity="warning", timeout=4, markup=False)
                return
            current = ""
            try:
                selected = cfg.selected_volume(self.settings) if cfg else None
                current = str(selected) if selected else ""
            except Exception:
                current = ""
            self.push_screen(VolumesScreen(items, current), callback=self._volume_chosen)

        def _volume_chosen(self, volume) -> None:
            if not volume:
                return
            path = str((volume or {}).get("path") or "")
            if not path:
                return
            self.settings = dict(self.settings)
            self.settings["volumePath"] = path
            try:
                if self.ctx is not None and getattr(self.ctx, "put_settings", None):
                    self.ctx.put_settings(self.settings)
                elif cfg:
                    cfg.save_settings(self.settings)
            except Exception:
                pass
            label = str((volume or {}).get("label") or path)
            try:
                self.notify("Volume: %s" % label, timeout=4, markup=False)
            except Exception:
                pass
            try:
                self._render_status()
            except Exception:
                pass

        # -- settings --------------------------------------------------------
        def _open_settings(self) -> None:
            self.push_screen(SettingsScreen(self.settings), callback=self._settings_saved)

        def _settings_saved(self, settings) -> None:
            if settings:
                self.settings = dict(settings)

        # -- the composer ----------------------------------------------------
        def action_submit_task(self) -> None:
            """Enter in the composer starts a run - or runs a /command."""
            try:
                composer = self.query_one("#composer", Composer)
            except Exception:
                return
            task = composer.text.strip()
            if not task:
                return
            # Slash first (opencode-style): a command never starts a run.
            try:
                if self._run_slash_text(task):
                    return
            except Exception:
                pass
            if self._run_state() == "running":
                # One run at a time: the agent has to stop first (Esc).
                self.notify("The agent is still working - press Esc or Stop first.",
                            severity="warning", timeout=4, markup=False)
                return
            composer.clear()
            if self._start_locked:
                self.notify("A run is already starting - wait for it.",
                            severity="warning", timeout=4, markup=False)
                return
            self._start_run("custom", task)

        async def on_composer_submitted(self, event) -> None:
            """The composer asked to send its text."""
            self.action_submit_task()

        async def on_button_pressed(self, event):
            bid = getattr(event.button, "id", "")
            if bid and bid.startswith("slash-"):
                action = bid[len("slash-"):]
                try:
                    self.query_one("#composer", Composer).clear()
                except Exception:
                    pass
                self._close_slash_menu()
                try:
                    self.query_one("#composer", Composer).focus()
                except Exception:
                    pass
                self._command_chosen("cmd-" + action)
                return
            if event.button.id != "btn-send":
                return
            state = self._run_state()
            if state == "running":
                # The agent is working: the button stops it.
                self._stop_run()
            elif state == "stopping":
                self.notify("The agent is stopping…", severity="warning",
                            timeout=3, markup=False)
            else:
                self.action_submit_task()

        # -- starting a run --------------------------------------------------
        def _start_run(self, task_kind: str, prompt: str) -> None:
            """Preflight and launch. The start lock keeps one run at a time."""
            if self._start_locked:
                self.notify("A run is already starting - wait for it.",
                            severity="warning", timeout=4, markup=False)
                return
            self._start_locked = True
            self.run_worker(lambda: self._start_run_worker(task_kind, prompt), thread=True)

        def _start_run_worker(self, task_kind: str, prompt: str) -> None:
            self._busy_inc()
            self._launcher_result = {"settled": False}
            try:
                self._start_run_body(task_kind, prompt)
            except Exception as error:
                self.call_from_thread(self._say, transcript_mod.ERROR,
                                      "Start failed: %s" % error)
                self.call_from_thread(self._settle_launch)

        def _start_run_body(self, task_kind: str, prompt: str) -> None:
            settings = dict(self.settings)
            effective, problems = flows_mod.build_new_run(task_kind, prompt, settings, cfg)
            gate = flows_mod.service_gate(settings, env_mod, services_mod)
            offer = flows_mod.temporary_context_offer(settings, gate)
            if offer is not None:
                try:
                    want = cfg.example_config().get("contextSize", 16384)
                except Exception:
                    want = 16384
                self.call_from_thread(self._say, transcript_mod.NOTICE,
                                      "Starting a temporary Ollama (context %s)..." % want)
                try:
                    started = services_mod.start_temporary_ollama(settings, want)
                except Exception as error:
                    started = {"ok": False, "error": str(error)}
                if started.get("ok"):
                    effective["endpoint"] = started["endpoint"]
                    gate = flows_mod.service_gate(dict(settings, endpoint=started["endpoint"]),
                                                  env_mod, services_mod)
                    self.call_from_thread(
                        self._say, transcript_mod.NOTICE,
                        "Temporary inference for this run only: %s (stops on TUI exit)."
                        % started["endpoint"])
                else:
                    self.call_from_thread(
                        self._say, transcript_mod.ERROR,
                        "Temporary Ollama failed: %s. Fix your Ollama manually."
                        % started.get("error"))
                    self.call_from_thread(self._settle_launch)
                    return
            problems = list(problems) + list(gate)
            if problems:
                self.call_from_thread(
                    self._say, transcript_mod.ERROR,
                    "Blocked:\n" + "\n".join(
                        "- %s" % (p if isinstance(p, str) else p.get("name"))
                        for p in problems[:8]))
                self.call_from_thread(self._settle_launch)
                return
            config_path = flows_mod.write_config(effective, task_kind, cfg)
            self.call_from_thread(self._say, transcript_mod.NOTICE,
                                  "Launching %s..." % config_path.name)
            thread = threading.Thread(target=self._launch_blocking,
                                      args=(str(config_path),), daemon=True)
            self._launcher_thread = thread
            thread.start()

        def _settle_launch(self) -> None:
            """Release the Start lock + busy spinner exactly once per launch."""
            if self._launcher_result.get("settled"):
                return
            self._launcher_result["settled"] = True
            self._start_locked = False
            self._busy_dec()

        def _launch_blocking(self, config_path: str):
            def on_line(line: str):
                try:
                    payload = json.loads(line)
                except ValueError:
                    return
                if payload.get("event") == "run_started" and payload.get("directory"):
                    self._launcher_result["directory"] = payload["directory"]
                    self._launcher_result["run_id"] = payload.get("runId")
                    self.call_from_thread(self._enter_live_from_launcher)
            result = None
            try:
                result = runner_mod.launch(Path(config_path), on_line=on_line)
                self._launcher_result["exit_code"] = result.exit_code
                if not self._launcher_result.get("directory") and result.directory:
                    self._launcher_result["directory"] = str(result.directory)
                    self._launcher_result["run_id"] = result.run_id
                    self.call_from_thread(self._enter_live_from_launcher)
            except Exception as error:
                self._launcher_result["error"] = str(error)
                self.call_from_thread(self._say, transcript_mod.ERROR,
                                      "Launch failed: %s" % error)
            finally:
                if not self._launcher_result.get("directory") and not self._launcher_result.get("error"):
                    tail = (result.output_tail if result else "")[-1500:]
                    self.call_from_thread(
                        self._say, transcript_mod.ERROR,
                        "Launch ended without reserving a run (config/duplicate/reservation). "
                        "No evidence touched.\n%s" % tail)
                self.call_from_thread(self._settle_launch)

        def _enter_live_from_launcher(self):
            directory = self._launcher_result.get("directory")
            if not directory:
                return
            self.start_live(directory, self._launcher_result.get("run_id"))
            self._start_poll()
            self._settle_launch()

    def main() -> int:
        """Crash-safe entry: no model call, non-TTY exits 2, hard stop on close."""
        import sys as _sys
        if not _sys.stdin.isatty():
            print("Volume Agent TUI needs an interactive terminal.")
            print("Run .\\Start-VolumeTUI.ps1 in Windows Terminal or PowerShell.")
            return 2
        settings = cfg.load_settings()
        _install_termination_handlers()
        try:
            # Inside the try: a Ctrl+C during the service auto-start must
            # still run the shutdown below, or the services it just started
            # would survive.
            start_owned_services(settings)
            app = VolumeAgentApp()
            app.run()
            return 0
        except KeyboardInterrupt:
            print("\nInterrupted - stopping agent, local Ollama and owned services...")
            return 130
        finally:
            # Every exit path (quit, hard stop, crash, closed window) leaves no
            # process behind: the agent tree, the local Ollama and the services
            # this TUI started are stopped here.
            try:
                report = services_mod.shutdown_all(hard=True)
                for line in services_mod.shutdown_lines(report):
                    print(line)
            except Exception as error:
                print("Shutdown problem: %s" % error)

    def _install_termination_handlers() -> None:
        """Turn SIGTERM/SIGBREAK into SystemExit so the cleanup still runs."""
        import signal as _signal

        def _handler(signum, frame):  # noqa: ARG001 - signal handler
            raise SystemExit(128 + int(signum))

        for name in ("SIGTERM", "SIGBREAK", "SIGHUP"):
            number = getattr(_signal, name, None)
            if number is None:
                continue
            try:
                _signal.signal(number, _handler)
            except (ValueError, OSError, AttributeError):
                continue

else:  # textual missing: importable stubs for unit tests
    class VolumeAgentApp:  # type: ignore
        def __init__(self, *args, **kwargs):
            raise RuntimeError("Textual is not installed; run Start-VolumeTUI.ps1 to bootstrap runtime/tui-venv.")

    def main() -> int:  # type: ignore
        print("Textual is not installed. Run .\\Start-VolumeTUI.ps1 to create runtime/tui-venv.")
        return 2
