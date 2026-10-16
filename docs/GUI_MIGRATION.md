# GUI migration — React frontend on `@fynns/ui`

Status: implemented and locally accepted (2026-10-16). The old single-file HTML GUI (`src/index.html`) is removed; `src/app.py` serves the built bundle only.

## Layout

- `frontend/` — React 19 + TypeScript + Vite, consuming `@fynns/ui` (`@fynn7/ui-design-core`, `file:` link to `../fynns_ui_design_core`).
  - `src/App.tsx` — shell (`ClippedNavShell` → `FillColumn` → `Chat`), session state machine, polling, dialogs, EndAside inspector.
  - `src/ChatRows.tsx` — chat row renderers (task, frames, decisions, pause, finish, error, live status, pause control).
  - `src/api.ts` — typed session/event client for `/api/*` plus the `/artifact/` URL mapping.
  - `src/app.css` — consumer-owned additions only (zoom-button reset; the `--fynns-layout-main-min-width` chat floor on `.gav-shell`).
- `src/app.py` — unchanged API; serves `frontend/dist` at `/`, static files confined to the dist directory, unknown routes and `/api/*` always answer JSON (no HTML fallback). `/artifact/` stays inside `runtime/`.
- `scripts/dev.ps1` — builds `frontend/dist` when missing (`-Rebuild` to refresh), then serves it.

## Behavior contract

- The composer is editable until a task starts; `+` sets the volume header path and the model button sets the Ollama model (default `qwen3.5:9b`) through core `Dialog`s. Sending validates path, model and non-empty task.
- While a task runs: composer busy, primary = Stop (except while a stop/continue request is pending), polling every 1.5 s with an in-flight guard and a session-id stale guard.
- Rows are keyed by event `seq`; each frame is bound to its producing event and scene revision (`Scene revision N · evidence: event M · sha256 … · task correctness unverified`). Model reasons/actions render in collapsed `ChatActivity`s; failure tracebacks in a `Collapsible`.
- Paused with budget left shows the single primary Continue control (resumes the same session and budget); stop-requested and resuming states are shown until the backend confirms.
- Transient request failures raise one error `snackbar` per contiguous failure streak and never remove prior recorded results. A frame that cannot load degrades to an inline warning while the recorded event/hash stay visible.
- Inspector (EndAside) shows read-only session state, budget, SceneSpec and volume facts. Frames zoom through a core `Dialog` (Esc close returns focus to the triggering frame).
- Width floors follow `llm/CHAT_COMPOSER_LAYOUT.md`: the shell host carries `--fynns-layout-main-min-width: 23rem` (composer 20rem + both dialog insets), so navigation auto-collapses before the chat column loses its minimum; the inspector sheets below 56.25rem.

## Local acceptance evidence

All runs use the local demo volume `C:\Users\fynna\AppData\Local\Temp\gav-demo\phantom.dat` (64³ structured phantom) and Ollama `qwen3.5:9b`.

| Path | Evidence |
| --- | --- |
| Full cycle: path → send → initial render → model decisions → new frames → model finish | `runtime/69460d36-e5c7-44b0-98b6-bc7c2bf54c22` (initial_render, 2 decisions, pause, 2 more decisions, finish; 4/4 budget) |
| Model finish after multi-angle inspection (4/4) | `runtime/92b0c9a3-a89e-4a14-b140-da8864d9a8a9` (2× render → pause → 2× render → decision 4 requested finish; aside `Finished — claim unverified`) |
| Decision budget exhausted | `runtime/bb485074-950d-41f6-8d44-1ee7b0404809` (4× render without finish; state `budget_exhausted`, final row "Decision budget exhausted — all 4 decisions used", no Continue row, composer disabled; screenshot `gav-acceptance-budget-exhausted.png`) |
| Real stop mid-model-call | `runtime/798d4dd5-4f96-4f1c-8d59-21b82f951023` (`error: Stopped during model call`, state `stopped`, info-severity status row, earlier frames kept) |
| Poll failure during a run | Server killed during session `090208e0-05c6-4190-98fb-1401971839c7`; exactly one `Could not refresh the session` snackbar across six failed polls, thread unchanged |
| Frame artifact missing | `frame-0/image.png` renamed → figure replaced by the "could not be loaded" warning, readback + hash stay |
| Routes | `/` → dist index; `/no-such-page` and `/api/unknown` → JSON 404; `/artifact/...` escapes → 404 |
| Widths | 1280 (nav + aside), 932 (opening the inspector auto-collapses nav; no composer overlap), 360 (nav collapsed, composer min 20rem, no clipping); screenshots `gav-desktop-aside.png`, `gav-360-landing.png` (gitignored) |
| Gates | `npm run build` (ui-export gate + tsc + vite), `npm run lint` (oxlint), `check-thinking-content.mjs frontend/src`, `install-as-npm.mjs --check` |

Reduced-motion behavior is owned by core (36 `prefers-reduced-motion` guards in the bundle); the consumer adds no motion of its own.
