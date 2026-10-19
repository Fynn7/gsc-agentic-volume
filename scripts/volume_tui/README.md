# Volume Agent TUI — interface survey (Stage 0) + status

## Stage 0 findings (read, not guessed)

- **Run ID / directory**: `scripts/run_local_agent.py:prepare_run` validates via
  `run_lifecycle.validate_config`, takes `RunLock(task_key)`, then
  `reserve_directory(base)` (atomic `mkdir(exist_ok=False)`). Prints
  `{"event":"run_started","runId","directory"}`. `resultDirectory` in the user
  config is a **base**; the effective per-run config rewrites it to the fresh
  child. Legacy bases that already hold evidence are redirected to a sibling
  (`resolve_run_base`). `LATEST.txt` points at the newest run. TUI reads the
  `run_started` line — it never mints IDs.
- **Duplicate protection**: one `runtime/locks/<taskKey>.lock` per logical task
  (`TASK_FIELDS`: harness/model/endpoint/rendererService/volumePath/task/
  contextSize). Second launcher exits **3** without touching evidence or
  killing anything (`run_lifecycle.RunLock`). Stale locks (dead pid) are
  reclaimed; live pid/harnessPid blocks. TUI delegates entirely (exit 3 shown
  verbatim).
- **PID / exit codes**: `run.json` keeps launcher pid + `harnessPid` once spawned;
  `manifest.json` keeps `pid`, `exitCode`, `timedOut`, `seconds/totalSeconds`.
  Exit codes: 0 done (semantic success still unverified), 1 run failure
  (+`failure.json`), 2 config/reservation (nothing created), 3 duplicate, 124
  harness timeout. TUI only reads these files.
- **agent_console reuse**: `ConsoleTrace` is importable (`result_directory`,
  `session_directory`, `poll_once`, `_lines` with trailing-partial-line buffering,
  `_seen_request/_seen_metadata` dedup, per-poll order harness→events→http).
  `scripts/volume_tui/trace_events.py:EventTail` reuses `_brief` + the same
  buffering/dedup/order; it yields structured dicts instead of printing, so
  there is exactly one event semantic. No base64 is loaded.
- **JSONL/HTTP ordering**: harness + `runtime/<session>/events.jsonl` are tailed
  incrementally (offset+buffer; shrink resets); corrupt lines skipped. HTTP
  `http-*.request.bin` arrival = "waiting", `http-*.metadata.json` arrival =
  result. Cross-source global sort is **not implemented** upstream (poll order
  wins); TUI documents the same limit.
- **Verifier**: `scripts/verify_local_agent.py <run>` (replay optional). Reads
  `manifest/session/events`, asserts PNG sha + scene/native-state/process exit,
  matches request `*.forwarded.bin` images by pixel hash → `actualFrames`,
  `agentRenders`, `visuallyDeliveredRevisions`, `state`, `final`. Exit nonzero on
  assertion failure; writes `verification.json`. TUI calls it as a subprocess.
- **Ports/services**: Ollama default `127.0.0.1:11434`, H200 tunnel
  `127.0.0.1:11435→server:11434`, renderer `127.0.0.1:8765` with
  `GET /api/service == gav-native-renderer`. `start-local-vlm.ps1` refuses
  non-loopback renderer, refuses cloud fallback, never kills a live service on
  context mismatch. CLI end leaves Ollama+renderer running for reuse; only the
  Codex child + recorder proxy exit.
- **PNG↔MCP↔revision linkage**: sufficient — `events.jsonl` external_tool_call
  (name+arguments) → external_tool_return (seconds) → render result
  (scene.revision, image path, nonblackPixels) + `session.json` events; HTTP
  forwarded bodies prove model delivery. `agent_console._render_event` already
  joins them; TUI shows the same chain.

## Not implemented (honest pending)

- Global cross-source timestamp merge, Textual frontend, terminal thumbnails as
  MVP gate, safe-cancel of a running harness (CLI has no verified API — TUI
  shows the limit and never kills), H200 field verification.

## Volume selection (fixed `volumes/` folder, not one fixed path)

- All local volume payloads live in the project-level **`volumes/`** folder
  (created on demand; gitignored). Discovery walks only that folder
  (subfolders allowed, `.git`/`.venv`/`__pycache__`/`node_modules` pruned) and
  offers every header the launcher can already use: prepared `render.dat`
  (+`volume.json`) and supported source headers (`.dat`/`.nhdr`/`.nrrd`).
  QVis and TU Wien binary DAT dimensions are read directly (binary DAT: 6-byte
  little-endian uint16 header).
- The currently selected path and the gate volume are added explicitly; the
  rest of the project tree (runtime session copies, experiments, prior configs)
  is no longer scanned. Manual entry still accepts any absolute/relative header
  path.
- Identical payloads (`volumeHash`) are collapsed into one row with a copy
  count, so copies do not flood the menu. QVis lz4 payloads are listed as
  unsupported by `describe` and omitted from discovery.
- Selecting a volume writes `volumePath` to `runtime/tui-settings.json` and is
  used by every new run; the run confirmation screen also offers
  **Change volume**. Existing runs keep their own `config.json`.
- Fallback remains `runtime/gate-volume/render.dat` when no selection exists.
  Preflight now checks the selected volume instead of a hard-coded default.
- Manual entry accepts any absolute/relative header path (the launcher still
  validates the payload; nothing is converted by the TUI).

## Layout

- `Start-VolumeTUI.ps1` — single entry (checks, isolated `runtime/tui/`, no model call).
- `volumes/` — local volume payloads (gitignored); the only folder discovery scans.
- `scripts/volume_tui/{config,env,runs,trace_events,runner,volumes,ui}.py` — control/display only.
- `tests/test_volume_tui.py` — non-recursive (launcher `execute` mocked; no Qwen call).

## Service lifecycle (auto-start on entry, auto-stop on exit)

- TUI entry runs `services.ensure_services`: missing **Local loopback**
  Ollama/Renderer are started detached (logs under `runtime/tui/`) and
  recorded in `runtime/tui/services.json` with session id + pid + image.
- Exit runs `services.release_owned_services`: only recorded, re-verified
  (pid alive + image name match) processes are terminated. Refused while any
  agent run (`Running`) or launcher lock is live — services stay up instead.
- **Ctrl+C is a hard stop** (`services.shutdown_all`): this TUI's
  launcher/harness/MCP process trees are killed first, then every local
  `ollama*` image (server, model runner, tray app) so the model is unloaded
  from VRAM, then the recorded renderer. A second Ctrl+C cannot abort cleanup
  (`SIGINT` is ignored during shutdown). The stopped Ollama endpoint is
  re-probed and reported as `verified down` or `STILL ANSWERING` — never
  assumed. Evidence directories are only read; nothing is deleted.
- Normal quit/Esc keeps a live agent running (its process tree is untouched);
  Esc in the live view closes the view only. Ctrl+C is the explicit stop-all.
- Never auto-started: H200 tunnel/endpoint, non-loopback URLs. Never
  downloaded: models (`ollama pull` stays manual). Never touched: foreign
  processes, harness children, another live TUI session (heartbeat-guarded).
- Temporary context instance: when the only Local blocker is a context
  mismatch on an otherwise reachable Ollama, the TUI offers to spawn a second
  TUI-owned `ollama serve` on a free 127.0.0.1 port with the required
  `OLLAMA_CONTEXT_LENGTH`. Only that run's in-memory config points at it
  (saved settings + your own Ollama untouched); it is recorded as
  `ollama-tmp-<port>` and reaped on TUI exit. Needs free VRAM for one more
  model load — otherwise fix your Ollama manually.

## Loading animation coverage

Every operation that can block repaints a live spinner instead of freezing the
screen (`screen.run_with_spinner`; one shared frame family with the live trace):

- startup: owned service start, preflight, temporary Ollama, run launch gate;
- menu refresh: cached inference/renderer check + run-history scan;
- History: run discovery, evidence reads, final-PNG lookup;
- Replay: event drain (read-only);
- Volumes: `volumes/` folder discovery and manual header inspection;
- Verify and H200 tunnel probe;
- exit: release of TUI-owned services (interactive terminals only).

Cached paths finish before the first frame, so routine menu redraws do not flash.

## Self-acceptance fixes (2026-10-18, no model run, evidence untouched)

1. `Start-VolumeTUI.ps1` used repo-relative paths — fails from any other cwd. Now absolute via `$repoRoot`.
2. Same script always probed the Local endpoint, even in H200 mode. Now checks the active endpoint.
3. `runner.launch` decoded harness output with the locale codec — non-ASCII model text could kill the pump thread. Now UTF-8/replace.
4. Ctrl+C anywhere produced a traceback. Now exits 130 with "agent keeps running".
5. No-console launch (piped/CI) hit `msvcrt` errors. Now exits 2 with guidance.
6. Every menu redraw ran live HTTP checks + full tree scan (multi-second hangs). Now 15s env cache + 5s discovery cache.
7. History showed `taskKey` hashes. Now Task A / Task B / Custom:+snippet from each run's own `config.json`.
8. Config filenames had 1s resolution — same-second launches overwrote each other. Now random token suffix.
9. Live view was tail-only (early events unreachable). Now Up/Down scrollback.
10. Image open always took the newest PNG. Now a chooser over all proven IMAGE events.
11. `turn.completed` with no prior `turn.started` printed fake `MODEL #0`. Now "turn count unknown".
12. Verify always used `sys.executable` (worker venv lacks NumPy). Now falls back to the Codex bundled interpreter.

Verified: 16 TUI + 32 lifecycle tests green; real-history probe labels Task A; `git status` shows only the three new paths.

## Ctrl+C hard stop (2026-10-19)

Reported: Ctrl+C left the harness running and Ollama still held the model.
Cause: `main()` only did a soft release in `finally`, and that release refused
to stop services while a run still looked `Running`; a second Ctrl+C during
that release aborted it with a traceback.

1. `ui.main` now handles `KeyboardInterrupt` first with `services.shutdown_all`.
2. `_terminate_agent_trees` kills the tracked launcher (`runner.stop_active`,
   `/T /F`), then live descendants and lock/manifest harness PIDs — so codex
   and its renderer-MCP children stop requesting inference before Ollama dies.
3. `terminate_local_inference` kills every local `ollama*` process (including
   a reused instance that predated the TUI) and re-probes `/api/version` for
   up to 5s, reporting `verified down` only when the endpoint is gone.
4. `release_owned_services(force=True)` stops the recorded renderer without
   the "live run" refusal that previously kept everything alive.
5. `SIGINT` is ignored inside shutdown, so repeated Ctrl+C cannot interrupt
   the kill sequence. `run_local_agent.stop_harness` does the same for its
   harness child and records `failure.json` with `status=interrupted`.
6. Esc / menu quit keep their old semantics: close the view/TUI, leave a live
   agent alone.

