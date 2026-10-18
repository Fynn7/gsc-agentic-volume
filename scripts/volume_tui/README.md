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

## Layout

## Layout

- `Start-VolumeTUI.ps1` — single entry (checks, isolated `runtime/tui/`, no model call).
- `scripts/volume_tui/{config,env,runs,trace_events,runner,ui}.py` — control/display only.
- `tests/test_volume_tui.py` — non-recursive (launcher `execute` mocked; no Qwen call).

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
