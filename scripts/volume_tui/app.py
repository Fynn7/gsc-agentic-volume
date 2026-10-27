"""Coding-agent style TUI for the Volume Agent.

One screen, like a coding agent app: a status line, the conversation with the
run (the task, the model's thinking, its tool calls, the frames it rendered and
its answer) and a composer that starts the next task.

Control/display only: runs start via scripts/run_local_agent.py, evidence is
read-only except new runs, verification.json, tui-settings.json,
composer-history.json and chat-log exports.
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
    import export_images as export_images_mod
    import flows as flows_mod
    import imaging as imaging_mod
    import telemetry as telemetry_mod
    import batches as batches_mod
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
    export_images_mod = None  # type: ignore
    flows_mod = None  # type: ignore
    imaging_mod = None  # type: ignore
    telemetry_mod = None  # type: ignore
    batches_mod = None  # type: ignore

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
    from textual.suggester import Suggester
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
)

# Preview budget: a frame in the conversation is fitted into this many text
# rows and this many columns, so the whole picture is visible at once (never
# a cut-off corner) and never swallows the conversation.
IMAGE_PREVIEW_MAX_ROWS = 14
IMAGE_PREVIEW_MAX_COLS = 64

# Composer history (shell-like Up/Down) survives TUI restarts: every submitted
# task is appended here, so killing the TUI and reopening it still recalls
# the last input with Up. Lives under runtime/tui/ with the other TUI-owned
# state (services.json); the run folders stay read-only.
COMPOSER_HISTORY_LIMIT = 100
COMPOSER_HISTORY_FILE = ROOT / "runtime" / "tui" / "composer-history.json"


def composer_history_path(path=None) -> Path:
    """Where the cross-restart composer history lives (patchable in tests)."""
    if path is not None:
        return Path(path)
    return Path(COMPOSER_HISTORY_FILE)


def load_composer_history(path=None) -> list[str]:
    """Read the persisted composer history; missing/corrupt means empty."""
    target = composer_history_path(path)
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if isinstance(raw, dict):
        raw = raw.get("history", [])
    if not isinstance(raw, list):
        return []
    out = [str(item) for item in raw if isinstance(item, str) and str(item).strip()]
    if len(out) > COMPOSER_HISTORY_LIMIT:
        del out[:-COMPOSER_HISTORY_LIMIT]
    return out


def save_composer_history(history: list[str], path=None) -> bool:
    """Persist the composer history atomically; never raises."""
    target = composer_history_path(path)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        cleaned = [str(item) for item in (history or []) if isinstance(item, str)]
        if len(cleaned) > COMPOSER_HISTORY_LIMIT:
            del cleaned[:-COMPOSER_HISTORY_LIMIT]
        tmp = target.with_name(target.name + ".tmp")
        tmp.write_text(json.dumps(cleaned, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(target)
        return True
    except (OSError, ValueError):
        return False

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
    if kind == transcript_mod.COMPACTION:
        return render_compaction(block, False)
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


def render_compaction(block: dict, expanded: bool = False):
    """The context checkpoint: one line that opens into the summary.

    The checkpoint is the model's own words, but it is housekeeping, not an
    answer — so it gets its own row (◈), never a "»" line. Closed it shows
    the arrow only; a click opens the summary the model wrote when its
    context filled up.
    """
    from rich.text import Text

    text = Text()
    body = str(block.get("body") or "")
    running = block.get("status") == "running"
    for part, role in transcript_mod.compaction_row_parts(block):
        if role == "spin":
            text.append(part, style="magenta")
        elif role == "ok":
            text.append(part, style="green")
        elif role == "words":
            text.append(part, style="italic dim" if running else "dim")
        else:
            text.append(part, style="dim")
    if not expanded:
        if body:
            text.append("  ▸", style="dim")
        return text
    text.append("  ▾", style="dim")
    if body:
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
                    "  Up/Down        recall the previous/next input (like a shell)\n"
                    "  Ctrl+Up/Down   scroll the conversation (the composer keeps the cursor)\n"
                    "  Esc            stop the running agent (evidence already written stays)\n"
                    "  /              slash commands with a menu (like opencode: type\n"
                    "                 / to list them, Enter runs the match)\n"
                    "  F1             this help\n"
                    "  F2             the glossary: what the words of the screen mean\n"
                    "  Ctrl+H         the technical rows (model calls, HTTP status)\n"
                    "  Ctrl+T         light / dark\n"
                    "  Ctrl+Q         quit (asks first while a run is live)\n"
                    "  Ctrl+C         copy the selected text (shows 拷贝成功);\n"
                    "                 with nothing selected it copies the latest toast\n"
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
                    "  ◈ Context checkpoint the model's summary when its context\n"
                    "                 filled up; click it to read it\n"
                    "  ✳ Working       a model turn that did not reason (Think is off)\n"
                    "  ✓ Model answered the turn is over, there is nothing to open\n"
                    "  ● <step>       what the agent is doing (past tense once\n"
                    "                 it returned), then what went in, what came\n"
                    "                 out and what changed; ✓ when it returned,\n"
                    "                 ✗ when the renderer refused it\n"
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
            yield Static("Enter = Yes (quit now) · Esc = No (cancel)", id="confirm-hint")
            with Horizontal():
                yield Button("Yes", id="confirm-yes", variant="error",
                             tooltip="Do the action now. Esc is the same as No.")
                yield Button("No", id="confirm-no", variant="primary",
                             tooltip="Do nothing and go back. Esc does the same.")

    def on_mount(self) -> None:
        """Ctrl+Q lands on Yes: Enter quits immediately, Esc still cancels."""
        try:
            self.query_one("#confirm-yes", Button).focus()
        except Exception:
            pass

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
        ("New: clear the conversation and start fresh", "new",
         "Clear the open run and refresh, ready for the next task. "
         "Never stops a live run - stop it first (Esc)."),
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
         "Download the conversation of this run to your Downloads folder as "
         "a .log file and open the folder (the run folder stays read-only)."),
        ("Live: export all run images (zip)", "live-export-images",
         "Zip every frame PNG of this run to your Downloads folder as "
         "a .zip file and open the folder (the run folder stays read-only)."),
        ("Live: view the current PNG", "live-open-png",
         "Open the previewed PNG in your image viewer (full size)."),
        ("Live: open the PNG folder", "live-open",
         "Open the file explorer at the folder that holds the PNGs."),
        ("Runs: open another run", "runs-open",
         "Pick one of the runs on this machine and open it here."),
        ("History: batches and runs", "history",
         "Batches and runs on this machine; a batch opens its detail first, "
         "then a member opens its own chat."),
        ("Batch: run the same task N times", "batch",
         "Repeat one task N times with the same frozen config; each member "
         "keeps its own run folder and chat."),
        ("Runs: archive all runs", "runs-archive-all",
         "Move every history run to runtime/archive (live runs are kept). "
         "No unarchive: move the folders back manually to restore them."),
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
    "new": "/new",
    "live-verify": "/verify",
    "live-copy": "/copy-json",
    "live-copy-chat": "/copy-chat",
    "live-export-chat": "/export-chat",
    "live-export-images": "/export-images",
    "live-open-png": "/open-image",
    "live-open": "/open-folder",
    "history": "/history",
    "batch": "/batch",
    "runs-archive-all": "/archive-all",
    "volume-switch": "/change-volume",
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


class _ModelSuggester(Suggester if _TEXTUAL else object):
    """Inline completion for the model field from this machine's Ollama.

    Prefix match first (``qwen`` -> ``qwen3.6:35b``), then substring
    (``35b`` -> ``qwen3.6:35b``); case-insensitive. The list comes from
    ``/api/tags`` (see ``SettingsScreen._refresh_models``), never typed
    by hand. Empty input suggests nothing.
    """

    def __init__(self, suggestions=()) -> None:
        try:
            super().__init__(case_sensitive=False)
        except Exception:
            pass
        self._suggestions: list[str] = []
        self.update(suggestions)

    def update(self, suggestions) -> None:
        seen: set[str] = set()
        ordered: list[str] = []
        for name in suggestions or []:
            text = str(name or "").strip()
            if text and text not in seen:
                seen.add(text)
                ordered.append(text)
        self._suggestions = sorted(ordered)

    @property
    def suggestions(self) -> list[str]:
        return list(self._suggestions)

    async def get_suggestion(self, value: str) -> str | None:
        needle = str(value or "").casefold()
        if not needle:
            return None
        for name in self._suggestions:
            if name.casefold().startswith(needle):
                return name
        for name in self._suggestions:
            if needle in name.casefold():
                return name
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
            yield Button("Archive all (move history to runtime/archive)",
                         id="runs-archive-all", variant="warning",
                         tooltip="Move every history run to runtime/archive. "
                                 "Live runs are kept. No unarchive: move the "
                                 "folders back manually to restore them.")

    def on_mount(self) -> None:
        table = self.query_one("#runs-modal-table", DataTable)
        table.add_columns("run", "status", "model", "frames", "task")
        for run in self._runs:
            table.add_row(_escape_markup(str(run.get("runId", "?"))),
                          _escape_markup(str(run.get("status", "?"))),
                          _escape_markup(str(run.get("model", "?"))),
                          str(run.get("frames", "?")),
                          _escape_markup(str(run.get("task", "?"))[:60]))
        table.focus()

    async def on_button_pressed(self, event):
        if event.button.id == "runs-archive-all":
            self.dismiss(result={"__archive_all__": True})
            return

    async def on_data_table_row_selected(self, event):
        row = event.cursor_row
        if 0 <= row < len(self._runs):
            self.dismiss(result=self._runs[row])


def batch_form_problems(count_raw, task_text: str, settings: dict) -> list[str]:
    """Validate the /batch form: positive-int N, required task, valid settings."""
    problems: list[str] = []
    try:
        from batches import parse_count as _parse_count
    except Exception:
        _parse_count = None  # type: ignore
    try:
        if batches_mod is not None and hasattr(batches_mod, "validate_batch"):
            problems.extend(batches_mod.validate_batch(count_raw, task_text))
        elif _parse_count is not None:
            _parse_count(count_raw)
            if not str(task_text or "").strip():
                problems.append("task must not be empty")
        else:
            count = int(str(count_raw or "").strip())
            if count < 1:
                problems.append("count must be a positive integer")
            if not str(task_text or "").strip():
                problems.append("task must not be empty")
    except (ValueError, TypeError) as error:
        problems.append(str(error))
    try:
        if cfg is not None and isinstance(settings, dict):
            problems.extend(cfg.validate_settings(settings))
    except Exception:
        pass
    return problems


class BatchScreen(DismissibleModal):
    """One-shot batch form: repeat count + task + frozen run config.

    Settings fields are prefilled with a COPY of current Settings; submitting
    never writes runtime/tui-settings.json. Task is prefilled with the open
    run's task, or empty when no run is open.
    """

    def __init__(self, settings: dict, task_prefill: str = ""):
        super().__init__()
        self.settings = dict(settings or {})
        self.task_prefill = str(task_prefill or "")

    def compose(self):
        with Vertical(classes="dlg"):
            with VerticalScroll():
                with Vertical(classes="panel"):
                    yield Static("Batch: same frozen config runs N times "
                                 "(config is frozen at start, settings file untouched)",
                                 classes="scroll-hint")
                    with Horizontal(classes="row"):
                        yield Label("Repeat count N", classes="field-label")
                        yield Input("", id="batch-count",
                                    placeholder="positive integer, e.g. 3")
                    with Horizontal(classes="row"):
                        yield Label("Task", classes="field-label")
                        yield Input(self.task_prefill, id="batch-task",
                                    placeholder="required task text")
                    for label, wid, key, _kind in SETTINGS_ROWS:
                        with Horizontal(classes="row"):
                            yield Label(label, classes="field-label")
                            yield Input(str(self.settings.get(key, "")),
                                        id="batch-" + wid)
                    with Horizontal(classes="row"):
                        yield Label("Think", classes="field-label")
                        yield Select([("off", "false"), ("on", "true")],
                                     value="true" if self.settings.get("think") else "false",
                                     id="batch-set-think")
                    with Horizontal(classes="row"):
                        yield Label("Debug windows", classes="field-label")
                        yield Select([("off (headless)", "false"), ("on (show popups)", "true")],
                                     value="true" if self.settings.get("debugWindows") else "false",
                                     id="batch-set-debug-windows")
                    with Horizontal(classes="row"):
                        yield Button("Start batch", id="btn-batch-start", variant="primary",
                                     tooltip="Freeze this config and run it N times in order.")
                        yield Button("Cancel", id="btn-batch-cancel",
                                     tooltip="Close without starting anything.")
                    yield Static("", id="batch-status", markup=False)

    def _collect_settings(self) -> dict:
        settings = dict(self.settings)
        for _label, widget, key, kind in SETTINGS_ROWS:
            try:
                raw = self.query_one("#batch-" + widget, Input).value.strip()
            except Exception:
                continue
            try:
                if kind == "int":
                    if raw:
                        settings[key] = int(raw)
                elif kind == "float":
                    if raw:
                        settings[key] = float(raw)
                else:
                    settings[key] = raw
            except ValueError:
                # Keep raw so validation reports it; int()/float() failure
                # surfaces via cfg.validate_settings or batch validation.
                settings[key] = raw
        try:
            settings["think"] = str(self.query_one("#batch-set-think", Select).value) == "true"
        except Exception:
            pass
        try:
            settings["debugWindows"] = str(self.query_one("#batch-set-debug-windows", Select).value) == "true"
        except Exception:
            pass
        return settings

    def _collect(self) -> tuple:
        try:
            count_raw = self.query_one("#batch-count", Input).value
        except Exception:
            count_raw = ""
        try:
            task = self.query_one("#batch-task", Input).value
        except Exception:
            task = ""
        return count_raw, str(task or ""), self._collect_settings()

    def _status(self, text: str) -> None:
        try:
            self.query_one("#batch-status", Static).update(text)
        except Exception:
            pass

    async def on_button_pressed(self, event):
        bid = event.button.id
        if bid == "btn-batch-cancel":
            self.dismiss(result=None)
            return
        if bid == "btn-batch-start":
            count_raw, task, settings = self._collect()
            problems = batch_form_problems(count_raw, task, settings)
            if problems:
                self._status("Invalid:\n" + "\n".join("- %s" % p for p in problems))
                return
            try:
                from batches import parse_count as _parse
                count = _parse(count_raw)
            except Exception:
                count = int(str(count_raw).strip())
            # Copy only: the caller must not persist these settings.
            self.dismiss(result={"count": count, "task": task.strip(),
                                 "settings": dict(settings)})


class HistoryScreen(DismissibleModal):
    """Batches / Runs tabs. A batch opens its detail first, then a member."""

    def __init__(self, batches: list[dict], runs: list[dict]):
        super().__init__()
        self._batches = list(batches or [])
        self._runs = list(runs or [])
        self._tab = "batches"

    def compose(self):
        with Vertical(classes="dlg"):
            yield Static("History: batches and runs (Enter opens, Esc closes)",
                         id="history-title")
            with Horizontal():
                yield Button("Batches", id="hist-tab-batches", variant="primary",
                             tooltip="Batch records in runtime/tui/batches.")
                yield Button("Runs", id="hist-tab-runs",
                             tooltip="Every single run, including batch members.")
            yield DataTable(id="hist-batches-table", cursor_type="row")
            yield DataTable(id="hist-runs-table", cursor_type="row")
            yield Button("Archive all (runs only; batches stay)",
                         id="hist-archive-all", variant="warning",
                         tooltip="Move every history run to runtime/archive. "
                                 "Batch records stay in Batches history.")

    def on_mount(self) -> None:
        batches_table = self.query_one("#hist-batches-table", DataTable)
        batches_table.add_columns("batch", "status", "planned", "task")
        for batch in self._batches:
            config = batch.get("config") or {}
            batches_table.add_row(
                _escape_markup(str(batch.get("batchId", "?"))),
                _escape_markup(str(batch.get("status", "?"))),
                str(batch.get("planned", "?")),
                _escape_markup(str(config.get("task", ""))[:60]))
        runs_table = self.query_one("#hist-runs-table", DataTable)
        runs_table.add_columns("run", "status", "model", "frames", "task")
        for run in self._runs:
            runs_table.add_row(_escape_markup(str(run.get("runId", "?"))),
                               _escape_markup(str(run.get("status", "?"))),
                               _escape_markup(str(run.get("model", "?"))),
                               str(run.get("frames", "?")),
                               _escape_markup(str(run.get("task", "?"))[:60]))
        self._show_tab(self._tab)
        try:
            batches_table.focus()
        except Exception:
            pass

    def _show_tab(self, tab: str) -> None:
        self._tab = tab if tab in ("batches", "runs") else "batches"
        try:
            self.query_one("#hist-batches-table", DataTable).display = (self._tab == "batches")
        except Exception:
            pass
        try:
            self.query_one("#hist-runs-table", DataTable).display = (self._tab == "runs")
        except Exception:
            pass
        for bid, active in (("hist-tab-batches", self._tab == "batches"),
                            ("hist-tab-runs", self._tab == "runs")):
            try:
                self.query_one("#" + bid, Button).variant = ("primary" if active else "default")
            except Exception:
                pass

    async def on_button_pressed(self, event):
        bid = event.button.id
        if bid == "hist-tab-batches":
            self._show_tab("batches")
            return
        if bid == "hist-tab-runs":
            self._show_tab("runs")
            return
        if bid == "hist-archive-all":
            self.dismiss(result={"__archive_all__": True})
            return

    async def on_data_table_row_selected(self, event):
        table = getattr(event, "data_table", None)
        table_id = ""
        try:
            table_id = str(getattr(table, "id", "") or "")
        except Exception:
            table_id = ""
        row = event.cursor_row
        # Fall back to the visible tab when the event lacks a table ref.
        if not table_id:
            table_id = "hist-batches-table" if self._tab == "batches" else "hist-runs-table"
        if table_id == "hist-batches-table":
            if 0 <= row < len(self._batches):
                self.dismiss(result={"__batch__": self._batches[row]})
            return
        if 0 <= row < len(self._runs):
            self.dismiss(result=self._runs[row])


class BatchDetailScreen(DismissibleModal):
    """One batch: config summary + per-item results; a member opens its chat."""

    def __init__(self, batch: dict):
        super().__init__()
        self._batch = dict(batch or {})

    def compose(self):
        with Vertical(classes="dlg"):
            yield Static("Batch %s (Enter opens a member, Esc back)" % str(self._batch.get("batchId", "?")),
                         id="batch-detail-title")
            yield Static("\n".join(self._summary_lines()) or "(no detail)",
                         id="batch-detail-summary", markup=False)
            yield DataTable(id="batch-detail-table", cursor_type="row")

    def _summary_lines(self) -> list[str]:
        try:
            if batches_mod is not None and hasattr(batches_mod, "batch_summary_text"):
                return list(batches_mod.batch_summary_text(self._batch))
        except Exception:
            pass
        config = self._batch.get("config") or {}
        lines = ["batch=%s status=%s planned=%s" % (
            self._batch.get("batchId", "?"), self._batch.get("status", "?"),
            self._batch.get("planned", "?")),
            "task=%s" % str(config.get("task", ""))[:160]]
        for item in self._batch.get("items") or []:
            lines.append("#%s %s runId=%s" % (
                item.get("seq", "?"), item.get("status", "?"), item.get("runId", "-")))
        return lines

    def on_mount(self) -> None:
        table = self.query_one("#batch-detail-table", DataTable)
        table.add_columns("seq", "status", "run", "seconds", "error")
        for item in self._batch.get("items") or []:
            table.add_row(str(item.get("seq", "?")),
                          _escape_markup(str(item.get("status", "?"))),
                          _escape_markup(str(item.get("runId") or "-")),
                          str(item.get("seconds", "-")),
                          _escape_markup(str(item.get("error") or "")[:60]))
        table.focus()

    async def on_data_table_row_selected(self, event):
        row = event.cursor_row
        items = list(self._batch.get("items") or [])
        if 0 <= row < len(items):
            item = items[row]
            if item.get("runId"):
                self.dismiss(result={"__batch_member__": item,
                                     "__batch__": self._batch})
                return
        # Items without a run (skipped) have no chat to open; stay open.


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
        table.focus()

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
        self._model_suggester = _ModelSuggester()

    def compose(self):
        with Vertical(classes="dlg"):
            with VerticalScroll():
                with Vertical(classes="panel"):
                    yield Static("Saved to runtime/tui-settings.json (Save settings writes it)",
                                 classes="scroll-hint")
                    for label, wid, key, _kind in SETTINGS_ROWS:
                        with Horizontal(classes="row"):
                            yield Label(label, classes="field-label")
                            if key == "model":
                                yield Input(str(self.settings.get(key, "")), id=wid,
                                            suggester=self._model_suggester,
                                            tooltip="Tab/Right completes from the models on this machine "
                                                    "(Refresh models re-reads them).")
                            else:
                                yield Input(str(self.settings.get(key, "")), id=wid)
                        if key == "model":
                            with Horizontal(classes="row", id="model-hint-row"):
                                yield Label("", classes="field-label")
                                yield Static("", id="model-hint", markup=False)
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
                        yield Label("Debug windows", classes="field-label")
                        yield Select([("off (headless)", "false"), ("on (show popups)", "true")],
                                     value="true" if self.settings.get("debugWindows") else "false",
                                     id="set-debug-windows",
                                     tooltip="Off hides console/GLFW popups (default). "
                                             "On keeps the old visible behaviour for debugging.")
                    with Horizontal(classes="row"):
                        yield Button("Save settings", id="btn-settings-save", variant="primary",
                                     tooltip="Write these settings to runtime/tui-settings.json.")
                        yield Button("Reset to defaults", id="btn-settings-reset",
                                     tooltip="Fill the form with the built-in defaults. Nothing is written yet.")
                        yield Button("Test H200 endpoint", id="btn-h200-probe",
                                     tooltip="Send one small probe request to the H200 endpoint. "
                                             "Never falls back silently.")
                        yield Button("Refresh models", id="btn-models-refresh",
                                     tooltip="Re-read /api/tags from the endpoints above, "
                                             "so Tab completion knows this machine's models.")
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
            settings["debugWindows"] = str(self.query_one("#set-debug-windows", Select).value) == "true"
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
        elif bid == "btn-models-refresh":
            self._refresh_models()

    def on_mount(self) -> None:
        self._refresh_models()

    def on_input_changed(self, event) -> None:
        try:
            wid = getattr(getattr(event, "input", None), "id", "")
        except Exception:
            return
        if wid in ("set-endpoint", "set-h200"):
            self._models_hint("endpoint 已修改 — 按 Refresh models 重新读取本机模型")

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
            self.query_one("#set-debug-windows", Select).value = (
                "true" if defaults.get("debugWindows") else "false")
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
        self._models_hint("已填入默认值(未保存) — 按 Refresh models 重新读取本机模型")

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

    # -- model name completion -----------------------------------------
    def _model_endpoints(self) -> list[str]:
        """Both loopback endpoints from the form (mode-agnostic merge)."""
        endpoints: list[str] = []
        for wid, key in (("set-endpoint", "endpoint"), ("set-h200", "h200Endpoint")):
            try:
                raw = str(self.query_one("#" + wid, Input).value or "").strip()
            except Exception:
                raw = str(self.settings.get(key, "") or "").strip()
            if raw and raw not in endpoints:
                endpoints.append(raw)
        return endpoints

    def _models_hint(self, text: str) -> None:
        try:
            self.query_one("#model-hint", Static).update(text)
        except Exception:
            pass

    def _refresh_models(self) -> None:
        """Re-read /api/tags in a worker: the screen must not freeze on it."""
        endpoints = self._model_endpoints()
        try:
            app = self.app
        except Exception:
            return
        self._models_hint("正在读取本机模型…")

        def _work() -> None:
            names: list[str] = []
            for endpoint in endpoints:
                try:
                    if services_mod is not None and hasattr(services_mod, "list_models"):
                        found = services_mod.list_models(endpoint, timeout=3.0)
                    else:
                        found = []
                except Exception:
                    found = []
                for name in found or []:
                    if name not in names:
                        names.append(name)
            try:
                app.call_from_thread(self._models_done, sorted(names))
            except Exception:
                pass

        try:
            app._run_guarded("Reading local models", _work)
        except Exception:
            pass

    def _models_done(self, names) -> None:
        """The tags answered: Tab completion knows this machine's models."""
        try:
            self._model_suggester.update(names or [])
        except Exception:
            pass
        names = list(names or [])
        if names:
            shown = ", ".join(names[:8])
            extra = "" if len(names) <= 8 else " …(共%d)" % len(names)
            self._models_hint("本机模型(%d): %s%s — 输入时按 Tab 补全" % (len(names), shown, extra))
        else:
            self._models_hint("本机暂无可用模型(ollama 未启动或无模型) — 可手动输入")


class TranscriptScroll(VerticalScroll if _TEXTUAL else object):
    """The conversation scroller.

    It tells the app when the view moves, so a user who scrolls up to read
    the history is not yanked back to the newest entry.

    Never focusable: the TextArea draws its solid block cursor only while
    focused, so any click that moved focus here would hide the composer's
    cursor. Scrolling (mouse wheel, Ctrl+Up/Down) works unfocused.
    """

    if _TEXTUAL:
        can_focus = False

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

    if _TEXTUAL:
        can_focus = False


class FramePreview(SafeSelection, Static if _TEXTUAL else object):
    """Preview pane for a frame in the conversation.

    The frame is drawn as half-block cells whose width must match the widget
    exactly, otherwise the terminal folds the row and the picture is cut in
    half. The widget therefore asks the app to re-fit the frame whenever its
    own size changes (window resize, scrollbar appearing).

    Never focusable: clicks here must not steal the composer's cursor.
    """

    if _TEXTUAL:
        can_focus = False

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
    (the answer arrives word by word). Never focusable: clicks here must
    not steal the composer's cursor.
    """

    if _TEXTUAL:
        can_focus = False

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
    chain grows while the model works - open or closed. Never focusable:
    clicks here must not steal the composer's cursor.
    """

    if _TEXTUAL:
        can_focus = False

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


class CompactionBlock(SafeSelection, Static if _TEXTUAL else object):
    """The context checkpoint: one line, a click opens the summary.

    The same foldable behaviour as the Thinking block: the summary grows
    while the model writes it, open or closed. Never focusable: clicks here
    must not steal the composer's cursor.
    """

    if _TEXTUAL:
        can_focus = False

    def __init__(self, block: dict):
        super().__init__()
        self.block = block
        self.expanded = False
        self.add_class("block")
        self.add_class("compaction")

    def set_block(self, block: dict) -> None:
        self.block = block
        self.update(render_compaction(block, self.expanded))

    def on_click(self, event=None) -> None:
        """A click opens the summary, a second click closes it."""
        if not self._can_open():
            return
        self.expanded = not self.expanded
        self.update(render_compaction(self.block, self.expanded))
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
        self.update(render_compaction(self.block, self.expanded))

    def _can_open(self) -> bool:
        """Only a written summary opens: without it the line is a plain row."""
        return bool(str((self.block.get("body") or "")).strip())


class ImageBlock(Vertical if _TEXTUAL else object):
    """A frame in the conversation: the caption, the path and the picture."""

    if _TEXTUAL:
        can_focus = False

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


COMPOSER_MIN_ROWS = 1
COMPOSER_MAX_ROWS = 10


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
            try:
                self.show_vertical_scrollbar = False
            except Exception:
                pass

        def _composer_visual_rows(self) -> tuple[int, int]:
            """(capped_rows, total_rows) for the current text with wrapping."""
            try:
                text = str(self.text or "")
            except Exception:
                return COMPOSER_MIN_ROWS, COMPOSER_MIN_ROWS
            lines = text.split("\n") if text else [""]
            try:
                width = int(self.size.width) - 2
            except Exception:
                width = 0
            if not width or width < 4:
                total = len(lines) or 1
            else:
                total = 0
                for line in lines:
                    try:
                        cells = len(line)
                    except Exception:
                        cells = 1
                    total += max(1, -(-cells // width))
                    if total > 256:
                        break
                total = max(1, total)
            capped = min(max(total, COMPOSER_MIN_ROWS), COMPOSER_MAX_ROWS)
            return capped, total

        def _fit_composer(self) -> None:
            """Grow the input with content, scroll past 10 rows."""
            capped, total = self._composer_visual_rows()
            try:
                wanted = capped + 2
                if int(self.styles.height.value or 0) != wanted:
                    self.styles.height = wanted
            except Exception:
                try:
                    self.styles.height = capped + 2
                except Exception:
                    pass
            try:
                self.show_vertical_scrollbar = total > COMPOSER_MAX_ROWS
            except Exception:
                pass

        def on_mount(self) -> None:
            try:
                self.call_after_refresh(self._fit_composer)
            except Exception:
                pass

        def on_resize(self, event) -> None:
            try:
                self._fit_composer()
            except Exception:
                pass

        def on_text_area_changed(self, event) -> None:
            try:
                self._fit_composer()
            except Exception:
                pass

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
                    try:
                        handled = app._run_slash_selection()
                    except Exception:
                        handled = False
                    if handled:
                        event.stop()
                        event.prevent_default()
                        return
                    # No highlighted entry (e.g. no match): fall through to
                    # the normal submit so the composer reports the
                    # "Unknown command ..." notice instead of going quiet.
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
            if event.key in ("up", "down"):
                try:
                    handled = (app._history_prev() if event.key == "up"
                               else app._history_next())
                except Exception:
                    handled = False
                if handled:
                    event.stop()
                    event.prevent_default()
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
        # The solid block cursor lives in the composer (TextArea only draws
        # its cursor while focused): the app boots with it there and hands
        # focus straight back after any click on the main screen.
        AUTO_FOCUS = "#composer"
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
        #composer { height: auto; min-height: 3; max-height: 12; border: round $accent; }
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
        #volumes-modal-table { height: 20; }
        #batch-detail-summary { height: auto; }
        #batch-detail-table { height: 12; }
        #settings-status { height: auto; min-height: 3; padding: 1 2; }
        #model-hint-row { height: auto; }
        #model-hint { height: auto; color: $text-muted; }
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
            # Bare Ctrl+C (no text selected anywhere) copies the latest
            # toast: toasts dismiss on click, so their text can never be
            # mouse-selected. Screen/Input/TextArea copy actions all raise
            # SkipAction when there is no selection, which falls through to
            # this App-level binding.
            Binding("ctrl+c,super+c", "copy_last_toast", show=False),
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
            self._slash_touched = False
            self._slash_items: list[tuple[str, str, str]] = []
            # Composer history (shell-like): every submitted task, Up/Down recalls it.
            # Persisted to runtime/tui/composer-history.json, so Up still works
            # after the TUI is killed and restarted.
            self._composer_history: list[str] = load_composer_history()
            self._history_index: int | None = None
            self._history_draft: str = ""
            # One-shot batch: same frozen config runs N times in order.
            # Chat shows only the current member run; the batch marker is
            # painted in the status line and announced in the chat.
            self._batch: dict | None = None
            self._batch_cancel = threading.Event()
            self._batch_thread: threading.Thread | None = None
            # Latest toast text, so a bare Ctrl+C (no selectable text -
            # toasts dismiss on click) can still copy what was shown.
            self._last_toast_text = ""
            self._last_toast_at = 0.0

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
            self._focus_composer()

        def _focus_composer(self) -> None:
            """Keep the solid block cursor in the composer.

            TextArea only draws its cursor while focused, so every path
            that could move focus (clicks, buttons, modals, Esc) hands it
            back here on the main screen.
            """
            try:
                self.query_one("#composer", Composer).focus()
            except Exception:
                pass

        def _modal_closed(self, _result=None) -> None:
            """A dialog went away: the cursor belongs to the composer again."""
            try:
                self.call_after_refresh(self._focus_composer)
            except Exception:
                self._focus_composer()

        def on_click(self, event) -> None:
            """Any mouse click on the main screen keeps the composer's cursor.

            Buttons stop the click themselves (their Pressed handler
            refocuses afterwards); a modal owns focus while it is open.
            Everything else hands focus straight back after the click.
            """
            try:
                if isinstance(self.screen, ModalScreen):
                    return
            except Exception:
                pass
            try:
                self.call_after_refresh(self._focus_composer)
            except Exception:
                pass

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
            if kind == transcript_mod.COMPACTION:
                return CompactionBlock(block)
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
            # Execution order, not read order: a model call was sent, the
            # model thought, the tool ran, the reply came back. The merge
            # is by run-clock time, so the transport rows interleave with
            # the steps they carried instead of piling up at the end.
            self._ingest_events(trace_mod.sort_events(list(streamed) + list(fresh)))
            # A history run is read-only: anything that is not actually
            # running freezes the view, so an interrupted turn never keeps
            # spinning as "Working". The check runs after the ingest, so a
            # stale thinking_start replayed from disk is closed at once.
            if self._run_state() not in ("running", "stopping"):
                self.live_frozen = True
                self._close_thinking()
                self._announce_the_end()
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
            if block is not None and block.get("status") == "running":
                started = block.get("ts") or time.time()
                block["spinner"] = _spinner_frame()
                block["subtitle"] = ("for " + event_text_mod.format_duration(
                    max(0.0, time.time() - started)))
                self._refresh_block_widget(block)
            compaction = self.transcript.find(transcript_mod.COMPACTION)
            if compaction is not None and compaction.get("status") == "running":
                compaction["spinner"] = _spinner_frame()
                self._refresh_block_widget(compaction)

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
            end = transcript_mod.run_end_ts(self.transcript.blocks)
            for kind in (transcript_mod.THINKING, transcript_mod.COMPACTION):
                block = self.transcript.find(kind)
                if block is not None and block.get("status") == "running":
                    block["status"] = "done"
                    # The ticking ends with the run: a thinking row freezes
                    # its final duration instead of counting on.
                    if kind == transcript_mod.THINKING:
                        block["subtitle"] = transcript_mod._thinking_elapsed(block, now=end)
                    if isinstance(block.get("data"), dict):
                        block["data"]["open"] = False
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
            state = self._run_state() if self.live_run_id else ""
            if state and state not in ("running", "idle"):
                text.append("  ·  %s" % state, style="dim")
            batch_label = self._batch_label()
            if batch_label:
                text.append("  ·  %s" % batch_label, style="bold %s" % ACCENT)
            used, budget = self._budget()
            if budget:
                text.append("  ·  %s of %s observations used" % (used, budget),
                           style="dim")
                left = budget - used
                if left >= 0:
                    text.append("  ·  %s left" % left, style="dim")
            try:
                self.query_one("#status-bar", Static).update(text)
            except Exception:
                pass

        def _volume_name(self) -> str:
            """The selected volume's file name, as chosen in settings."""
            try:
                selected = cfg.selected_volume(self.settings) if cfg else None
            except Exception:
                selected = None
            if not selected:
                return ""
            return Path(selected).name

        def _run_state(self) -> str:
            """What the open run is doing.

            Read from the run's own run.json, so the line never claims that a
            stopped run is still running: running / stopping / stopped /
            finished / failed / unknown / idle.

            A run.json that still says "running" after its process is gone
            (interrupted / stale lock) is history, not live: it reports the
            recorded classification (unknown) so the conversation stays
            read-only instead of spinning as "Working".
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
                try:
                    manifest = json.loads(
                        (self.live_dir / "manifest.json").read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    manifest = None
                if not isinstance(manifest, dict):
                    manifest = None
                # A run killed by the old task timeout says "finished" in
                # run.json: report it as timed out, never as a clean finish.
                if manifest is not None and manifest.get("timedOut"):
                    return "timed out"
                status = str(record.get("status") or "running")
                if status != "running":
                    return status
                if self._stop_requested_for == self.live_run_id:
                    return "stopping"
                # Recorded "running": verify the process is really alive.
                # A dead pid means history (unknown), never a live agent.
                try:
                    if runs_mod is not None and runs_mod.classify(record, manifest) != "Running":
                        return "unknown"
                except Exception:
                    pass
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

        # -- batch (one-shot N repeats, same frozen config) -----------------
        def _batch_label(self) -> str:
            """Status-line marker: ◇ BATCH <current>/<N> (while running)."""
            batch = getattr(self, "_batch", None)
            if not isinstance(batch, dict) or not batch.get("batchId"):
                return ""
            if not bool(batch.get("running")):
                return ""
            try:
                total = int(batch.get("total") or 0)
            except (TypeError, ValueError):
                total = 0
            if total < 1:
                return ""
            try:
                current = int(batch.get("current") or 0)
            except (TypeError, ValueError):
                current = 0
            current = min(max(current, 1), total) if total else 0
            return "◇ BATCH %d/%d" % (current, total)

        def _batch_active(self) -> bool:
            batch = getattr(self, "_batch", None)
            if not isinstance(batch, dict):
                return False
            return bool(batch.get("running"))

        def _cancel_batch(self, reason: str = "cancelled") -> None:
            """Stop the queue: current member is stopped, rest are skipped."""
            try:
                self._batch_cancel.set()
            except Exception:
                pass
            batch = getattr(self, "_batch", None)
            if isinstance(batch, dict) and batch.get("running"):
                batch["running"] = False

        def _batch_task_prefill(self) -> str:
            """Current open run's task, or empty when no run is open."""
            try:
                if self.live_dir is not None:
                    task = self._task_text(self.live_dir)
                    if task.strip():
                        return task.strip()
            except Exception:
                pass
            return ""

        def _discover_batches(self) -> list[dict]:
            try:
                if batches_mod is not None and hasattr(batches_mod, "list_batches"):
                    return list(batches_mod.list_batches())
            except Exception:
                return []
            return []

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
            if result == "cmd-new":
                self._new_conversation()
            elif result == "cmd-live-stop":
                self._stop_run()
            elif result == "cmd-live-verify":
                self._verify_live()
            elif result == "cmd-live-copy":
                self._copy_run_json_log()
            elif result == "cmd-live-copy-chat":
                self._copy_chat_log()
            elif result == "cmd-live-export-chat":
                self._export_chat_log()
            elif result == "cmd-live-export-images":
                self._export_run_images()
            elif result == "cmd-live-open-png":
                self._open_live_png()
            elif result == "cmd-live-open":
                self._open_live_folder()
            elif result == "cmd-runs-open":
                self._open_history()
            elif result == "cmd-history":
                self._open_history()
            elif result == "cmd-batch":
                self._open_batch()
            elif result == "cmd-runs-archive-all":
                self._archive_all_runs_ask()
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
            self._slash_touched = False
            try:
                composer.focus()
            except Exception:
                pass
            self._refresh_slash_menu()

        def _close_slash_menu(self) -> None:
            self._slash_open = False
            self._slash_selected = 0
            self._slash_touched = False
            try:
                self.query_one("#slash-menu").remove_class("open")
            except Exception:
                pass

        def _history_push(self, text: str) -> None:
            """Remember a submitted composer text (shell-like history)."""
            text = str(text or "")
            if not text.strip():
                return
            history = getattr(self, "_composer_history", None)
            if history is None:
                history = self._composer_history = load_composer_history()
            if history and history[-1] == text:
                self._history_index = None
                self._history_draft = ""
                return
            history.append(text)
            if len(history) > COMPOSER_HISTORY_LIMIT:
                del history[:-COMPOSER_HISTORY_LIMIT]
            self._history_index = None
            self._history_draft = ""
            try:
                save_composer_history(history)
            except Exception:
                pass

        def _history_show(self, text: str) -> None:
            """Replace the composer text and park the cursor at the end."""
            try:
                composer = self.query_one("#composer", Composer)
            except Exception:
                return
            try:
                composer.text = text
            except Exception:
                return
            try:
                lines = str(text or "").splitlines() or [""]
                composer.move_cursor((len(lines) - 1, len(lines[-1])))
            except Exception:
                pass

        def _history_prev(self) -> bool:
            """Up recalls the previous input. True when the key is consumed."""
            history = list(getattr(self, "_composer_history", None) or [])
            if not history:
                return False
            try:
                composer = self.query_one("#composer", Composer)
            except Exception:
                return False
            try:
                if not bool(composer.cursor_at_first_line):
                    return False
            except Exception:
                pass
            index = getattr(self, "_history_index", None)
            if index is None:
                try:
                    self._history_draft = str(composer.text or "")
                except Exception:
                    self._history_draft = ""
                self._history_index = len(history) - 1
            elif index > 0:
                self._history_index = index - 1
            else:
                return True
            try:
                self._history_show(history[self._history_index])
            except Exception:
                pass
            return True

        def _history_next(self) -> bool:
            """Down moves toward newer inputs. True when the key is consumed."""
            history = list(getattr(self, "_composer_history", None) or [])
            index = getattr(self, "_history_index", None)
            if index is None:
                return False
            try:
                composer = self.query_one("#composer", Composer)
            except Exception:
                return False
            try:
                if not bool(composer.cursor_at_last_line):
                    return False
            except Exception:
                pass
            if index < len(history) - 1:
                self._history_index = index + 1
                try:
                    self._history_show(history[self._history_index])
                except Exception:
                    pass
            else:
                self._history_index = None
                try:
                    self._history_show(str(getattr(self, "_history_draft", "") or ""))
                except Exception:
                    pass
            return True

        def _slash_move(self, delta: int) -> None:
            if not self._slash_open or not self._slash_items:
                return
            self._slash_selected = (self._slash_selected + delta) % len(self._slash_items)
            self._slash_touched = True
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

        def _run_slash_selection(self) -> bool:
            """Run the highlighted slash menu entry. True when it ran."""
            if not getattr(self, "_slash_open", False):
                return False
            items = list(getattr(self, "_slash_items", []) or [])
            if not items:
                return False
            # Bare "/": the user is still browsing, not running - keep the
            # menu open instead of firing the first entry by accident.
            # Once the highlight was moved (Up/Down), Enter runs the
            # highlighted entry even though the text is still just "/".
            try:
                current = str(self.query_one("#composer", Composer).text or "")
            except Exception:
                current = ""
            stripped = current.lstrip()
            token = stripped.split(None, 1)[0] if stripped else ""
            if token == "/" and not getattr(self, "_slash_touched", False):
                return False
            try:
                selected = int(getattr(self, "_slash_selected", 0))
            except Exception:
                selected = 0
            if not (0 <= selected < len(items)):
                selected = 0
            try:
                action = items[selected][1]
            except Exception:
                return False
            parts = stripped.split(None, 1)
            arg = parts[1] if len(parts) > 1 else ""
            try:
                self.query_one("#composer", Composer).clear()
            except Exception:
                pass
            try:
                self._close_slash_menu()
            except Exception:
                pass
            try:
                if str(action) == "new":
                    self._new_conversation(arg)
                else:
                    self._command_chosen("cmd-" + str(action))
            except Exception:
                return False
            return True

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
            if action == "new":
                parts = str(text).lstrip().split(None, 1)
                self._new_conversation(parts[1] if len(parts) > 1 else "")
                return True
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
            self.push_screen(HelpScreen(), callback=self._modal_closed)

        def action_glossary(self) -> None:
            """F2: the words the screen uses, explained."""
            self.push_screen(GlossaryScreen(), callback=self._modal_closed)

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
            self._focus_composer()

        def action_quit(self) -> None:
            """Ctrl+Q asks first while a run is live, then leaves."""
            self.action_quit_confirm()

        def action_quit_confirm(self):
            """Quit: stop every process this TUI owns, then leave."""
            live = self._live_run_ids()
            try:
                if self._batch_active() and "batch-queue" not in live:
                    live = list(live) + ["batch-queue"]
            except Exception:
                pass
            if live:
                self.push_screen(ConfirmScreen(
                    "Quit the TUI and stop everything it owns?\n\n"
                    "  - agent run(s): %s (launcher, harness, MCP children)\n"
                    "  - the renderer service this TUI started\n"
                    "  - the local Ollama this TUI started (model unloaded from VRAM)\n\n"
                    "Nothing is left running. The run keeps its evidence on disk."
                    % ", ".join(live),
                    on_yes=self._quit_and_stop), callback=self._modal_closed)
            else:
                self._quit_and_stop()

        def _live_run_ids(self) -> list[str]:
            """Run ids that are still running (the runs on disk and the open one)."""
            ids = []
            for entry in self.runs:
                if not isinstance(entry, dict) or not entry.get("runId"):
                    continue
                # The recorded status alone is not liveness: a stale
                # run.json that still says "running" after its process is
                # gone is history (Unknown), never a live agent.
                if str(entry.get("status") or "") == "Running":
                    ids.append(str(entry["runId"]))
            if self.live_run_id and self.live_run_id not in ids:
                if self._run_is_live():
                    ids.append(str(self.live_run_id))
            return ids

        def _hard_stop(self) -> None:
            """The hard stop: kill the agent tree, the local Ollama, the services.

            Textual uses Ctrl+C for copying the selection, so the hard stop
            lives in the slash menu (/hard-stop) and in closing the window.
            """
            try:
                self._cancel_batch("hard-stop")
            except Exception:
                pass
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
            try:
                self._cancel_batch("quit")
            except Exception:
                pass
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
            # A batch queue is cancelled too: the current member stops and the
            # rest are skipped (batch becomes interrupted, never auto-resumed).
            batch_running = self._batch_active()
            if not self.live_run_id:
                if batch_running:
                    self._cancel_batch("stop")
                    self._say(transcript_mod.NOTICE,
                              "Batch stop requested - the current member stops "
                              "and the queue is skipped.")
                    self._render_status()
                    return
                self.notify("No run is open.", severity="warning", timeout=4, markup=False)
                return
            if not self._run_is_live():
                # History is read-only: nothing is working, so there is
                # nothing to stop (no stop marker is written for it).
                self.notify("No live run to stop - the open run is read-only history.",
                            severity="warning", timeout=4, markup=False)
                return
            run_id = self.live_run_id
            if self._stop_requested_for == run_id:
                # Stop asked for this run already: one request, one line.
                return
            self._stop_requested_for = run_id
            if batch_running:
                try:
                    self._batch_cancel.set()
                except Exception:
                    pass
            try:
                runner_mod.request_stop(run_id)
                self._say(transcript_mod.NOTICE,
                          "Stop requested for %s - the harness and its children are "
                          "terminated now, the evidence stays on disk." % run_id)
                if batch_running:
                    self._say(transcript_mod.NOTICE,
                              "Batch queue cancelled - remaining members are skipped.")
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

        def notify(self, message, *args, **kwargs) -> None:
            """Remember the latest toast so a bare Ctrl+C can copy it.

            Toasts dismiss on click, so their text can never be selected
            for Screen.copy_text; the App-level Ctrl+C fallback
            (action_copy_last_toast) copies what was stashed here.
            """
            try:
                text = str(message or "")
                if text.strip():
                    self._last_toast_text = text
                    self._last_toast_at = time.monotonic()
            except Exception:
                pass
            super().notify(message, *args, **kwargs)

        def action_copy_last_toast(self) -> None:
            """Copy the latest toast (bare Ctrl+C, nothing selected).

            Reached only when Screen/Input/TextArea copy actions all
            raised SkipAction, i.e. there is no text selection. Copies
            the stashed toast text via the terminal clipboard escape
            (OSC 52) instead of doing nothing. Stale toasts (older than
            two minutes) are ignored so a stray Ctrl+C cannot surprise
            with ancient text.
            """
            try:
                text = str(getattr(self, "_last_toast_text", "") or "")
                age = time.monotonic() - float(getattr(self, "_last_toast_at", 0.0) or 0.0)
            except Exception:
                return
            if not text or age > 120:
                return
            try:
                super().copy_to_clipboard(text)
            except Exception:
                pass
            try:
                # Bypass the stash: the confirmation itself must not
                # become the next fallback source.
                super().notify("拷贝成功：已复制 %d 个字符" % len(text),
                               timeout=3, markup=False)
            except Exception:
                pass

        def copy_to_clipboard(self, text: str) -> None:
            """Terminal copy (Ctrl+C selection) + success toast.

            Textual's Screen/TextArea/Input all funnel their Ctrl+C here
            (OSC 52 escape). The system-clipboard copies (/copy-json,
            /copy-chat) already toast via _copy_finished; this covers the
            interactive selection copy which was previously silent.

            Empty text means there was no Textual selection (e.g. Ctrl+C
            while a toast is focused - toasts dismiss on click so their
            text is never selected): stay silent so the informative toast
            stays on screen and no false success is shown. Skipping
            super() also avoids sending an empty OSC 52 sequence, which
            clears the terminal clipboard.
            """
            if not str(text or ""):
                return
            try:
                super().copy_to_clipboard(text)
            except Exception:
                pass
            try:
                count = len(str(text or ""))
                # Bypass the stash: the confirmation itself must not become
                # the next bare-Ctrl+C fallback source.
                super().notify("拷贝成功：已复制 %d 个字符" % count,
                               timeout=3, markup=False)
            except Exception:
                pass

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
            """Download the chat record to the user's Downloads folder.

            One file per export; the run folder stays read-only. The
            Downloads folder opens automatically once written.
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
            self.notify("Chat log written: %s" % path, timeout=20, markup=False)
            # The toast is transient (and dismisses on click, so its text
            # can never be Ctrl+C-copied): mirror the path into the
            # conversation where it stays selectable via SafeSelection.
            try:
                self._say(transcript_mod.NOTICE, "Chat log written: %s" % path)
            except Exception:
                pass
            # The export is a download: reveal the Downloads folder so the
            # file is one click away (same opener as /open-folder).
            try:
                evidence_mod.open_path(str(Path(path).parent))
            except Exception:
                pass

        def _export_run_images(self, button=None) -> None:
            """Zip every frame PNG of the open run into Downloads + open it.

            One zip per export; the run folder stays read-only. The
            Downloads folder opens automatically once written.
            """
            if not self.live_dir:
                self.notify("No run is open - open one first.",
                            severity="warning", timeout=4, markup=False)
                return
            run_dir = Path(str(self.live_dir))
            session_dir = None
            try:
                if trace_mod is not None:
                    session_dir = trace_mod.resolve_session_dir(run_dir)
            except Exception:
                session_dir = None
            live_png = self._live_png_path()
            extra = [str(live_png)] if live_png is not None else []
            verification_image = None
            try:
                if runs_mod is not None and hasattr(runs_mod, "final_image_of"):
                    verification = None
                    try:
                        import json as _json
                        verification = _json.loads(
                            (run_dir / "verification.json").read_text(encoding="utf-8"))
                    except (OSError, ValueError):
                        verification = None
                    verification_image = runs_mod.final_image_of(
                        {"directory": str(run_dir),
                         "verification": verification,
                         "sessionId": (session_dir.name if session_dir else None)})
            except Exception:
                verification_image = None
            if verification_image:
                extra.append(str(verification_image))
            if export_images_mod is None:
                self.notify("Image export is unavailable in this session.",
                            severity="error", timeout=4, markup=False)
                return
            try:
                images = export_images_mod.collect_run_images(
                    run_dir, session_dir, extra=extra)
            except Exception as error:
                self.notify("Image export failed: %s" % error,
                            severity="error", timeout=5, markup=False)
                return
            if not images:
                self.notify("No frame PNGs yet - wait for the first render.",
                            severity="warning", timeout=4, markup=False)
                return
            run_id = self.live_run_id

            def _work() -> None:
                path = export_images_mod.export_run_images_zip(
                    images, run_id=run_id)
                self.call_from_thread(self._run_images_exported, path, len(images))

            self._run_guarded("Exporting %d image(s)" % len(images), _work, button)

        def _run_images_exported(self, path, count: int = 0) -> None:
            self.notify("Images written (%d): %s" % (count, path),
                        timeout=20, markup=False)
            # The toast is transient (and dismisses on click, so its text
            # can never be Ctrl+C-copied): mirror the path into the
            # conversation where it stays selectable via SafeSelection.
            try:
                self._say(transcript_mod.NOTICE,
                          "Images written (%d): %s" % (count, path))
            except Exception:
                pass
            # The export is a download: reveal the Downloads folder so the
            # file is one click away (same opener as /open-folder).
            try:
                evidence_mod.open_path(str(Path(path).parent))
            except Exception:
                pass

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
            """Legacy alias: /history now shows Batches + Runs tabs."""
            self._open_history()

        def _open_history(self) -> None:
            """Batches / Runs tabs; a batch opens its detail first."""
            runs = self.discover_runs(fresh=True)
            self.runs = runs
            batches = self._discover_batches()
            if not runs and not batches:
                self.notify("No runs or batches on disk yet.", severity="warning", timeout=4, markup=False)
                return
            self.push_screen(HistoryScreen(batches, runs), callback=self._history_chosen)

        def _history_chosen(self, result) -> None:
            if not result:
                self._focus_composer()
                return
            if isinstance(result, dict) and result.get("__archive_all__"):
                self._archive_all_runs_ask()
                return
            if isinstance(result, dict) and result.get("__batch__") is not None and result.get("__batch_member__") is None:
                batch = result.get("__batch__")
                if isinstance(batch, dict):
                    self._open_batch_detail(batch)
                    return
            if isinstance(result, dict) and result.get("__batch_member__") is not None:
                self._open_batch_member(result.get("__batch_member__"), result.get("__batch__"))
                return
            # Plain run row (Runs tab): open its independent chat.
            self._run_chosen(result)

        def _open_batch_detail(self, batch: dict) -> None:
            """Batch detail: config summary + per-item results."""
            fresh = batch
            try:
                if batches_mod is not None and hasattr(batches_mod, "find_batch"):
                    found = batches_mod.find_batch(str(batch.get("batchId") or ""))
                    if isinstance(found, dict):
                        fresh = found
            except Exception:
                pass
            self.push_screen(BatchDetailScreen(fresh), callback=self._history_chosen)

        def _open_batch_member(self, item: dict, batch=None) -> None:
            """Open one batch member's independent chat (runId survives archive)."""
            run_id = str((item or {}).get("runId") or "")
            stored = str((item or {}).get("directory") or "")
            directory = None
            if run_id:
                try:
                    if batches_mod is not None and hasattr(batches_mod, "find_member_dir"):
                        directory = batches_mod.find_member_dir(run_id, stored)
                except Exception:
                    directory = None
            if directory is None and stored:
                directory = Path(stored)
            if directory is not None:
                try:
                    if Path(directory).is_dir():
                        self.start_live(directory, run_id or Path(directory).name)
                        self._start_poll()
                        self._focus_composer()
                        return
                except Exception:
                    pass
            # Fall back to the live discovery (member still under runtime/).
            try:
                if runs_mod is not None and hasattr(runs_mod, "find_run") and run_id:
                    entry = runs_mod.find_run(run_id)
                    if isinstance(entry, dict) and entry.get("directory"):
                        self.start_live(entry["directory"], entry.get("runId"))
                        self._start_poll()
                        self._focus_composer()
                        return
            except Exception:
                pass
            self.notify("Member run %s is gone (folder missing); batch summary stays." % (run_id or "?"),
                        severity="warning", timeout=5, markup=False)
            # Keep the detail reachable: reopen it.
            if isinstance(batch, dict) and batch.get("batchId"):
                self._open_batch_detail(batch)
            else:
                self._focus_composer()

        def _run_chosen(self, run) -> None:
            if not run:
                self._focus_composer()
                return
            if isinstance(run, dict) and run.get("__archive_all__"):
                self._archive_all_runs_ask()
                return
            if isinstance(run, dict) and run.get("__batch__") is not None:
                # Defensive: batch rows routed here open the detail.
                if run.get("__batch_member__") is not None:
                    self._open_batch_member(run.get("__batch_member__"), run.get("__batch__"))
                else:
                    self._open_batch_detail(run.get("__batch__"))
                return
            self.start_live(run["directory"], run.get("runId"))
            self._start_poll()
            self._focus_composer()

        def _archive_all_runs_ask(self) -> None:
            """One-click tidy: confirm, then move every history run aside."""
            if self._run_is_live() or self._run_state() in ("running", "stopping") or self._batch_active():
                self.notify("The agent is still working - press Esc or Stop first.",
                            severity="warning", timeout=4, markup=False)
                return
            try:
                runs = self.discover_runs(fresh=True)
            except Exception:
                runs = []
            if not runs:
                self.notify("No runs on disk yet.", severity="warning", timeout=4, markup=False)
                return
            live = [str(r.get("runId")) for r in runs
                    if str(r.get("status") or "") == "Running"]
            message = ("Archive all %d run%s to runtime/archive?\n\n"
                       "Live runs are kept%s. There is no unarchive: "
                       "move the folders back manually to restore them."
                       % (len(runs), "" if len(runs) == 1 else "s",
                          (" (%s stays)" % ", ".join(live)) if live else ""))
            self.push_screen(ConfirmScreen(message, on_yes=self._archive_all_runs),
                             callback=self._modal_closed)

        def _archive_all_runs(self) -> None:
            """Move the history aside in a worker; the list is empty after."""
            mod = self._runs_mod()
            archive = getattr(mod, "archive_all_runs", None)
            if archive is None:
                archive = runs_mod.archive_all_runs if runs_mod else None
            if archive is None:
                self.notify("Archive is unavailable in this session.",
                            severity="error", timeout=4, markup=False)
                return

            def _work() -> None:
                try:
                    result = archive()
                except Exception as error:
                    self.call_from_thread(self._say, transcript_mod.ERROR,
                                          "Archive all failed: %s" % error)
                    return
                self.call_from_thread(self._archive_all_done, result)

            self._run_guarded("Archiving runs", _work)

        def _archive_all_done(self, result) -> None:
            """Report the tidy and leave the conversation on a clean list."""
            try:
                self.runs = self.discover_runs(fresh=True)
            except Exception:
                self.runs = []
            if self.live_dir is not None:
                try:
                    if not Path(self.live_dir).exists():
                        with self._poll_lock:
                            self.live_dir = None
                            self.live_run_id = None
                            self.live_tail = None
                            self.stream_tail = None
                        self.live_events = []
                        self.live_frozen = False
                        self._live_following = True
                        self._stop_requested_for = None
                        self._end_announced = False
                        self.live_image_path = None
                        self.live_image_revision = None
                        self._clear_transcript()
                        self._say_welcome()
                except Exception:
                    pass
            try:
                moved = list((result or {}).get("moved") or [])
                skipped = list((result or {}).get("skipped_live") or [])
                errors = list((result or {}).get("errors") or [])
                sessions = list((result or {}).get("sessions_moved") or [])
                where = str((result or {}).get("archive_dir") or "runtime/archive")
            except Exception:
                moved, skipped, errors, sessions, where = [], [], [], [], "runtime/archive"
            if errors:
                self._say(transcript_mod.ERROR,
                          "Archive all: moved %d, live kept %d, failed %d (%s). "
                          "See %s." % (len(moved), len(skipped), len(errors),
                                       "; ".join(str(e.get("error", e)) for e in errors[:3]), where))
            elif not moved:
                self.notify("Nothing archived%s." % (" - live runs are kept" if skipped else ""),
                            severity="warning", timeout=4, markup=False)
            else:
                detail = " (+%d session folder%s)" % (len(sessions), "" if len(sessions) == 1 else "s") if sessions else ""
                kept = " Live kept: %s." % ", ".join(skipped) if skipped else ""
                self._say(transcript_mod.NOTICE,
                          "Archived %d run%s%s to %s.%s There is no unarchive: "
                          "move the folders back manually to restore them."
                          % (len(moved), "" if len(moved) == 1 else "s",
                             detail, where, kept))
            try:
                self._render_status()
                self._sync_send_button()
            except Exception:
                pass
            self._focus_composer()

        def _new_conversation(self, arg: str = "") -> None:
            """Refresh and open a new run: clear the view, ready for the next task.

            Like opencode's /new: the open run is closed on screen (its
            evidence stays on disk), the run list is re-read fresh and the
            conversation returns to the welcome state. A trailing task
            ("/new rotate a little") starts that run at once.
            Never touches a live run: stop it first (Esc/Stop).
            """
            if self._start_locked or self._batch_active():
                self.notify("A run is already starting - wait for it.",
                            severity="warning", timeout=4, markup=False)
                return
            if self._run_is_live() or self._run_state() in ("running", "stopping"):
                self.notify("The agent is still working - press Esc or Stop first.",
                            severity="warning", timeout=4, markup=False)
                return
            task = str(arg or "").strip()
            with self._poll_lock:
                self.live_dir = None
                self.live_run_id = None
                self.live_tail = None
                self.stream_tail = None
            self.live_events = []
            self.live_frozen = False
            self._live_following = True
            self._stop_requested_for = None
            self._end_announced = False
            self.live_image_path = None
            self.live_image_revision = None
            self._clear_transcript()
            self._say_welcome()
            try:
                self.runs = self.discover_runs(fresh=True)
            except Exception:
                pass
            self._render_status()
            self._sync_send_button()
            if task:
                try:
                    self._history_push(task)
                except Exception:
                    pass
                self._focus_composer()
                self._start_run("custom", task)
                return
            self._focus_composer()

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
                self._focus_composer()
                return
            path = str((volume or {}).get("path") or "")
            if not path:
                self._focus_composer()
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
            self._focus_composer()

        # -- settings --------------------------------------------------------
        def _open_settings(self) -> None:
            self.push_screen(SettingsScreen(self.settings), callback=self._settings_saved)

        def _settings_saved(self, settings) -> None:
            if settings:
                self.settings = dict(settings)
            self._focus_composer()

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
            # When the menu is open with a highlight, Enter/Send runs the
            # highlighted entry even if the typed text is only a prefix
            # (e.g. "/v" with "/change-volume" highlighted runs /change-volume).
            if str(task).lstrip().startswith("/"):
                try:
                    if self._run_slash_selection():
                        return
                except Exception:
                    pass
            try:
                if self._run_slash_text(task):
                    return
            except Exception:
                pass
            if self._run_state() == "running" or self._batch_active():
                # One run at a time: the agent has to stop first (Esc).
                self.notify("The agent is still working - press Esc or Stop first.",
                            severity="warning", timeout=4, markup=False)
                return
            try:
                self._history_push(task)
            except Exception:
                pass
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
            # The click moved focus to the button: hand it back so the
            # composer's solid block cursor never disappears.
            self._focus_composer()

        # -- starting a run --------------------------------------------------
        def _open_batch(self) -> None:
            """Open the one-shot batch form (/batch)."""
            if self._batch_active():
                self.notify("A batch is already running - stop it first (Esc).",
                            severity="warning", timeout=4, markup=False)
                return
            if self._start_locked:
                self.notify("A run is already starting - wait for it.",
                            severity="warning", timeout=4, markup=False)
                return
            if self._run_is_live() or self._run_state() in ("running", "stopping"):
                self.notify("The agent is still working - press Esc or Stop first.",
                            severity="warning", timeout=4, markup=False)
                return
            # Prefill: copy of current Settings + current open run's task.
            prefill_task = self._batch_task_prefill()
            prefill_settings = dict(self.settings)
            self.push_screen(BatchScreen(prefill_settings, prefill_task),
                             callback=self._batch_chosen)

        def _batch_chosen(self, result) -> None:
            if not result:
                self._focus_composer()
                return
            try:
                count = int(result.get("count", 0))
                task = str(result.get("task") or "").strip()
                settings_snapshot = dict(result.get("settings") or {})
            except Exception:
                self._focus_composer()
                return
            self._focus_composer()
            self._start_batch(count, task, settings_snapshot)

        def _start_batch(self, count: int, task: str, settings_snapshot: dict) -> None:
            """Freeze the effective config once, then run N members in order."""
            if self._batch_active() or self._start_locked:
                self.notify("A batch or run is already starting - wait for it.",
                            severity="warning", timeout=4, markup=False)
                return
            if self._run_is_live() or self._run_state() in ("running", "stopping"):
                self.notify("The agent is still working - press Esc or Stop first.",
                            severity="warning", timeout=4, markup=False)
                return
            problems = batch_form_problems(count, task, settings_snapshot)
            if problems:
                self._say(transcript_mod.ERROR,
                          "Batch blocked:\n" + "\n".join("- %s" % p for p in problems[:8]))
                return
            self._start_locked = True
            self.run_worker(lambda: self._batch_worker(count, task, settings_snapshot),
                            thread=True)

        def _batch_worker(self, count: int, task: str, settings_snapshot: dict) -> None:
            self._busy_inc()
            try:
                self._batch_body(count, task, settings_snapshot)
            except Exception as error:
                try:
                    self.call_from_thread(self._say, transcript_mod.ERROR,
                                          "Batch failed: %s" % error)
                except Exception:
                    pass
            finally:
                # Batch owns the start lock for its whole queue (members must
                # not interleave with single runs); release it here, not via
                # the single-run _settle_launch flag.
                def _settle_batch():
                    self._start_locked = False
                    self._busy_dec()
                    try:
                        self._render_status()
                    except Exception:
                        pass
                    try:
                        self._sync_send_button()
                    except Exception:
                        pass
                try:
                    self.call_from_thread(_settle_batch)
                except Exception:
                    try:
                        _settle_batch()
                    except Exception:
                        pass

        def _batch_body(self, count: int, task: str, settings_snapshot: dict) -> None:
            import copy as _copy
            # Freeze once: same effective config object for every member.
            # Never writes runtime/tui-settings.json (snapshot only).
            settings_snapshot = dict(settings_snapshot)
            effective, problems = flows_mod.build_new_run("custom", task, settings_snapshot, cfg)
            gate = flows_mod.service_gate(settings_snapshot, env_mod, services_mod)
            problems = list(problems) + list(gate)
            if problems:
                self.call_from_thread(
                    self._say, transcript_mod.ERROR,
                    "Batch blocked:\n" + "\n".join(
                        "- %s" % (p if isinstance(p, str) else p.get("name"))
                        for p in problems[:8]))
                return
            frozen = _copy.deepcopy(effective)
            try:
                batch_id = batches_mod.new_batch_id() if batches_mod else "batch-%d" % int(time.time())
            except Exception:
                batch_id = "batch-%d" % int(time.time())
            try:
                batches_mod.create_batch_record(batch_id, frozen, int(count))
            except Exception as error:
                self.call_from_thread(self._say, transcript_mod.ERROR,
                                      "Batch record failed: %s" % error)
                return
            self._batch = {"batchId": str(batch_id), "total": int(count),
                           "current": 0, "running": True}
            try:
                self._batch_cancel.clear()
            except Exception:
                pass
            self.call_from_thread(self._say, transcript_mod.NOTICE,
                                  "◇ BATCH %s · 0/%d starting - config frozen." % (batch_id, int(count)))
            self.call_from_thread(self._render_status)
            for seq in range(1, int(count) + 1):
                if self._batch_cancel.is_set():
                    self._batch_mark_skipped_from(batch_id, seq, int(count))
                    break
                self._batch["current"] = seq
                self.call_from_thread(self._say, transcript_mod.NOTICE,
                                      "◇ BATCH %s · %d/%d running." % (batch_id, seq, int(count)))
                self.call_from_thread(self._render_status)
                started = time.time()
                member_dir = None
                member_run_id = None
                member_error = None
                member_exit = None
                try:
                    config_path = flows_mod.write_config(_copy.deepcopy(frozen), "custom", cfg)
                except Exception as error:
                    member_error = str(error)
                    config_path = None
                if config_path is not None:
                    self.call_from_thread(self._say, transcript_mod.NOTICE,
                                          "Batch member %d/%d launching %s..." % (seq, int(count), config_path.name))
                    try:
                        outcome = self._batch_launch_one(config_path, batch_id, seq, int(count))
                        member_dir = outcome.get("directory")
                        member_run_id = outcome.get("run_id")
                        member_exit = outcome.get("exit_code")
                        member_error = outcome.get("error") or member_error
                    except Exception as error:
                        member_error = str(error)
                seconds = round(time.time() - started, 3)
                failed = False
                if member_error:
                    failed = True
                elif member_exit is not None:
                    try:
                        failed = int(member_exit) != 0
                    except (TypeError, ValueError):
                        failed = False
                if not member_run_id and not member_dir and not member_error:
                    failed = True
                    member_error = "launch produced no run"
                status = "failed" if failed else "success"
                try:
                    record = batches_mod.load_batch(batch_id) or {}
                    items = record.get("items") or []
                    items.append({"seq": seq, "runId": member_run_id,
                                  "directory": str(member_dir) if member_dir else None,
                                  "status": status, "seconds": seconds,
                                  "error": member_error, "exitCode": member_exit})
                    record["items"] = items
                    record["updatedAt"] = time.time()
                    batches_mod.save_batch(record)
                except Exception:
                    pass
                if self._batch_cancel.is_set():
                    self._batch_mark_skipped_from(batch_id, seq + 1, int(count))
                    break
            try:
                record = batches_mod.load_batch(batch_id) or {}
                if self._batch_cancel.is_set() or str(record.get("status") or "") == "interrupted":
                    record["status"] = "interrupted"
                else:
                    record["status"] = batches_mod.finalize_status(record)
                record["updatedAt"] = time.time()
                batches_mod.save_batch(record)
                final = str(record.get("status") or "")
            except Exception:
                final = "completed"
            try:
                if isinstance(self._batch, dict):
                    self._batch["running"] = False
            except Exception:
                pass
            # Exiting the TUI cancels the queue; never auto-resume.
            self.call_from_thread(self._say, transcript_mod.NOTICE,
                                  "◇ BATCH %s · finished: %s." % (batch_id, final))
            self.call_from_thread(self._render_status)
            self.call_from_thread(self._sync_send_button)

        def _batch_mark_skipped_from(self, batch_id: str, start_seq: int, total: int) -> None:
            try:
                record = batches_mod.load_batch(batch_id) or {}
                items = record.get("items") or []
                existing = {int(i.get("seq") or 0) for i in items}
                for rest in range(int(start_seq), int(total) + 1):
                    if rest in existing:
                        continue
                    items.append({"seq": rest, "runId": None, "directory": None,
                                  "status": "skipped", "seconds": 0,
                                  "error": "cancelled"})
                record["items"] = items
                record["status"] = "interrupted"
                record["updatedAt"] = time.time()
                batches_mod.save_batch(record)
            except Exception:
                pass
            try:
                if isinstance(self._batch, dict):
                    self._batch["running"] = False
            except Exception:
                pass
            try:
                self.call_from_thread(self._say, transcript_mod.NOTICE,
                                      "◇ BATCH %s · interrupted - remaining members skipped." % batch_id)
                self.call_from_thread(self._render_status)
            except Exception:
                pass

        def _batch_launch_one(self, config_path, batch_id: str, seq: int, total: int) -> dict:
            """One member run, blocking; chat shows only this member run."""
            outcome: dict = {}
            holder: dict = {}

            def on_line(line: str):
                try:
                    payload = json.loads(line)
                except ValueError:
                    return
                if payload.get("event") == "run_started" and payload.get("directory"):
                    holder["directory"] = payload["directory"]
                    holder["run_id"] = payload.get("runId")
                    try:
                        self.call_from_thread(self._enter_live_for_batch,
                                              payload["directory"], payload.get("runId"),
                                              batch_id, seq, total)
                    except Exception:
                        pass

            try:
                result = runner_mod.launch(Path(config_path), on_line=on_line)
                outcome["exit_code"] = result.exit_code
                outcome["output_tail"] = getattr(result, "output_tail", "")
                if holder.get("directory"):
                    outcome["directory"] = holder["directory"]
                    outcome["run_id"] = holder.get("run_id")
                elif getattr(result, "directory", None):
                    outcome["directory"] = str(result.directory)
                    outcome["run_id"] = result.run_id
                    try:
                        self.call_from_thread(self._enter_live_for_batch,
                                              str(result.directory), result.run_id,
                                              batch_id, seq, total)
                    except Exception:
                        pass
                if outcome.get("exit_code") not in (None, 0):
                    tail = str(outcome.get("output_tail") or "")[-800:]
                    outcome["error"] = "exit %s %s" % (outcome.get("exit_code"), tail)
                    outcome["error"] = outcome["error"].strip()
                elif not outcome.get("directory"):
                    outcome["error"] = "launch ended without reserving a run"
            except Exception as error:
                outcome["error"] = str(error)
            return outcome

        def _enter_live_for_batch(self, directory, run_id, batch_id: str, seq: int, total: int):
            # Chat shows only the current member run; the batch marker is
            # announced alongside. No single-run lock bookkeeping here: the
            # batch worker owns _start_locked for the whole queue.
            try:
                self.start_live(directory, run_id)
            except Exception:
                pass
            try:
                self._start_poll()
            except Exception:
                pass
            try:
                self._say(transcript_mod.NOTICE,
                          "◇ BATCH %s · %d/%d open in chat." % (batch_id, seq, total))
            except Exception:
                pass
            try:
                self._render_status()
            except Exception:
                pass

        def _start_run(self, task_kind: str, prompt: str) -> None:
            """Preflight and launch. The start lock keeps one run at a time."""
            if self._start_locked or self._batch_active():
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

    def _restore_windows_console() -> None:
        """Best-effort console repair for abnormal exits on Windows.

        A killed Textual app can leave the parent PowerShell with the
        alternate screen active and raw input mode on (PSReadLine then
        loses highlighting and Backspace echoes ^H). Undo both; never
        raise, never depend on anything but ctypes.
        """
        try:
            import os as _os
            if _os.name != "nt":
                return
            try:
                import sys as _s
                _s.stdout.write("\x1b[?1049l\x1b[?25h")
                _s.stdout.flush()
            except Exception:
                pass
            try:
                import ctypes as _ct
                _k = _ct.windll.kernel32
                _h = _k.GetStdHandle(-10)
                _mode = _ct.c_uint(0)
                if _k.GetConsoleMode(_h, _ct.byref(_mode)):
                    _k.SetConsoleMode(_h, 0x1F7)
            except Exception:
                pass
        except Exception:
            pass

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
            finally:
                # Heal the parent PowerShell console in place (no new tab).
                _restore_windows_console()

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
