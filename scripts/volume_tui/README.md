# Volume Agent TUI — a coding agent for one volume

One screen, like a coding agent app (Codex CLI, OpenCode): the task at the
top, then the model's thinking, its tool calls, the frames it rendered and its
answer. Control/display only over the Codex CLI — no agent loop lives here.
Evidence is read-only except new runs, `verification.json`,
`tui-settings.json` and the downloaded chat logs (your Downloads folder).

## Entry

```powershell
.\Start-VolumeTUI.ps1
```

- Finds Python (bundled Codex runtime preferred, needs NumPy/Pillow).
- Bootstraps `runtime/tui-venv` (bundled Python `--system-site-packages` + pinned
  `textual==8.2.8` from `scripts/volume_tui/requirements.txt`). Prompts once,
  records a hash in `runtime/tui/venv-requirements.sha256`; re-runs are offline.
  Set `$env:GAV_TUI_WHEELHOUSE` for offline installs; `-NoBootstrap` fails fast.
- Prints one-line settings/mode/model, probes inference + renderer (read-only),
  then runs `python -B scripts/volume_tui` inside the venv.
- The TUI itself starts the missing **Local loopback** services on entry (see
  "Service lifecycle"); it never downloads a model and never calls one.
- Non-interactive shells exit 2 with guidance. `Ctrl+Q` quits and stops every
  process the TUI owns (see "Leaving the TUI"); the hard stop (`/hard-stop`)
  also stops a local Ollama the TUI did not start. `Ctrl+C`
  copies the selected text — the terminal default, not a hard stop.

## The screen (one screen, no workspaces)

```
 ● volume-agent  qwen3.6:35b  ·  volume 246x246x221 voxels  ·  run-20261019-131431  ·  running  ·  2 of 4 observations used
 ▌ Set the camera rotationXYZ to [25,-35,10] degrees.
 ✓ Model answered  in 12278, out 96 tokens
 ● Inspected the scene  camera, opacity window, crop box, current picture · read-only  ✓ 0s
 ● Rendered the frame  camera rotation 25,-35,10 · a new PNG of the volume · updated the scene  ✓ 1s
   » Rotate the camera as the task asks.
   the camera turns 25° around X, then -35° around Y, then 10° around Z
 ▣ frame revision 1 · 148491 lit px (56.6% of the picture) · 512x512
   D:/…/runtime/<session>/frame-1/image.png
   [the picture, inline]
 » The camera is now rotated to [25,-35,10] …
 ✓ Run finished · picked frame revision 1 · task check: unverified
   » The camera is rotated.
```

- The **status line** names the model, the volume, the open run, its state and
  how much of the observation budget is already spent and what is left. It carries the spinner
  while a worker runs.
- The **screen and the backend run on their own clocks**: the screen ticks
  every 0.1s (the spinner, the seconds) and the evidence is read in a worker
  thread every 0.5s, so a slow backend never freezes the animation.
- The **flow diagram** of the whole pipeline (TUI, launcher, renderer service,
  agent, evidence) is in `docs/agent-prompt-to-completion.html`, with its
  source (`docs/agent-prompt-to-completion.workflow.json`) next to it.
  The agent-failure analysis and improvement plan (tool changes, evaluation
  protocol, worker plan) live in `docs/improvement/00-README.md`, with the
  local-evidence recheck in `docs/improvement/08-repository-recheck.md`.
- **Whose words are these?** `▌` is your task, `»` is everything the model
  itself wrote (its answer, its reason for a call, its claim at the end),
  and `● ▣ ⚠ ✓` are facts the screen measured or recorded. The screen never
  writes a `»` line, so the two can never be mixed up.
- The **conversation** is the whole live view: the task, the model turns, the
  tool calls with the numbers they changed, the frames (the picture is inline,
  fitted into the panel, never cropped) and the answer. Every line says what
  happened — the wording lives in `transcript.py`, `event_text.py` and
  `app.py`.
- Every row is **facts, not labels**: what went in, what came out, what
  changes ("Rendered the frame  camera rotation 25,-35,10 · a new PNG of the
  volume · updated the scene"). No parentheses of explanation, no "what that
  does:" in front of the numbers — the numbers say themselves. The raw tool
  names are not on the rows either; the glossary (F2) names them.
- The **transport is hidden**: the model calls and the HTTP status are written
  to the evidence but are not shown in the conversation. The harness notices
  (an under-development feature, a missing model description) are one quiet
  row instead of a wall of jargon.
- The **model turn** line says **Thinking** / **Thought** when the model
  really reasoned, and **Working** / **Model answered** when it did not —
  the Think setting is off, and the screen never claims reasoning that the
  model did not produce. **Only reasoning opens**: the foldable block exists
  when the model reasoned, and the words the model wrote before a tool call
  join that reasoning. Without reasoning there is nothing to open, so the
  words stay a `»` line of the model's own and the turn line is a plain
  fact row.
- The **answer is streamed**: the recorded model stream
  (`http/http-NNN.response.bin`, the raw SSE the launcher recorded while the
  model was still answering) is tailed, so the text appears word by word.
- The **composer** at the bottom starts the next task: Enter sends it,
  Shift+Enter adds a line. While the agent works the **Send button turns into
  a red Stop** — it stops the run the same way Esc does. One run at a time:
  a new task waits until the current run is over.
- The **footer** lists the keys.

### Keys

| Key | What it does |
| --- | --- |
| Enter | send the composer text: it starts a new run |
| Ctrl+Up / Ctrl+Down | scroll the conversation (the composer keeps the cursor) |
| Esc | stop the running agent (the evidence already written stays) |
| / + Up/Down/Tab/Enter/Esc | the slash menu: filter, move, complete, run, close |
| F1 | help |
| F2 | the glossary: what the words of the screen mean |
| Ctrl+T | light / dark |
| Ctrl+Q | quit (asks first while a run is live) |
| Ctrl+C | copy the selected text (the terminal default — not a hard stop) |

The **Send button becomes a red Stop** while the agent works; it stops the
run the same way Esc does.

The conversation **follows the newest entry on its own** while it is scrolled
to the bottom; scrolling up to read the history stops the follow, scrolling
back to the bottom resumes it (there is no Follow button).

### What "stop" really stops

- Esc (or the red **Stop** button) writes the stop marker; the launcher
  notices it within half a second and kills the **agent tree** — the harness
  and its MCP children. The run records `interrupted` + `failure.json`, the
  evidence stays complete.
- A stop that arrives during the start-up phase (the first render) never
  starts the agent at all.
- The **local Ollama and the renderer are services**: they keep running, so
  the model may still finish the request that was in flight. They are
  stopped when the TUI quits (Ctrl+Q) or with the hard stop.
- The status line says `stopping` right away and `stopped` when the run has
  ended; the Thinking block stops spinning and the conversation says how the
  run ended.

### Slash commands (type / in the composer, like opencode)

| Slash | What it does |
| --- | --- |
| /new | clear the open run and refresh, ready for the next task (`/new <task>` starts it at once; never stops a live run) |
| /task-a, /task-b | start the audited preset prompts |
| /verify | read-only check → `verification.json` |
| /copy-json | the run's meta data, every event and every JSON log file |
| /copy-chat | the conversation as plain text: the task, the model's thinking, the tool calls, the frames and the answer |
| /export-chat | the same conversation downloaded to your Downloads folder as `<runId>-<time>.log` and the folder opened (the run folder stays read-only) |
| /export-images | every frame PNG of this run zipped to your Downloads folder as `<runId>-images-<time>.zip` and the folder opened (the run folder stays read-only) |
| /open-image | opens the previewed PNG in the system viewer |
| /open-folder | opens the folder that holds the PNGs (`runtime/<sessionId>`) |
| /history | picks one of the runs on this machine and opens it here |
| /archive-all | moves every history run to `runtime/archive` (live runs are kept; no unarchive — move folders back manually) |
| /change-volume | picks the volume the next run works on; writes `runtime/tui-settings.json` |
| /settings | the form that writes `runtime/tui-settings.json` — model, mode, the volume the agent works on, endpoints, context size, render budget, temperature, the Think toggle and the Debug-windows toggle (off hides console/GLFW popups; on keeps them for debugging; `GAV_DEBUG_WINDOWS=1` overrides) |
| /theme, /help, /glossary | light or dark, the help screen, the glossary |
| /hard-stop | agent tree, local Ollama, owned services — then leave |
| /quit | stops what the TUI owns, then leaves (asks first while a run is live) |

Type `/` to list every command in a menu (Up/Down moves, Tab completes,
Enter runs, Esc closes, click runs). Filtering is fuzzy like VSCode
(`/rify` hits `/verify`, a wrong char like `/var` hits nothing).
There is no Ctrl+P shortcut.
Every menu entry carries the same tooltip as the retired modal palette.

### What was removed

The old workspaces (Dashboard, New Run, Runs, Live, Volumes, Services,
Settings) are gone. What they did lives in the coding agent screen:

- **Dashboard** → the status line.
- **New Run** → the composer (a free task) plus the audited presets as
  slash commands (`/task-a`, `/task-b`).
- **Runs** → *Runs: open another run* (a dialog, not a page).
- **Live** → the conversation itself.
- **Volumes / Services** → not part of a coding agent app: the volume is
  chosen in the settings dialog (the volumes this machine offers), and the
  services are started on entry and stopped on exit.
- **Settings** → the settings dialog behind one command.

## Service lifecycle (auto-start on entry, auto-stop on exit)

- TUI entry runs `services.ensure_services`: missing **Local loopback**
  Ollama/Renderer are started detached (logs under `runtime/tui/`) and recorded
  in `runtime/tui/services.json` with session id + pid + image.
- The session keeps that record fresh with a **heartbeat** (30 s tick, 90 s
  TTL). Another TUI session may only adopt the services once this heartbeat
  went stale — a live session's services are never taken away from it.
- Exit runs `services.release_owned_services`: only recorded, re-verified
  (pid alive + image name match) processes are terminated. Refused while any
  agent run (`Running`) or launcher lock is live — services stay up instead.
- Never auto-started: H200 tunnel/endpoint, non-loopback URLs. Never
  downloaded: models (`ollama pull` stays manual). Never touched: foreign
  processes — except the local `ollama*` of a Local-mode run, which the
  shutdown below stops (an H200 endpoint is remote and is never touched).
- The status line shows the state read-only: `Inference up/down`,
  `Renderer up/down` and `Owned TUI-owned (…) / foreign/manual`.

## Leaving the TUI leaves no process behind

Every exit path (quit, hard stop, crash, closed window) runs one shutdown:

1. **Cooperative stop**: `runtime/tui-control/<runId>.stop` is written for
   every run that has not recorded an end. The launcher polls it, records
   `interrupted` + `failure.json`, and exits — the evidence stays complete.
2. **Hard kill**: the child processes are snapshotted *before* the first
   kill, so a child that outlives its parent (the codex MCP server) is
   stopped as well. Launcher, harness, MCP children and renderer go.
3. **Services**: the renderer and the Ollama this TUI started are stopped
   (ownership re-verified by pid + image name).
4. **Report**: `[stop requested] / [stopped] / [kept] / [note] / [failed]`
   lines say exactly what happened. A local Ollama the TUI did *not* start is
   left alone and reported as such.

The result is cached, so the quit path and the safety net in `main()` never
stop the same process twice. `main()`'s safety net runs on **every** exit
path (quit, hard stop, crash, closed window) and stops the agent tree, the
local Ollama and the owned services — with `hard=True`, so in Local mode it
also stops every `ollama*` process, including one that predates this TUI (an
H200 endpoint is remote and is never touched). `Ctrl+C` is the terminal's
copy key and does not stop anything — use Ctrl+Q, the hard stop, or close
the window.

## Busy feedback (every waiting operation animates)

- The status line carries a spinner while any background worker is active;
  the pressed button shows its own spinner and locks against double clicks;
  worker failures surface as error toasts.
- Covered: preflight, run start, verify evidence, copy log, chat log, settings
  probe.
  The status line animates while the model thinks; the startup service check
  prints elapsed time.
- Static text and table cells escape literal `[...]` (terminal markup would
  otherwise swallow tags like `[FROZEN]`); toasts render with markup off.
- The frame preview is drawn at the picture's own size: a stale width would
  fold the picture row in half, so the preview is re-fitted whenever the
  window is resized.

## Backend contracts (unchanged semantics)

- `run_local_agent.py:prepare_run` mints IDs (atomic `mkdir`), `RunLock`
  duplicate protection (exit 3), `LATEST.txt`, `run_started` line. The TUI
  never reserves directories or writes locks.
- Cooperative stop: `runner.request_stop(runId)` writes
  `runtime/tui-control/<runId>.stop`; the launcher checks that file **before
  the agent starts** (a stop during the start-up phase never starts the
  harness) and every 0.5s while it waits on the harness, then
  `stop_harness` + `interrupted`/`failure.json` + exit 130 path. No stop file =
  the launcher waits until the harness exits on its own (runs have no
  timeout). The TUI says `stopping` at once and `stopped` when
  the run has ended.
- Shutdown: `services.shutdown_all()` — cooperative stop of every live run
  (evidence complete) → hard kill of the agent tree (children snapshotted
  before the first kill, so orphaned MCP servers die too) → local Ollama
  (only the one this TUI started; the hard stop kills every `ollama*`) →
  owned services (`runtime/tui/services.json` + heartbeat, ownership
  re-verified). One run per process, cached report.
- Events: `trace_events.EventTail` tails harness.jsonl, run `session.json`
  (mtime/size gated), the live budget from `runtime/<session>/session.json`
  (the run's own copy only lands when the run ends), `runtime/<session>/
  events.jsonl` (seq gap-fill), http request/metadata (request summaries, no
  base64). The session resolves while the run is live: run `session.json`
  id, then live `run.json` `sessionId` (written right after `/api/tools/
  open`), then the launcher lock file (`runtime/locks/*.lock`, matched by
  run id or directory). All events carry `ts` and a `kind` plus the raw
  payload the transcript needs. The kinds are `thinking_start`,
  `thinking_end`, `thinking`, `assistant`, `assistant_update`, `tool_call`,
  `tool_result`, `tool_error`, `image`, `notice`, `warn`, `error`, `finish`,
  `meta` (from the harness and the service log) and `text_delta`,
  `reasoning_delta`, `message_done`, `done` (from the recorded model stream,
  each tagged with its model call `call`, its poll time `ts`, and
  `compaction` when the recorded request body carries the context
  checkpoint marker).
- Transcript: `transcript.Transcript` folds the events into the blocks the
  screen shows. The service log is the authoritative source of a tool call,
  the model stream and the harness are the fallback — a call that several
  sources see is shown once, and the answer that the stream and the harness
  both report is written once. One model call gets one Thinking block (the
  block closes when the call's tool call arrives or the next call streams);
  the model's own words keep their order because the stream folds in before
  the service/harness evidence. A context checkpoint compaction is not an
  answer: it gets its own `compaction` row (◈, foldable, names the
  observations left) instead of a » line.
- Model stream: `stream.StreamTail` tails the recorded SSE responses
  (`http/http-NNN.response.bin`, written chunk by chunk while the model
  answers). The words the model writes **before** a tool call are its
  thinking and move into the Thinking block; the words after the call are the
  answer. `stream.SseParser` keeps the state of the turn between the chunks.
- Run log: `runs.run_json_log` builds one JSON document out of the run
  directory (meta + every event + every JSON/JSONL log file, base64 image
  bytes replaced by a size note, bulk number arrays — the 65536 bins of the
  volume histogram — replaced by a size note in their place, unreadable files
  listed under `skipped`); `evidence.copy_to_clipboard` puts it on the system
  clipboard (PowerShell `Set-Clipboard` over stdin on Windows,
  `pbcopy`/`wl-copy`/`xclip` elsewhere, the terminal's own OSC 52 escape as
  the fallback). Read-only, nothing is written into the run folder.
- Chat log: `chat_log.chat_log_text` folds the transcript blocks into the
  conversation as plain text — the same marks as the screen (`▌` the task,
  `»` the model's words, `● ▣ ⚠ ✓` the facts) and the same rows
  (`transcript.MARKS`, `transcript.thinking_row_parts`,
  `transcript.tool_row_parts`): the screen paints the roles, the log writes
  them. The log opens what the screen folds away: the reasoning chain and
  the harness warnings (a file has no keys). The transport (the model calls,
  the HTTP status) stays out: it belongs to the JSON log. Every row carries a
  time — the time of the step when the evidence knows it (the task, the tool
  calls, the frames), the moment the TUI read it otherwise (the model's own
  words: the harness log has no timestamps).
  `chat_log.export_chat_log` downloads it to
  your Downloads folder as `<runId>-<time>.log`, one file per export; the run
  folder is never written to.
- Image export: `export_images.collect_run_images` gathers every frame PNG
  (session `frame-*/image.png`, run-folder fallback, session.json event
  paths); `export_images.export_run_images_zip` zips them to your Downloads
  folder as `<runId>-images-<time>.zip`, one file per export; the run
  folder is never written to.
- Verify: `runs.run_verify` subprocess + three-way summary
  (execution / image transport / visual-quality UNVERIFIED).
- Services: `services.ensure_services` (Local-loopback only: the H200 tunnel is
  probed read-only and never started, never `ollama pull`), ownership via
  `runtime/tui/services.json` + heartbeat,
  `release_owned_services` (refuses while a live run needs them),
  `shutdown_all` (agent tree → local Ollama → owned services, SIGINT-immune).
- Volumes: `volumes.discover_volumes` scans only `volumes/` (+ selection/gate),
  collapses identical `volumeHash`, rejects lz4; `slice_preview_path` renders
  read-only PNGs under `runtime/tui/slices`.

## Tests

```powershell
runtime\tui-venv\Scripts\python.exe -B -m unittest discover -s tests -p "test_*.py" -v
runtime\tui-venv\Scripts\python.exe -B -m compileall -q scripts\volume_tui
```

`tests/test_volume_tui.py` covers backend (config/discovery/lifecycle/trace/
services); `tests/test_transcript.py` covers the coding-agent transcript
(pure functions, no run needed); `tests/test_event_text.py` covers the wording
of every row (the same pure functions); `tests/test_stream.py` covers the
recorded model stream (SSE parsing and the live tail);
`tests/test_run_lifecycle.py` covers the launcher, the stop before the agent
starts and the evidence; `tests/test_chat_log.py` covers the chat record as a
log (the plain text, the marks, the export); `tests/test_volume_tui_app.py`
covers the screen (the conversation, the thinking chain, the composer, the
dialogs, the busy feedback). No test starts a model, renderer, or network
service. Run them with the TUI venv Python (has NumPy/Pillow/Textual). With a
bare Python lacking NumPy, `test_capabilities`/`test_external_tools` fail at
import (pre-existing, untouched: they exercise `src/` which requires NumPy);
the Textual smoke and NumPy slice tests skip cleanly instead of erroring.
Real acceptance is one live Task A run: send a task, watch the conversation,
open the thinking chain, copy the chat log, stop, verify, switch runs.

### CI

`.github/workflows/tests.yml` runs the same two commands above on every push
and on every pull request — with the runner's Python, because the TUI venv
cannot exist on a fresh checkout. Windows runner (the TUI, the launcher and
the C++ raycaster are Windows-only), Python 3.12, the pinned dependencies from
`scripts/volume_tui/requirements.txt` (textual) and
`scripts/volume_context/requirements.txt` (NumPy/Pillow), pip cache on the
requirement files, 15-minute timeout.

The scene validation hashes the compiled `Raycaster.exe`; without the C++
toolchain the test points that hash at a stub file, so the validation stays
covered. Building the raycaster itself remains a local step
(`scripts/build-raycaster.ps1`, Visual Studio Build Tools 2026).
