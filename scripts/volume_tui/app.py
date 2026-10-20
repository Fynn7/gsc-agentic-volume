"""Textual IDE-style TUI: Dashboard / New Run / Runs / Live / Volumes / Services / Settings.

Control/display only: runs start via scripts/run_local_agent.py, evidence is
read-only except new runs + verification.json + tui-settings.json.
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
    import config as cfg
    import env as env_mod
    import runs as runs_mod
    import runner as runner_mod
    import services as services_mod
    import trace_events as trace_mod
    import volumes as volumes_mod
    from context import AppContext
    import evidence as evidence_mod
    import flows as flows_mod
    import imaging as imaging_mod
    import telemetry as telemetry_mod
except Exception:  # pragma: no cover - import path for tests
    cfg = None  # type: ignore
    env_mod = None  # type: ignore
    runs_mod = None  # type: ignore
    runner_mod = None  # type: ignore
    services_mod = None  # type: ignore
    trace_mod = None  # type: ignore
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
    from textual.screen import ModalScreen
    from textual.widgets import (
        Button,
        DataTable,
        Footer,
        Header,
        Input,
        Label,
        LoadingIndicator,
        RichLog,
        Select,
        Static,
        TabbedContent,
        TabPane,
        TextArea,
    )
    _TEXTUAL = True
except Exception:
    _TEXTUAL = False
    App = object  # type: ignore
    ComposeResult = None  # type: ignore

CATEGORIES = ["ALL", "MODEL", "TOOL", "IMAGE", "INFER", "ERROR", "WARN", "FINISH"]
HINT_TEXT = ("F1 = what every button does | Ctrl+Up/Down = scroll window | "
             "wheel scrolls outside text boxes")
TASK_PRESETS = ["task-a", "task-b", "custom"]

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

# Preview budget: the frame is fitted into this many text rows, so the whole
# image is visible at once (never a cut-off corner).
LIVE_PREVIEW_MAX_ROWS = 48


class DismissibleModal(ModalScreen if _TEXTUAL else object):
    """Modal screen that closes with Esc.

    Textual's ModalScreen ships no escape binding of its own (only the
    built-in command palette has one), so the help screen and the command
    palette ignored Esc even though both advertise it. The binding lives on
    the screen, so it wins over the app bindings while the modal is up.
    """

    if _TEXTUAL:
        BINDINGS = [Binding("escape", "dismiss", "Close", show=False)]


class HelpScreen(DismissibleModal):
    def compose(self):
        yield Static(
            "Volume Agent TUI — what every button does\n"
            "\n"
            "Tabs: Dashboard / New Run / Runs / Live / Volumes / Services / Settings\n"
            "Ctrl+P = command palette · F1 = this help · t = theme · q = quit "
            "(asks first while a run is live)\n"
            "\n"
            "DASHBOARD — mode, model, volume and service state at a glance\n"
            "  New run          open the New Run tab to start an agent run\n"
            "  Open Live view   open the Live tab; with a run selected in Runs it\n"
            "                    follows that run\n"
            "  Refresh status   re-read mode, model, volume, service state and the\n"
            "                    run list from disk (nothing is written)\n"
            "\n"
            "LIVE — watch one run while it runs\n"
            "  Stop run          ask the agent to stop now; evidence already\n"
            "                    written is kept, the launcher records 'interrupted'\n"
            "  Verify evidence   read-only check of this run: did it finish, and was\n"
            "                    a PNG really sent to the model? Writes\n"
            "                    verification.json. Never calls the model.\n"
            "  View PNG          open the previewed PNG in your image viewer (full size)\n"
            "  Open folder       open the file explorer at the folder that holds the\n"
            "                    PNGs — the run log folder itself has no images\n"
            "  Copy JSON log     copy the whole run's JSON log to the clipboard: the\n"
            "                    run's meta data, every event and every JSON log file\n"
            "                    of the run folder. Read-only.\n"
            "  Stop watching     leave the Live view; the run keeps running in the\n"
            "                    background (open it again from Runs)\n"
            "\n"
            "  The event timeline follows the newest event on its own while it is\n"
            "  scrolled to the bottom; scroll up to read the history and it stops\n"
            "  following until you scroll back down.\n"
            "\n"
            "RUNS — runs already on disk\n"
            "  Open Live view    follow the selected run in the Live tab\n"
            "  Verify evidence   same read-only check as in Live, for the selected run\n"
            "  Rerun as new run   copy the selected run's task into New Run; nothing\n"
            "                    starts until you press Start agent run\n"
            "  Pick as A / B     remember two runs, then Compare A vs B shows their\n"
            "                    config difference\n"
            "  Refresh list      re-read the runs on disk (nothing is written)\n"
            "\n"
            "NEW RUN\n"
            "  Check services & settings  check the local inference service and the\n"
            "                    render settings; nothing is written\n"
            "  Start agent run   write the run config and launch the agent; the Live\n"
            "                    tab opens by itself\n"
            "\n"
            "VOLUMES\n"
            "  Preview slice     render one slice of the selected prepared volume\n"
            "                    (read-only, no render budget)\n"
            "  Use this volume   use the selected volume for the next run\n"
            "\n"
            "SERVICES — only services this TUI started are ever touched\n"
            "  Start missing services  start the owned services that are down\n"
            "  Stop owned services     stop the owned services\n"
            "  Refresh status          re-read the owned services and their logs\n"
            "  Restart owned           restart the owned services\n"
            "  Stop all services       stop the agent tree, the local Ollama this TUI\n"
            "                    started and the owned services (the model unloads\n"
            "                    from VRAM)\n"
            "\n"
            "SETTINGS\n"
            "  Save settings     write the form to runtime/tui-settings.json\n"
            "  Reset to defaults fill the form with the built-in defaults (writes nothing)\n"
            "  Test H200 endpoint send one small probe request; never falls back silently\n"
            "\n"
            "Evidence is read-only except new runs, verification.json and\n"
            "tui-settings.json. Ctrl+C is the hard stop (agent tree + local Ollama +\n"
            "owned services, confirmed).\n"
            "\n"
            "LEAVING THE TUI leaves no process behind: the live agent run is stopped\n"
            "(its evidence stays complete), the services this TUI started are\n"
            "stopped with it, and anything that survives is killed. Ctrl+C does the\n"
            "same and additionally stops a local Ollama that this TUI did not start.\n"
            "\n"
            "Press Esc to close.",
            id="help-text",
        )


class FramePreview(Static if _TEXTUAL else object):
    """Preview pane for the current frame PNG.

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


class ConfirmScreen(DismissibleModal):
    def __init__(self, message: str, on_yes=None):
        super().__init__()
        self._message = message
        self._on_yes = on_yes

    def compose(self):
        yield Static(self._message, id="confirm-message")
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
    # (label, action id, tooltip) — the id is stable, the label spells out the
    # effect and the tooltip says it once more (every button carries one).
    COMMANDS = [
        ("Go: Dashboard", "go-dashboard", "Show mode, model, volume and service state."),
        ("Go: New Run", "go-newrun", "Open the form that starts an agent run."),
        ("Go: Runs", "go-runs", "Show the runs already on disk."),
        ("Go: Live", "go-live", "Watch the selected run while it runs."),
        ("Go: Volumes", "go-volumes", "Show the prepared volumes found on this machine."),
        ("Go: Services", "go-services", "Show the services this TUI owns."),
        ("Go: Settings", "go-settings", "Show the form that writes runtime/tui-settings.json."),
        ("Live: Jump to newest event", "live-jump",
         "Scroll the event timeline back to the newest event."),
        ("Live: Stop the run", "live-stop",
         "Ask the agent to stop now; evidence already written is kept."),
        ("Live: Verify evidence (read-only)", "live-verify",
         "Check this run's evidence; writes verification.json, never calls the model."),
        ("Live: View current PNG", "live-open-png",
         "Open the previewed PNG in your image viewer (full size)."),
        ("Live: Open PNG folder", "live-open",
         "Open the file explorer at the folder that holds the PNGs."),
        ("Live: Stop watching (run keeps going)", "live-detach",
         "Leave the Live view; the run keeps running in the background."),
        ("Runs: Refresh list", "runs-refresh", "Re-read the runs on disk."),
        ("Services: Refresh list", "services-refresh",
         "Re-read the owned services and their logs."),
        ("Help: what every button does", "help", "Open this help screen."),
        ("Quit the TUI", "quit",
         "Stop everything the TUI owns, then leave. Asks first while a run is live."),
    ]

    def compose(self):
        yield Static("Command palette (Enter runs, Esc closes)", id="cmd-title")
        for label, action, tooltip in self.COMMANDS:
            yield Button(label, id="cmd-%s" % action, tooltip=tooltip)

    async def on_button_pressed(self, event):
        self.dismiss(result=event.button.id)


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
        TITLE = "Volume Agent TUI"
        CSS = """
        TabbedContent { height: 1fr; }
        TabPane { height: 1fr; }
        VerticalScroll { height: 1fr; }
        #busy-spinner { height: 1; display: none; }
        #busy-spinner.busy { display: block; }
        .panel { height: auto; padding: 1 2; }
        .row { height: 3; }
        .row > Input { width: 1fr; }
        .row > Select { width: 1fr; }
        .field-label { width: 24; content-align: right middle; padding-right: 1; }
        .narrow { width: 18; }
        #status-bar { height: 5; border: solid green; }
        #confirm-hint { height: auto; color: $text-muted; }
        #newrun-prompt { height: 8; }
        #newrun-preflight { height: auto; min-height: 3; }
        .scroll-hint { color: gray; }
        #runs-table { height: 12; }
        #runs-detail { height: auto; min-height: 3; }
        #events-table { height: 10; }
        #live-detail { height: 5; border: solid gray; }
        #live-image-path { height: auto; color: $text-muted; }
        #live-image { height: auto; min-height: 3; border: solid cyan; }
        #volumes-table { height: 10; }
        #volumes-detail { height: auto; min-height: 3; }
        #slice-preview { height: 14; border: solid cyan; }
        #services-table { height: 8; }
        #service-logs { height: 10; }
        #settings-status { height: auto; min-height: 3; }
        """

        BINDINGS = [
            # "app.command_palette" is the name Textual looks for: declaring it
            # stops Textual from adding its own ctrl+p binding, so Ctrl+P opens
            # the TUI palette (the Go:/Live: list) and not the generic system
            # palette, which Textual would otherwise bind with priority.
            Binding("ctrl+p", "app.command_palette", "Commands", show=False),
            Binding("f1", "help", "Help"),
            Binding("t", "toggle_dark", "Theme"),
            Binding("q", "quit_confirm", "Quit"),
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
            self.volumes: list[dict] = []
            self.services_report: dict = {}
            self.live_run_id: str | None = None
            self.live_dir: Path | None = None
            self.live_tail = None
            self.live_events: list[dict] = []
            self.live_frozen = False
            self._live_following = True
            self.live_filter = "ALL"
            self.live_search = ""
            self.live_image_path: str | None = None
            self.live_image_revision = None
            self._preview_key: tuple | None = None
            self.compare_a: str | None = None
            self.compare_b: str | None = None
            self._launcher_thread: threading.Thread | None = None
            self._launcher_result: dict = {}
            self._start_locked = False
            self._busy_count = 0

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
            double clicks; the top spinner animates while any worker is active;
            failures surface as error toasts instead of vanishing silently.
            """
            if button is not None:
                try:
                    button.loading = True
                except Exception:
                    pass
            self._busy_inc()

            def _finish(message=None, error=False):
                if button is not None:
                    try:
                        button.loading = False
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

        def discover_volumes(self) -> list[dict]:
            if self.ctx is not None and getattr(self.ctx, "discover_volumes", None):
                try:
                    return list(self.ctx.discover_volumes())
                except Exception:
                    return []
            try:
                current = cfg.selected_volume(self.settings) if cfg else None
                return list(volumes_mod.discover_volumes(current))
            except Exception:
                return []

        # -- layout -------------------------------------------------------
        def compose(self) -> ComposeResult:
            yield Header()
            yield LoadingIndicator(id="busy-spinner")
            with TabbedContent(id="workspaces"):
                with TabPane("Dashboard", id="tab-dashboard"):
                    with VerticalScroll():
                        with Vertical(classes="panel"):
                            yield Static("Loading…", id="status-bar")
                            with Horizontal(classes="row"):
                                yield Button("New run", id="btn-goto-newrun", variant="primary",
                                             tooltip="Open the New Run tab to start a new agent run.")
                                yield Button("Open Live view", id="btn-goto-live",
                                             tooltip="Open the Live tab. With a run selected in Runs it follows that run.")
                                yield Button("Refresh status", id="btn-refresh",
                                             tooltip="Re-read mode, model and the run list from disk.")
                with TabPane("New Run", id="tab-newrun"):
                    with VerticalScroll():
                        with Vertical(classes="panel"):
                            yield Static(HINT_TEXT, classes="scroll-hint")
                            yield Label("Task preset")
                            yield Select([(p, p) for p in TASK_PRESETS], value="task-a", id="newrun-preset")
                            yield Label("Prompt (editable; A/B presets load audited text)")
                            yield TextArea("", id="newrun-prompt")
                            yield Label("Volume header path (empty = current selection)")
                            yield Input("", placeholder="D:/path/to/render.dat", id="newrun-volume")
                            for label, wid, value in (
                                ("Model", "newrun-model", str(self.settings.get("model", ""))),
                                ("Render budget", "newrun-budget", str(self.settings.get("renderBudget", 4))),
                                ("Context size", "newrun-context", str(self.settings.get("contextSize", 16384))),
                                ("Temperature", "newrun-temp", str(self.settings.get("temperature", 0)))):
                                with Horizontal(classes="row"):
                                    yield Label(label, classes="field-label")
                                    yield Input(value, id=wid)
                            yield Static("", id="newrun-preflight")
                            with Horizontal(classes="row"):
                                yield Button("Check services & settings", id="btn-preflight",
                                             tooltip="Check the local inference service and the render settings "
                                                     "before starting. Nothing is written.")
                                yield Button("Start agent run", id="btn-start-run", variant="success",
                                             tooltip="Write the run config and launch the agent. The Live tab opens by itself.")
                with TabPane("Runs", id="tab-runs"):
                    with VerticalScroll():
                        with Vertical(classes="panel"):
                            yield Static(HINT_TEXT, classes="scroll-hint")
                            yield Input("", placeholder="/ filter runId/status/model/task", id="runs-filter")
                            yield DataTable(id="runs-table")
                            with Horizontal(classes="row"):
                                yield Button("Open Live view", id="btn-runs-follow", variant="primary",
                                             tooltip="Open the Live tab and follow the selected run.")
                                yield Button("Verify evidence", id="btn-runs-verify",
                                             tooltip="Read-only check of the selected run: did it finish, and was a PNG "
                                                     "really sent to the model? Writes verification.json, never calls the model.")
                                yield Button("Rerun as new run", id="btn-runs-rerun",
                                             tooltip="Copy the selected run's task into New Run. Nothing starts until "
                                                     "you press Start agent run.")
                                yield Button("Refresh list", id="btn-runs-refresh",
                                             tooltip="Re-scan the run folders on disk.")
                            with Horizontal(classes="row"):
                                yield Button("Pick as A", id="btn-compare-a",
                                             tooltip="Remember the selected run as comparison A.")
                                yield Button("Pick as B", id="btn-compare-b",
                                             tooltip="Remember the selected run as comparison B.")
                                yield Button("Compare A vs B", id="btn-compare-show",
                                             tooltip="Show the config difference of the two picked runs.")
                            yield Static("", id="runs-detail")
                with TabPane("Live", id="tab-live"):
                    with VerticalScroll():
                        with Vertical(classes="panel"):
                            yield Static(HINT_TEXT, classes="scroll-hint")
                            yield Static("No live run. Start one from New Run or open a run from Runs.", id="live-head")
                            yield Static("", id="live-resources")
                            with Horizontal(classes="row"):
                                yield Input("", placeholder="/ search events", id="live-search")
                                yield Select([(c, c) for c in CATEGORIES], value="ALL", id="live-category", classes="narrow")
                                yield Button("Copy JSON log", id="btn-live-copy",
                                             tooltip="Copy the whole run's JSON log to the clipboard: the run's "
                                                     "meta data, every event and every JSON log file of the run "
                                                     "folder. Read-only.")
                            yield DataTable(id="events-table")
                            yield Static("(select an event for details)", id="live-detail")
                            yield Static("Current frame PNG: (none yet)", id="live-image-path")
                            yield FramePreview("(no frame yet)", id="live-image")
                            with Horizontal(classes="row"):
                                yield Button("Stop run", id="btn-live-stop", variant="error",
                                             tooltip="Ask the agent to stop now. Evidence already written is kept.")
                                yield Button("Verify evidence", id="btn-live-verify",
                                             tooltip="Read-only check of this run: did it finish, and was a PNG really "
                                                     "sent to the model? Writes verification.json, never calls the model.")
                                yield Button("View PNG", id="btn-live-open-png",
                                             tooltip="Open the previewed PNG in your image viewer, full size.")
                                yield Button("Open folder", id="btn-live-open",
                                             tooltip="Open the file explorer at the folder that holds the PNGs "
                                                     "(the run log folder itself has no images).")
                                yield Button("Stop watching", id="btn-live-detach",
                                             tooltip="Leave the Live view. The run keeps going until the TUI is "
                                                     "closed (closing stops it); open it again from Runs.")
                            yield Static("Verify evidence: (not run yet — read-only check of this run)",
                                         id="live-verify")
                with TabPane("Volumes", id="tab-volumes"):
                    with VerticalScroll():
                        with Vertical(classes="panel"):
                            yield Static(HINT_TEXT, classes="scroll-hint")
                            yield Input("", placeholder="/ filter path/dims", id="volumes-filter")
                            yield DataTable(id="volumes-table")
                            yield Static("", id="volumes-detail")
                            with Horizontal(classes="row"):
                                yield Label("Axis", classes="field-label")
                                yield Select([("x", "x"), ("y", "y"), ("z", "z")], value="z", id="slice-axis", classes="narrow")
                                yield Label("Index", classes="field-label")
                                yield Input("0", placeholder="index", id="slice-index")
                            with Horizontal(classes="row"):
                                yield Button("Preview slice", id="btn-slice",
                                             tooltip="Render one slice of the selected prepared volume (read-only, "
                                                     "uses no render budget).")
                                yield Button("Use this volume", id="btn-volume-use", variant="primary",
                                             tooltip="Use the selected volume for the next run.")
                            yield Static("(no slice yet)", id="slice-preview")
                with TabPane("Services", id="tab-services"):
                    with VerticalScroll():
                        with Vertical(classes="panel"):
                            yield Static(HINT_TEXT, classes="scroll-hint")
                            yield DataTable(id="services-table")
                            with Horizontal(classes="row"):
                                yield Button("Start missing services", id="btn-svc-start", variant="success",
                                             tooltip="Start the services this TUI owns that are not running.")
                                yield Button("Stop owned services", id="btn-svc-stop",
                                             tooltip="Stop only the services this TUI started. Foreign processes are never touched.")
                                yield Button("Refresh status", id="btn-svc-refresh",
                                             tooltip="Re-check which services are alive.")
                            with Horizontal(classes="row"):
                                yield Button("Restart owned", id="btn-svc-restart",
                                             tooltip="Restart the owned services one by one.")
                                yield Button("Stop all services", id="btn-svc-stopall", variant="error",
                                             tooltip="Stop the agent tree, the local Ollama this TUI started and the owned "
                                                     "services. The model unloads from VRAM.")
                            yield RichLog(id="service-logs", max_lines=200)
                with TabPane("Settings", id="tab-settings"):
                    with VerticalScroll():
                        with Vertical(classes="panel"):
                            yield Static(HINT_TEXT, classes="scroll-hint")
                            yield Label("Settings (saved to runtime/tui-settings.json on Save)")
                        for label, wid, key, _kind in SETTINGS_ROWS:
                            with Horizontal(classes="row"):
                                yield Label(label, classes="field-label")
                                yield Input(str(self.settings.get(key, "")), id=wid)
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
                                         tooltip="Send one small probe request to the H200 endpoint. Never falls back silently.")
                        yield Static("", id="settings-status")
            yield Footer()

        # -- lifecycle ----------------------------------------------------
        async def on_mount(self):
            for table_id, cols in (
                ("runs-table", ["run", "status", "model", "frames", "task"]),
                ("events-table", ["#", "cat", "title"]),
                ("volumes-table", ["path", "dims", "flags"]),
                ("services-table", ["service", "pid", "state"]),
            ):
                try:
                    table = self.query_one("#" + table_id, DataTable)
                    table.clear(columns=True)
                    for col in cols:
                        table.add_column(col)
                except Exception:
                    pass
            self.set_interval(0.5, self._tick_live)
            self._run_guarded("Loading dashboard", self._load_dashboard)

        def _load_dashboard(self):
            try:
                runs = self.discover_runs()
                self.runs = runs
                running = [r for r in runs if r.get("status") == "Running"]
                last = runs[0] if runs else None
                volume = None
                try:
                    volume = cfg.selected_volume(self.settings) if cfg else None
                except Exception:
                    volume = None
                try:
                    status = services_mod.service_status(self.settings)
                except Exception:
                    status = {}
                text = (
                    "Mode %s · Model %s\n"
                    "Volume %s · Inference %s · Renderer %s\n"
                    "Owned %s · Runs %d · Running %s · Last %s" % (
                        self.settings.get("mode"), self.settings.get("model"),
                        (str(volume)[-40:] if volume else "none"),
                        status.get("inference", "?"), status.get("renderer", "?"),
                        status.get("owned", "?"),
                        len(runs),
                        running[0]["runId"] if running else "none",
                        ("%s (%s)" % (last["runId"], last["status"])) if last else "none"))
                self.call_from_thread(self._set_text, "status-bar", text)
            except Exception as error:
                self.call_from_thread(self._set_text, "status-bar", "dashboard failed: %s" % error)

        def _set_text(self, widget_id: str, text: str):
            # Static parses [..] as markup and drops unknown tags, so escape
            # every dynamic text (task text, paths, errors all reach here).
            try:
                self.query_one("#" + widget_id, Static).update(_escape_markup(text))
            except Exception:
                pass

        def _tick_live(self):
            if not self.live_dir or self.live_frozen:
                # No polling, but the window may have been resized: keep the
                # preview fitted so the whole frame stays visible.
                self._refresh_frame_display()
                return
            try:
                self._poll_live()
            except Exception:
                pass

        # -- live ---------------------------------------------------------
        def start_live(self, run_dir: str | Path, run_id: str | None = None):
            self.live_dir = Path(run_dir)
            self.live_run_id = run_id or self.live_dir.name
            self.live_tail = trace_mod.EventTail(self.live_dir, trace_mod.resolve_session_dir(self.live_dir))
            self.live_events = []
            self.live_frozen = False
            self._live_following = True
            self.live_image_path = None
            self.live_image_revision = None
            self._preview_key = None
            try:
                self.query_one("#live-head", Static).update("Live: %s" % self.live_dir.name)
            except Exception:
                pass
            # Follow the newest event until the user scrolls up.
            try:
                self.query_one("#events-table", DataTable).anchor()
            except Exception:
                pass

        def _poll_live(self):
            assert self.live_tail is not None and self.live_dir is not None
            # Re-resolve session dir once the launcher writes session.json.
            if self.live_tail.session_dir is None:
                found = trace_mod.resolve_session_dir(self.live_dir)
                if found:
                    self.live_tail.session_dir = found
            fresh = self.live_tail.poll()
            if fresh:
                self.live_events.extend(fresh)
            # Finished/failed freezes the view (no more polling).
            try:
                status = json.loads((self.live_dir / "run.json").read_text(encoding="utf-8")).get("status")
            except (OSError, ValueError):
                status = None
            if status in ("finished", "failed"):
                for _ in range(2):
                    extra = self.live_tail.poll()
                    if extra:
                        self.live_events.extend(extra)
                self.live_frozen = True
            self._render_live()
            try:
                self.query_one("#live-resources", Static).update(telemetry_mod.resource_line(self.live_dir))
            except Exception:
                pass

        def _filtered_events(self) -> list[dict]:
            items = self.live_events
            if self.live_filter != "ALL":
                items = [e for e in items if e.get("cat") == self.live_filter]
            if self.live_search:
                needle = self.live_search.lower()
                items = [e for e in items if needle in (e.get("title", "") + " " + e.get("detail", "")).lower()]
            return items

        @staticmethod
        def _at_bottom(table) -> bool:
            """True when the event table shows its last row."""
            try:
                return table.scroll_offset.y >= table.max_scroll_y - 1
            except Exception:
                return True

        def _sync_follow(self, table) -> None:
            """Follow the newest event while the timeline sits at the bottom.

            The anchor keeps the table pinned to the last row. Scrolling up
            releases it (Textual does that on its own), so the history stays
            readable; scrolling back to the bottom anchors it again.
            """
            at_bottom = self._at_bottom(table)
            if at_bottom and not self._live_following:
                self._live_following = True
                try:
                    table.anchor()
                except Exception:
                    pass
            elif not at_bottom and self._live_following:
                self._live_following = False
                try:
                    table.release_anchor()
                except Exception:
                    pass

        def _render_live(self):
            try:
                table = self.query_one("#events-table", DataTable)
            except Exception:
                return
            items = self._filtered_events()
            key = (self.live_filter, self.live_search)
            prev_len = getattr(self, "_live_table_len", 0)
            first = prev_len == 0
            key_changed = key != getattr(self, "_live_render_key", None)
            rebuild = key_changed or len(items) < prev_len
            try:
                if rebuild:
                    table.clear()
                    for i, event in enumerate(items):
                        table.add_row(str(i), _escape_markup(str(event.get("cat", ""))),
                                      _escape_markup(str(event.get("title", ""))[:100]))
                    self._live_render_key = key
                    self._live_table_len = len(items)
                    if key_changed and not first:
                        # A new filter/search starts at the first match and
                        # stops the follow until the view is back at the tail.
                        self._live_following = False
                        table.release_anchor()
                elif len(items) > prev_len:
                    # Incremental append: never clear, so scroll offset and the
                    # cursor row survive every refresh.
                    for i in range(prev_len, len(items)):
                        event = items[i]
                        table.add_row(str(i), _escape_markup(str(event.get("cat", ""))),
                                      _escape_markup(str(event.get("title", ""))[:100]))
                    self._live_table_len = len(items)
            except Exception:
                pass
            self._sync_follow(table)
            try:
                if self.live_frozen:
                    state = "[FROZEN]"
                else:
                    frame = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"[int(time.time() * 5) % 10]
                    state = "[live %s]" % frame
                self._set_text("live-head", "Live: %s events=%d %s" % (
                    self.live_dir.name if self.live_dir else "?", len(self.live_events), state))
            except Exception:
                pass
            # Newest frame preview.
            images = [e for e in self.live_events if e.get("image")]
            if images:
                newest = images[-1]
                self._show_frame(newest.get("image"), newest.get("revision"))
            else:
                self._refresh_frame_display()

        def _jump_to_latest(self) -> None:
            """Scroll the timeline to the newest event (the follow resumes)."""
            self._live_following = True
            try:
                self.query_one("#events-table", DataTable).anchor()
            except Exception:
                pass

        def _show_frame(self, image: str | None, revision=None):
            self.live_image_path = image
            self.live_image_revision = revision
            self._refresh_frame_display()

        def _preview_box(self) -> tuple[int, int]:
            """Cell box for the frame preview: (columns, rows) of the widget.

            The box follows the window width so a wide frame uses the whole
            panel; the row budget keeps the preview on one screen. The frame is
            fitted into this box, never cropped.
            """
            width = 0
            try:
                width = self.query_one("#live-image", Static).size.width
            except Exception:
                width = 0
            if width < 16:
                width = 80  # not laid out yet
            rows = LIVE_PREVIEW_MAX_ROWS
            try:
                screen_height = self.screen.size.height
                if screen_height:
                    rows = max(16, min(LIVE_PREVIEW_MAX_ROWS, screen_height - 14))
            except Exception:
                pass
            return (int(width), int(rows))

        def _refresh_frame_display(self) -> None:
            """Show the current frame: its path plus a preview of the whole PNG.

            The path is always visible so the PNG can be found on disk, and the
            preview is re-fitted whenever the window size changed.
            """
            image = getattr(self, "live_image_path", None)
            if image:
                revision = getattr(self, "live_image_revision", None)
                self._set_text("live-image-path", "Current frame PNG (revision %s): %s" % (
                    revision if revision is not None else "?", image))
            else:
                self._set_text("live-image-path", "Current frame PNG: (none yet)")
                self._preview_key = None
                try:
                    self.query_one("#live-image", Static).update("(no frame yet)")
                except Exception:
                    pass
                return
            columns, rows = self._preview_box()
            key = (str(image), columns, rows)
            if key == getattr(self, "_preview_key", None):
                return
            self._preview_key = key
            try:
                self.query_one("#live-image", Static).update(
                    imaging_mod.preview_or_message(image, width=columns, height=rows))
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
                self.notify("No frame PNG yet — wait for the first render.",
                            severity="warning", timeout=4, markup=False)
                return
            message = evidence_mod.open_path(str(png))
            self.notify(message, timeout=5, markup=False)

        def _open_live_folder(self) -> None:
            folder = self._live_frames_folder()
            if folder is None:
                self.notify("No folder to open yet — start or open a run first.",
                            severity="warning", timeout=4, markup=False)
                return
            message = evidence_mod.open_path(str(folder))
            self.notify(message, timeout=5, markup=False)

        # -- actions ------------------------------------------------------
        def action_command_palette(self):
            """Ctrl+P (and the header icon) opens the TUI palette.

            Textual binds ctrl+p to its own system palette; the binding above
            uses the "app.command_palette" name so Textual keeps its hands off
            it, and this override routes the key to the curated list.
            """
            self.action_commands()

        def action_commands(self):
            self.push_screen(CommandScreen(), callback=self._command_chosen)

        def _command_chosen(self, result):
            mapping = {
                "cmd-go-dashboard": "tab-dashboard", "cmd-go-newrun": "tab-newrun",
                "cmd-go-runs": "tab-runs", "cmd-go-live": "tab-live",
                "cmd-go-volumes": "tab-volumes", "cmd-go-services": "tab-services",
                "cmd-go-settings": "tab-settings",
            }
            if result in mapping:
                self._goto_tab(mapping[result])
            elif result == "cmd-live-jump":
                self._jump_to_latest()
            elif result == "cmd-live-stop":
                self._confirm_stop()
            elif result == "cmd-live-verify":
                self._verify_live()
            elif result == "cmd-live-open-png":
                self._open_live_png()
            elif result == "cmd-live-open":
                self._open_live_folder()
            elif result == "cmd-live-detach":
                self._detach_live()
            elif result == "cmd-runs-refresh":
                self._run_guarded("Refreshing runs", self._refresh_runs)
            elif result == "cmd-services-refresh":
                self._run_guarded("Refreshing services", self._refresh_services)
            elif result == "cmd-help":
                self.action_help()
            elif result == "cmd-quit":
                self.action_quit_confirm()

        def action_help(self):
            self.push_screen(HelpScreen())

        def action_toggle_dark(self):
            self.dark = not self.dark

        def _active_scroller(self):
            """The scroll container of the currently visible tab (if any)."""
            try:
                pane = self.query_one("#workspaces", TabbedContent).active_pane
                return pane.query_one(VerticalScroll)
            except Exception:
                return None

        def on_key(self, event) -> None:
            """Window scroll that works even when a text box owns the focus.

            TextArea/Input/Select consume arrows, Tab and the mouse wheel for
            themselves, so the parent scroll container would otherwise never
            scroll. Ctrl+Up/Down is consumed by nothing and always reaches here.
            """
            key = getattr(event, "key", "")
            if key == "ctrl+down":
                scroller = self._active_scroller()
                if scroller is not None:
                    scroller.scroll_relative(y=8, animate=False)
                    event.prevent_default()
            elif key == "ctrl+up":
                scroller = self._active_scroller()
                if scroller is not None:
                    scroller.scroll_relative(y=-8, animate=False)
                    event.prevent_default()

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
            """Run ids that are still running (from the table and the Live tab)."""
            ids = []
            for entry in self.runs:
                if runs_mod.is_live(entry) and entry.get("runId"):
                    ids.append(str(entry["runId"]))
            if self.live_run_id and self.live_run_id not in ids:
                ids.append(str(self.live_run_id))
            return ids

        def _quit_and_stop(self) -> None:
            """Leave no process behind, then exit.

            The agent run is stopped cooperatively first (the launcher still
            records interrupted/failure.json), everything that survives is
            killed, and the services this TUI started are stopped with it.
            """
            self._busy_inc()
            self._set_text("live-head", "Stopping the agent run, the local Ollama and the "
                                        "services this TUI started…")
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

        def _confirm_stop(self):
            if not self.live_run_id:
                return
            run_id = self.live_run_id
            self.push_screen(ConfirmScreen(
                "Stop run %s cooperatively? Evidence stays; launcher records interrupted." % run_id,
                on_yes=lambda: self._do_stop(run_id)))

        def _do_stop(self, run_id: str):
            try:
                runner_mod.request_stop(run_id)
                self._set_text("live-head", "Stop requested for %s — waiting for harness…" % run_id)
            except Exception as error:
                self._set_text("live-head", "Stop failed: %s" % error)

        def _verify_live(self, button=None):
            if not self.live_dir:
                self._set_text("live-verify", "Verify evidence: no run is open — open one from Runs first.")
                return
            run_dir = str(self.live_dir)
            self._run_guarded("Verifying evidence", lambda: self._run_verify(run_dir), button)

        def _run_verify(self, run_dir: str):
            try:
                result = runs_mod.run_verify(run_dir)
                lines = runs_mod.verify_summary(result.get("report"))
                text = "Verify evidence (read-only) for %s:\n%s" % (
                    Path(run_dir).name, "\n".join(lines[:6]))
                self.call_from_thread(self._set_text, "live-verify", text)
            except Exception as error:
                self.call_from_thread(self._set_text, "live-verify", "Verify evidence failed: %s" % error)

        def _detach_live(self) -> None:
            """Leave the Live view; the run keeps running in the background."""
            run_id = self.live_run_id
            self.live_dir = None
            self.live_run_id = None
            self.live_tail = None
            if run_id:
                self._set_text("live-head", "Stopped watching %s. The run keeps going until the TUI is "
                                            "closed (closing stops it) — open it again from Runs." % run_id)
            else:
                self._set_text("live-head", "Stopped watching. No run was open.")

        def _copy_run_json_log(self, button=None) -> None:
            """Copy the whole run's JSON log to the clipboard (read-only).

            The payload is one JSON document: the run's meta data, every
            event of the timeline and every JSON log file of the run folder.
            Base64 image bytes become a size note and files that could not
            be read are listed inside the payload — nothing is dropped
            silently.
            """
            if not self.live_dir:
                self.notify("No run is open — open one from Runs first.",
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
                self.notify(message + " — the terminal clipboard escape was used instead.",
                            severity="warning", timeout=6, markup=False)
                return
            self.notify(message, timeout=4, markup=False)

        # -- events -------------------------------------------------------
        def _goto_tab(self, tab_id: str) -> None:
            """Switch workspace tabs without focus snapping back.

            The pressed button lives in the tab being left; if it keeps focus
            while its pane hides, focus reassignment re-activates the old tab.
            Blur first, then switch.
            """
            try:
                self.set_focus(None)
            except Exception:
                pass
            try:
                self.query_one("#workspaces", TabbedContent).active = tab_id
            except Exception:
                pass

        async def on_button_pressed(self, event):
            bid = event.button.id
            button = event.button
            if bid == "btn-refresh":
                self._run_guarded("Refreshing dashboard", self._load_dashboard, button)
            elif bid == "btn-goto-newrun":
                self._goto_tab("tab-newrun")
            elif bid == "btn-goto-live":
                self._goto_tab("tab-live")
            elif bid == "btn-preflight":
                self._run_guarded("Preflight check", self._check_preflight, button)
            elif bid == "btn-start-run":
                # The gate probes endpoints and can take seconds: the lock is
                # taken here, on the UI thread, so a second click cannot start
                # a second run.
                if self._start_locked:
                    self.notify("A run is already starting — wait for it.",
                                severity="warning", timeout=4, markup=False)
                    return
                self._start_locked = True
                self.run_worker(self._start_run, thread=True)
            elif bid == "btn-runs-refresh":
                self._run_guarded("Refreshing runs", self._refresh_runs, button)
            elif bid == "btn-runs-follow":
                self._follow_selected_run(button)
            elif bid == "btn-runs-verify":
                self._verify_selected_run(button)
            elif bid == "btn-runs-rerun":
                self._rerun_selected_run()
            elif bid in ("btn-compare-a", "btn-compare-b"):
                self._mark_compare(bid)
            elif bid == "btn-compare-show":
                self._show_compare()
            elif bid == "btn-live-stop":
                self._confirm_stop()
            elif bid == "btn-live-verify":
                self._verify_live(button)
            elif bid == "btn-live-open-png":
                self._open_live_png()
            elif bid == "btn-live-open":
                self._open_live_folder()
            elif bid == "btn-live-detach":
                self._detach_live()
            elif bid == "btn-live-copy":
                self._copy_run_json_log(button)
            elif bid == "btn-slice":
                self._slice_preview(button)
            elif bid == "btn-volume-use":
                self._use_selected_volume()
            elif bid == "btn-svc-refresh":
                self._run_guarded("Refreshing services", self._refresh_services, button)
            elif bid == "btn-svc-start":
                self._run_guarded("Starting services", self._ensure_services, button)
            elif bid == "btn-svc-stop":
                self._run_guarded("Stopping owned services",
                                  lambda: self._release_services(False), button)
            elif bid == "btn-svc-stopall":
                self.push_screen(ConfirmScreen(
                    "Stop the agent run, the local Ollama this TUI started and the owned\n"
                    "services? The model unloads from VRAM; the run's evidence stays on disk.",
                    on_yes=lambda: self._run_guarded(
                        "Stopping everything", self._stop_everything_owned, button)))
            elif bid == "btn-svc-restart":
                self._run_guarded("Restarting services", self._restart_services, button)
            elif bid == "btn-settings-save":
                self._save_settings()
            elif bid == "btn-settings-reset":
                self._reset_settings()
            elif bid == "btn-h200-probe":
                self._run_guarded("Probing H200", self._probe_h200, button)

        async def on_input_changed(self, event):
            if event.input.id == "live-search":
                self.live_search = event.value.strip()
                self._render_live()
            elif event.input.id == "runs-filter":
                self._render_runs(event.value)
            elif event.input.id == "volumes-filter":
                self._render_volumes(event.value)

        async def on_select_changed(self, event):
            if event.select.id == "live-category":
                self.live_filter = str(event.value)
                self._render_live()
            elif event.select.id == "newrun-preset":
                preset = str(event.value)
                try:
                    prompt = self.query_one("#newrun-prompt", TextArea)
                    if preset == "task-a":
                        prompt.load_text(cfg.TASK_A)
                    elif preset == "task-b":
                        prompt.load_text(cfg.TASK_B)
                    elif preset == "custom":
                        prompt.load_text("")
                except Exception:
                    pass

        async def on_data_table_row_selected(self, event):
            if event.data_table.id == "events-table":
                items = self._filtered_events()
                if 0 <= event.cursor_row < len(items):
                    detail = items[event.cursor_row].get("detail", "")
                    self._set_text("live-detail", detail[:600])
                    if items[event.cursor_row].get("image"):
                        self._show_frame(items[event.cursor_row]["image"], items[event.cursor_row].get("revision"))
            elif event.data_table.id == "runs-table":
                self._show_run_detail(event.cursor_row)

        # -- runs tab ------------------------------------------------------
        def _refresh_runs(self):
            runs = self.discover_runs(fresh=True)
            self.runs = runs
            # Keep the active filter: the table and _selected_run must show
            # the same runs, or the row the cursor is on is another run.
            needle = ""
            try:
                needle = self.query_one("#runs-filter", Input).value
            except Exception:
                pass
            self.call_from_thread(self._render_runs, needle)

        def _render_runs(self, needle: str):
            try:
                table = self.query_one("#runs-table", DataTable)
            except Exception:
                return
            needle = (needle or "").lower()
            try:
                table.clear()
                for run in self.runs:
                    text = " ".join(str(run.get(k, "")) for k in ("runId", "status", "model", "task"))
                    if needle and needle not in text.lower():
                        continue
                    table.add_row(_escape_markup(str(run.get("runId", "?"))),
                                  _escape_markup(str(run.get("status", "?"))),
                                  _escape_markup(str(run.get("model", "?"))), str(run.get("frames")),
                                  _escape_markup(str(run.get("task", "?"))[:60]))
            except Exception:
                pass

        def _selected_run(self) -> dict | None:
            try:
                table = self.query_one("#runs-table", DataTable)
                row = table.cursor_row
            except Exception:
                return None
            # Recompute filtered order (same as _render_runs with current filter).
            try:
                needle = self.query_one("#runs-filter", Input).value.lower()
            except Exception:
                needle = ""
            visible = [r for r in self.runs
                       if not needle or needle in " ".join(str(r.get(k, "")) for k in ("runId", "status", "model", "task")).lower()]
            if 0 <= row < len(visible):
                return visible[row]
            return None

        def _follow_selected_run(self, button=None):
            run = self._selected_run()
            if not run:
                return
            self.start_live(run["directory"], run.get("runId"))
            self._goto_tab("tab-live")
            self._run_guarded("Following live", self._poll_live, button)

        def _verify_selected_run(self, button=None):
            run = self._selected_run()
            if not run:
                self._set_text("runs-detail", "Select a run row first, then press Verify evidence.")
                return
            run_dir = str(run["directory"])
            self._run_guarded("Verifying evidence", lambda: self._run_verify_into(run_dir), button)

        def _run_verify_into(self, run_dir: str):
            try:
                result = runs_mod.run_verify(run_dir)
                lines = runs_mod.verify_summary(result.get("report"))
                text = "Verify evidence (read-only) for %s:\n%s" % (
                    Path(run_dir).name, "\n".join(lines[:8]))
                self.call_from_thread(self._set_text, "runs-detail", text)
            except Exception as error:
                self.call_from_thread(self._set_text, "runs-detail", "Verify evidence failed: %s" % error)

        def _rerun_selected_run(self):
            run = self._selected_run()
            if not run:
                return
            try:
                prompt_widget = self.query_one("#newrun-prompt", TextArea)
                prompt_widget.load_text(runs_mod.task_text_of(run["directory"]) or "")
                preset = self.query_one("#newrun-preset", Select)
                preset.value = "custom"
                self._goto_tab("tab-newrun")
                self._set_text("newrun-preflight", "Rerun prepared from %s — review and Start." % run.get("runId"))
            except Exception:
                pass

        def _show_run_detail(self, row: int):
            run = self._selected_run()
            if not run:
                return
            numbers = runs_mod.summary_numbers(run)
            self._set_text("runs-detail", json.dumps(numbers, indent=1)[:800])

        def _mark_compare(self, bid: str):
            run = self._selected_run()
            if not run:
                return
            if bid == "btn-compare-a":
                self.compare_a = str(run["directory"])
            else:
                self.compare_b = str(run["directory"])
            self._set_text("runs-detail", "Comparison A = %s\nComparison B = %s\nPress 'Compare A vs B' to see the difference."
                           % (self.compare_a, self.compare_b))

        def _show_compare(self):
            if not (self.compare_a and self.compare_b):
                self._set_text("runs-detail", "Pick one run with 'Pick as A' and another with 'Pick as B' first.")
                return
            try:
                left = runs_mod.summary_numbers(runs_mod.find_run(Path(self.compare_a).name) or {"directory": self.compare_a})
            except Exception:
                left = {"directory": self.compare_a}
            try:
                diff = runs_mod.config_diff(self.compare_a, self.compare_b)
                left_img = runs_mod.final_image_of({"directory": self.compare_a, "verification": None, "sessionId": None})
                right_img = runs_mod.final_image_of({"directory": self.compare_b, "verification": None, "sessionId": None})
            except Exception as error:
                diff = ["compare failed: %s" % error]
                left_img = right_img = None
            self._set_text("runs-detail", "\n".join(diff[:40]))

        # -- new run --------------------------------------------------------
        def _check_preflight(self):
            try:
                preset = self.query_one("#newrun-preset", Select).value
            except Exception:
                preset = "task-a"
            settings = self._collect_newrun_settings()
            problems = flows_mod.service_gate(settings, env_mod, services_mod)
            text = "Preflight: %s" % ("OK — safe to start" if not problems else "%d blocker(s)" % len(problems))
            for item in problems[:6]:
                text += "\n[DOWN] %s %s" % (item.get("name"), (item.get("detail") or "")[:100])
            self.call_from_thread(self._set_text, "newrun-preflight", text)

        def _collect_newrun_settings(self) -> dict:
            settings = dict(self.settings)
            try:
                settings["model"] = self.query_one("#newrun-model", Input).value.strip() or settings.get("model", "")
                settings["renderBudget"] = int(self.query_one("#newrun-budget", Input).value.strip() or settings.get("renderBudget", 4))
                settings["contextSize"] = int(self.query_one("#newrun-context", Input).value.strip() or settings.get("contextSize", 16384))
                try:
                    settings["temperature"] = float(self.query_one("#newrun-temp", Input).value.strip() or settings.get("temperature", 0))
                except ValueError:
                    pass
                vol = self.query_one("#newrun-volume", Input).value.strip()
                if vol:
                    settings["volumePath"] = vol
            except Exception:
                pass
            return settings

        def _start_run(self):
            """Preflight and launch. The caller already holds the start lock."""
            self._start_locked = True
            self.call_from_thread(self._set_start_loading, True)
            self._busy_inc()
            self._launcher_result = {"settled": False}
            try:
                self._start_run_body()
            except Exception as error:
                self.call_from_thread(self._set_text, "newrun-preflight",
                                      "Start failed: %s" % error)
                self.call_from_thread(self._settle_launch)

        def _set_start_loading(self, loading: bool) -> None:
            """The Start button spinner is widget state: only the UI thread."""
            try:
                self.query_one("#btn-start-run", Button).loading = loading
            except Exception:
                pass

        def _start_run_body(self):
            try:
                preset = str(self.query_one("#newrun-preset", Select).value)
                prompt = self.query_one("#newrun-prompt", TextArea).text
            except Exception:
                preset, prompt = "task-a", ""
            settings = self._collect_newrun_settings()
            effective, problems = flows_mod.build_new_run(preset, prompt, settings, cfg)
            gate = flows_mod.service_gate(settings, env_mod, services_mod)
            offer = flows_mod.temporary_context_offer(settings, gate)
            if offer is not None:
                try:
                    want = cfg.example_config().get("contextSize", 16384)
                except Exception:
                    want = 16384
                self.call_from_thread(self._set_text, "newrun-preflight",
                                      "Starting temporary Ollama (context %s)…" % want)
                try:
                    started = services_mod.start_temporary_ollama(settings, want)
                except Exception as error:
                    started = {"ok": False, "error": str(error)}
                if started.get("ok"):
                    effective["endpoint"] = started["endpoint"]
                    gate = flows_mod.service_gate(dict(settings, endpoint=started["endpoint"]), env_mod, services_mod)
                    self.call_from_thread(
                        self._set_text, "newrun-preflight",
                        "Temporary inference for this run only: %s (stops on TUI exit)." % started["endpoint"])
                else:
                    self.call_from_thread(self._set_text, "newrun-preflight",
                                          "Temporary Ollama failed: %s. Fix your Ollama manually." % started.get("error"))
                    self.call_from_thread(self._settle_launch)
                    return
            problems = list(problems) + list(gate)
            if problems:
                self.call_from_thread(self._set_text, "newrun-preflight",
                                      "Blocked:\n" + "\n".join("- %s" % p if isinstance(p, str) else "- %s" % p.get("name") for p in problems[:8]))
                self.call_from_thread(self._settle_launch)
                return
            config_path = flows_mod.write_config(effective, preset, cfg)
            self.call_from_thread(self._set_text, "newrun-preflight", "Launching %s..." % config_path.name)
            thread = threading.Thread(target=self._launch_blocking, args=(str(config_path),), daemon=True)
            self._launcher_thread = thread
            thread.start()

        def _settle_launch(self) -> None:
            """Release the Start button + busy spinner exactly once per launch."""
            if self._launcher_result.get("settled"):
                return
            self._launcher_result["settled"] = True
            self._start_locked = False
            self._set_start_loading(False)
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
                self.call_from_thread(self._set_text, "newrun-preflight", "Launch failed: %s" % error)
            finally:
                if not self._launcher_result.get("directory") and not self._launcher_result.get("error"):
                    tail = (result.output_tail if result else "")[-1500:]
                    self.call_from_thread(
                        self._set_text, "newrun-preflight",
                        "Launch ended without reserving a run (config/duplicate/reservation). "
                        "No evidence touched.\n%s" % tail)
                self.call_from_thread(self._settle_launch)

        def _enter_live_from_launcher(self):
            directory = self._launcher_result.get("directory")
            if not directory:
                return
            self.start_live(directory, self._launcher_result.get("run_id"))
            self._goto_tab("tab-live")
            self._settle_launch()

        # -- volumes ---------------------------------------------------------
        def _render_volumes(self, needle: str):
            vols = self.volumes or self.discover_volumes()
            self.volumes = vols
            try:
                table = self.query_one("#volumes-table", DataTable)
            except Exception:
                return
            needle = (needle or "").lower()
            try:
                table.clear()
                for item in vols:
                    text = "%s %s" % (item.get("label", ""), item.get("path", ""))
                    if needle and needle not in text.lower():
                        continue
                    dims = "x".join(str(v) for v in (item.get("dimensions") or ["?"]))
                    flags = ("default " if item.get("default") else "") + ("+%d" % (item.get("copies", 1) - 1) if item.get("copies", 1) > 1 else "")
                    table.add_row(_escape_markup(str(item.get("path", ""))[-60:]),
                                  _escape_markup(dims), _escape_markup(flags))
            except Exception:
                pass

        def _selected_volume(self) -> dict | None:
            try:
                row = self.query_one("#volumes-table", DataTable).cursor_row
            except Exception:
                return None
            if 0 <= row < len(self.volumes):
                return self.volumes[row]
            return None

        def _slice_preview(self, button=None):
            item = self._selected_volume()
            if not item:
                self._set_text("volumes-detail", "Select a volume row first.")
                return
            try:
                axis = str(self.query_one("#slice-axis", Select).value)
                index = int(self.query_one("#slice-index", Input).value.strip() or "0")
            except Exception:
                self._set_text("volumes-detail", "Slice index must be an integer.")
                return
            header, want_axis, want_index = item["path"], axis, index
            self._set_text("volumes-detail", "Rendering slice %s=%d..." % (axis, index))

            def _work():
                result = volumes_mod.slice_preview_path(header, want_axis, want_index)
                if result.get("ok"):
                    self.call_from_thread(
                        self._set_text, "volumes-detail",
                        "slice %s=%d dims=%s" % (want_axis, want_index, result.get("dimensions")))
                    try:
                        image = imaging_mod.preview_or_message(result["image"], width=50, height=20)
                    except Exception as error:
                        image = "(preview failed: %s)" % error
                    self.call_from_thread(self._show_slice_image, image)
                else:
                    self.call_from_thread(
                        self._set_text, "volumes-detail",
                        "Slice unavailable: %s" % result.get("detail"))
            self._run_guarded("Slice preview", _work, button)

        def _show_slice_image(self, image: str) -> None:
            try:
                self.query_one("#slice-preview", Static).update(image)
            except Exception:
                pass

        def _use_selected_volume(self):
            item = self._selected_volume()
            if not item:
                return
            self.settings["volumePath"] = item["path"]
            try:
                cfg.save_settings(self.settings)
            except Exception:
                pass
            self._set_text("volumes-detail", "Volume selected: %s" % item["path"])

        # -- services ---------------------------------------------------------
        def _refresh_services(self):
            try:
                items = services_mod.owned_services()
            except Exception:
                items = []
            try:
                table = self.query_one("#services-table", DataTable)
                table.clear()
                for entry in items:
                    table.add_row(_escape_markup(str(entry.get("kind", ""))),
                                  _escape_markup(str(entry.get("pid", ""))),
                                  "alive" if entry.get("alive") else "stopped")
            except Exception:
                pass
            try:
                from rich.text import Text as _RichText
            except Exception:  # pragma: no cover
                _RichText = None
            try:
                log = self.query_one("#service-logs", RichLog)
                log.clear()
                for name, lines in services_mod.service_logs():
                    log.write("[bold]%s[/bold]" % name)
                    for line in lines[-15:]:
                        if _RichText is not None:
                            log.write(_RichText(line[:160]))
                        else:
                            log.write(line[:160])
            except Exception:
                pass

        def _ensure_services(self):
            try:
                report = services_mod.ensure_services(self.settings)
                self.call_from_thread(self._set_text, "live-head", "; ".join(services_mod.report_lines(report)[:4]) or "services OK")
            except Exception as error:
                pass
            try:
                # Keep the ownership record fresh: another TUI session may
                # only adopt our services once this heartbeat went stale.
                services_mod.heartbeat_start()
            except Exception:
                pass
            self._refresh_services()

        def _release_services(self, force: bool):
            try:
                if force:
                    report = services_mod.release_owned_services(force=True)
                else:
                    report = services_mod.release_owned_services()
                self.call_from_thread(self._refresh_services)
            except Exception:
                pass

        def _stop_everything_owned(self):
            """Stop the agent tree, the local Ollama and the owned services.

            Same order as the quit path (run -> agent tree -> owned services),
            but without the cached report, so the later quit still cleans up
            whatever was started meanwhile.
            """
            try:
                # A second Ctrl+C must not abort the stop half-way.
                self._on_ui(services_mod.block_interrupts)
            except Exception:
                pass
            try:
                report = services_mod.shutdown_all(self.settings, hard=False, cache=False)
                self.call_from_thread(
                    self._set_text, "live-head",
                    "; ".join(services_mod.shutdown_lines(report)[:4]) or "stopped")
            except Exception:
                pass
            self._on_ui(self._refresh_services)

        def _restart_services(self):
            try:
                services_mod.release_owned_services(force=True)
            except Exception:
                pass
            self._ensure_services()

        # -- settings ----------------------------------------------------------
        def _save_settings(self):
            try:
                for _label, widget, key, kind in SETTINGS_ROWS:
                    raw = self.query_one("#" + widget, Input).value.strip()
                    if kind == "int":
                        if raw:
                            self.settings[key] = int(raw)
                    elif kind == "float":
                        if raw:
                            self.settings[key] = float(raw)
                    else:
                        self.settings[key] = raw
                try:
                    self.settings["think"] = str(
                        self.query_one("#set-think", Select).value) == "true"
                except Exception:
                    pass
                problems = cfg.validate_settings(self.settings)
                if problems:
                    self._set_text("settings-status", "Invalid:\n" + "\n".join("- " + p for p in problems))
                    self.notify("Settings invalid: %s" % problems[0], severity="error", timeout=5, markup=False)
                    return
                cfg.save_settings(self.settings)
                self._set_text("settings-status", "Saved to runtime/tui-settings.json.")
                self.notify("Settings saved.", timeout=3, markup=False)
            except Exception as error:
                self._set_text("settings-status", "Save failed: %s" % error)
                self.notify("Save failed: %s" % error, severity="error", timeout=5, markup=False)

        def _reset_settings(self):
            """Fill the form with the built-in defaults. Writes nothing.

            The button label, its tooltip and the F1 help all promise that
            nothing is written here, so the file is only written by
            "Save settings". The settings in use stay untouched until then —
            the next run must not silently use the defaults.
            """
            defaults = dict(cfg.DEFAULTS)
            for _label, widget, key, _kind in SETTINGS_ROWS:
                self._set_input(widget, str(defaults.get(key, "")))
            try:
                self.query_one("#set-think", Select).value = (
                    "true" if defaults.get("think") else "false")
            except Exception:
                pass
            self._set_text("settings-status",
                           "Form filled with the built-in defaults. Nothing was written — "
                           "press Save settings to keep them.")
            self.notify("Form reset to defaults (not saved yet).", timeout=4, markup=False)

        def _set_input(self, widget_id: str, value: str) -> None:
            try:
                self.query_one("#" + widget_id, Input).value = value
            except Exception:
                pass

        def _probe_h200(self):
            import urllib.request
            try:
                endpoint = self.settings.get("h200Endpoint", "").rstrip("/")
                with urllib.request.urlopen(endpoint + "/api/version", timeout=5) as response:
                    version = json.load(response)
                self.call_from_thread(self._set_text, "settings-status", "H200 OK: %s" % str(version)[:120])
            except Exception as error:
                self.call_from_thread(self._set_text, "settings-status", "H200 DOWN: %s" % error)


    def main() -> int:
        """Crash-safe entry: no model call, non-TTY exits 2, Ctrl+C hard stop."""
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
            print("\nInterrupted — stopping agent, local Ollama and owned services…")
            return 130
        finally:
            # Every exit path (quit, Ctrl+C, crash, closed window) leaves no
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
