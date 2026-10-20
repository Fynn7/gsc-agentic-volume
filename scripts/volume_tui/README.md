# Volume Agent TUI — Textual IDE-style interface

Control/display only over the Codex CLI. No agent loop lives here.
Evidence is read-only except new runs, `verification.json`, and `tui-settings.json`.

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
- Non-interactive shells exit 2 with guidance. `q` quits and stops every
  process the TUI owns (see "Leaving the TUI"); Ctrl+C is the hard stop and
  also stops a local Ollama the TUI did not start.

## Workspaces (Ctrl+P commands, F1 help, t theme)

Every button states its purpose in its label, carries a tooltip, and is
explained in the F1 help screen — stated once, never repeated on screen.

- **Dashboard**: mode/model/volume, Inference/Renderer status (`up` / `down` /
  `foreign service` / `remote (not probed)` — only loopback is probed), owned
  services, Running/Last run. Refresh status re-discovers without blocking the
  UI.
- **New Run**: Task preset (A/B loads audited text, Custom never inherits A/B),
  editable prompt, volume override, model/budget/context/temperature overrides,
  Check services & settings, Start agent run. Service DOWN blocks before any
  config is written. A lone Model-context mismatch auto-starts a temporary
  TUI-owned Ollama on a free loopback port (this run only; saved settings
  untouched).
- **Runs**: filterable table (run/status/model/frames/task), Open Live view
  (re-enters a running run from existing events), Verify evidence, Rerun as new
  run (pre-fills New Run), Pick as A/B + Compare A vs B (config diff).
- **Live**: event timeline (category filter + text search), selected
  event detail, resource bar (`telemetry.jsonl` GPU/VRAM + Ollama context), and
  the current frame: its **absolute PNG path** plus an inline preview (Pillow
  downsample to Rich half-block cells; Static never interprets raw ANSI
  escapes). The preview is fitted into the panel (aspect ratio kept, black
  padding, never cropped) and re-fitted when the window is resized, so the
  whole frame is visible instead of a cut-off corner.
  - The timeline **follows the newest event on its own** while it is scrolled
    to the bottom; scrolling up to read the history stops the follow, and
    scrolling back to the bottom resumes it (there is no Follow button).
    A new filter/search starts at the first match and stops the follow until
    the view is back at the tail.
  - Stop run (cooperative `runtime/tui-control/<runId>.stop`, launcher records
    `interrupted`/`failure.json`), Verify evidence (read-only check →
    `verification.json`, never calls the model), View PNG (opens the previewed
    PNG in the system image viewer), Open folder (opens the folder that holds
    the PNGs — `runtime/<sessionId>` — **not** the run log folder, which has no
    images), Copy JSON log (copies the whole run's JSON log to the clipboard:
    the run's meta data, every event and every JSON log file of the run folder
    — base64 image bytes are replaced by a size note, unreadable files are
    listed inside the payload), Stop watching (leave Live; the run keeps going,
    reopen it from Runs). Finish freezes the view (no more polling).
- **Volumes**: scan of the fixed `volumes/` folder (+ current selection),
  filter, metadata, Use this volume (persists to `tui-settings.json`), Slice
  Inspector (x/y/z + index → inline preview for prepared volumes only;
  never converts, never consumes budget, never touches run evidence).
- **Services**: owned-service table (liveness re-verified), Start missing
  services, Stop owned services, Restart owned, Stop all services (confirmed;
  stops the agent run, the local Ollama and the owned services — the model
  unloads from VRAM), Refresh status, owned log tails. Foreign processes are
  never touched; releasing the owned services refuses while a run/launcher is
  live (the forced paths — Ctrl+C and "Stop all services" — stop the agent
  tree first).
- **Settings**: mode/model/endpoints, contextSize, renderBudget, temperature,
  think, timeouts, maxOutputTokens. Validated, saved to
  `runtime/tui-settings.json`, H200 probe honest (never silent fallback).

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
  processes.
- The Dashboard shows the state read-only: `Inference up/down`,
  `Renderer up/down` and `Owned TUI-owned (…) / foreign/manual`.

## Leaving the TUI leaves no process behind

Every exit path (quit, Ctrl+C, crash, closed window) runs one shutdown:

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
stop the same process twice. Ctrl+C additionally stops every `ollama*`
process (the documented emergency path).

## Busy feedback (every waiting operation animates)

- A one-row spinner under the header animates while any background worker is
  active; the pressed button shows its own spinner and locks against
  double clicks; worker failures surface as error toasts.
- Covered: dashboard refresh, preflight, run start (button stays loaded until
  Live takes over), runs refresh/verify, live verify, live copy, slice preview,
  all service operations, H200 probe. The Live header animates while running
  and freezes on finish; the startup service check prints elapsed time.
- Static text and table cells escape literal `[...]` (terminal markup would
  otherwise swallow tags like `[FROZEN]`); toasts render with markup off.
- The frame preview is drawn at the panel width: a stale width would fold the
  picture row in half, so `FramePreview` re-fits the PNG whenever its own size
  changes (window resize, scrollbar).

## Backend contracts (unchanged semantics)

- `run_local_agent.py:prepare_run` mints IDs (atomic `mkdir`), `RunLock`
  duplicate protection (exit 3), `LATEST.txt`, `run_started` line. The TUI
  never reserves directories or writes locks.
- Cooperative stop: `runner.request_stop(runId)` writes
  `runtime/tui-control/<runId>.stop`; `execute()` polls every 0.5s while
  waiting on the harness, then `stop_harness` + `interrupted`/`failure.json`
  + exit 130 path. No stop file = legacy timeout/Ctrl+C behavior.
- Shutdown: `services.shutdown_all()` — cooperative stop of every live run
  (evidence complete) → hard kill of the agent tree (children snapshotted
  before the first kill, so orphaned MCP servers die too) → local Ollama
  (only the one this TUI started; Ctrl+C stops every `ollama*`) → owned
  services (`runtime/tui/services.json` + heartbeat, ownership re-verified).
  One run per process, cached report.
- Events: `trace_events.EventTail` tails harness.jsonl, run `session.json`
  (mtime/size gated), `runtime/<session>/events.jsonl` (seq gap-fill),
  http request/metadata (request summaries, no base64). All events carry `ts`.
- Run log: `runs.run_json_log` builds one JSON document out of the run
  directory (meta + every event + every JSON/JSONL log file, base64 image
  bytes replaced by a size note, unreadable files listed under `skipped`);
  `evidence.copy_to_clipboard` puts it on the system clipboard (PowerShell
  `Set-Clipboard` over stdin on Windows, `pbcopy`/`wl-copy`/`xclip` elsewhere,
  the terminal's own OSC 52 escape as the fallback). Read-only, nothing is
  written into the run folder.
- Verify: `runs.run_verify` subprocess + three-way summary
  (execution / image transport / visual-quality UNVERIFIED). The Live tab
  writes it to its own `Verify evidence:` box so it never overwrites the
  selected event detail.
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
services); `tests/test_volume_tui_app.py` covers new flows (cooperative stop,
timestamps, diffs, slices, imaging, Textual smoke via `run_test`). No test
starts a model, renderer, or network service. Run them with the TUI venv
Python (has NumPy/Pillow/Textual). With a bare Python lacking NumPy,
`test_capabilities`/`test_external_tools` fail at import (pre-existing,
untouched: they exercise `src/` which requires NumPy); the Textual smoke and
NumPy slice tests skip cleanly instead of erroring. Real acceptance is one live
Task A run: Live/Stop/Verify/Compare/Volumes/Services/Settings.

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
