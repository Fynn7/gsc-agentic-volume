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
- Non-interactive shells exit 2 with guidance. `q` detaches (run keeps going);
  Ctrl+C is the hard stop (agent tree + local Ollama + owned services, confirmed).

## Workspaces (Ctrl+P commands, F1 help, t theme)

- **Dashboard**: mode/model/volume, Inference/Renderer status, owned services,
  Running/Last run. Refresh re-discovers without blocking the UI.
- **New Run**: Task preset (A/B loads audited text, Custom never inherits A/B),
  editable prompt, volume override, model/budget/context/temperature overrides,
  preflight check, Start. Service DOWN blocks before any config is written.
  A lone Model-context mismatch auto-starts a temporary TUI-owned Ollama on a
  free loopback port (this run only; saved settings untouched).
- **Runs**: filterable table (run/status/model/frames/task), Follow live
  (re-enters a running run from existing events), Replay, Verify, Rerun
  (pre-fills New Run), Open folder, dual-select Compare.
- **Live**: event timeline (category filter + text search + follow), selected
  event detail, current frame inline preview (Pillow → ANSI truecolor
  half-blocks) + external open, resource bar (`telemetry.jsonl` GPU/VRAM +
  Ollama context), Stop (cooperative `runtime/tui-control/<runId>.stop`,
  launcher records `interrupted`/`failure.json`), Verify, Open folder, Detach.
  Finish freezes the view (no more polling).
- **Volumes**: scan of the fixed `volumes/` folder (+ current selection),
  filter, metadata, Use selected (persists to `tui-settings.json`), Slice
  Inspector (x/y/z + index → inline preview for prepared volumes only;
  never converts, never consumes budget, never touches run evidence).
- **Services**: owned-service table (liveness re-verified), Start missing,
  Stop owned, Restart, Stop ALL (confirmed; unloads local model), Refresh,
  owned log tails. Foreign processes are never touched; release refuses while
  a run/launcher is live (except forced Ctrl+C path).
- **Settings**: mode/model/endpoints, contextSize, renderBudget, temperature,
  think, timeouts, maxOutputTokens. Validated, saved to
  `runtime/tui-settings.json`, H200 probe honest (never silent fallback).

## Backend contracts (unchanged semantics)

- `run_local_agent.py:prepare_run` mints IDs (atomic `mkdir`), `RunLock`
  duplicate protection (exit 3), `LATEST.txt`, `run_started` line. The TUI
  never reserves directories or writes locks.
- Cooperative stop: `runner.request_stop(runId)` writes
  `runtime/tui-control/<runId>.stop`; `execute()` polls every 0.5s while
  waiting on the harness, then `stop_harness` + `interrupted`/`failure.json`
  + exit 130 path. No stop file = legacy timeout/Ctrl+C behavior.
- Events: `trace_events.EventTail` tails harness.jsonl, run `session.json`
  (mtime/size gated), `runtime/<session>/events.jsonl` (seq gap-fill),
  http request/metadata (request summaries, no base64). All events carry `ts`.
- Verify: `runs.run_verify` subprocess + three-way summary
  (execution / image transport / visual-quality UNVERIFIED).
- Services: `services.ensure_services` (Local-loopback only, never H200,
  never `ollama pull`), ownership via `runtime/tui/services.json` + heartbeat,
  `release_owned_services` (refuses while live), `shutdown_all` (agent tree →
  local Ollama verified down → owned renderer, SIGINT-immune).
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
