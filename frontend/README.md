# Agentic Volume Visualization — frontend

React 19 + TypeScript + Vite UI for the local harness in the parent repo. It consumes the fynns design system from the sibling checkout `../fynns_ui_design_core` (`@fynn7/ui-design-core`, `file:` link, Vite alias `@fynns/ui`). The Python harness serves `dist/`; see `../docs/GUI_MIGRATION.md`.

## Commands

- `npm run dev` — Vite dev server on 5177 plus the Python harness on `127.0.0.1:8765` in one terminal (`/api` and `/artifact` proxy to it; honors `GAV_PYTHON`/`GAV_PORT`). A backend already on that port is reused. The Vite port honors `GAV_VITE_PORT` and fails fast when occupied (no silent port hopping). Use `dev:vite` / `dev:backend` to run either side alone.
- `npm run build` — ui-export gate + `tsc -b` + production build into `dist/`.
- `npm run lint` — oxlint.
- `npm run fynns-ui:check-exports` — standalone ui-export gate against this app.
- `npm run fynns-ui:check-update` — advisory core-version check.
- `npm run wasm:build` — one-shot Wasm engine build from `VirtualSampling/Raycaster` (`make emscripten_release`) synced to ignored `public/wasm/` (`sampling.js/.wasm/.data`; `preview-shell.html` bridge is committed). Never runs on `dev`/`build`; the stage shows a build prompt when assets are missing. `em++` is auto-activated from `%USERPROFILE%/emsdk` when not on `PATH`; use `npm run wasm:sync -- --sync-only` to copy an existing `web/` without rebuilding.

The `pre*` and `postinstall` hooks run `scripts/fynns-ui-bootstrap.mjs`, which syncs/prepares the linked core. When the sibling core checkout must stay read-only, run npm with `FYNNS_UI_SKIP_SIBLING_SYNC=1` (PowerShell: `$env:FYNNS_UI_SKIP_SIBLING_SYNC='1'`).

Styling comes from `@fynns/ui` primitives and `var(--fynns-*)` tokens only; `src/app.css` carries consumer-owned additions (zoom-button reset and the shell chat-width floor).
