# Agentic Volume Visualization — frontend

React 19 + TypeScript + Vite UI for the local harness in the parent repo. It consumes the fynns design system from the sibling checkout `../fynns_ui_design_core` (`@fynn7/ui-design-core`, `file:` link, Vite alias `@fynns/ui`). The Python harness serves `dist/`; see `../docs/GUI_MIGRATION.md`.

## Commands

- `npm run dev` — Vite dev server on 5173, proxying `/api` and `/artifact` to the Python harness at `127.0.0.1:8765` (start `python ../src/app.py` separately).
- `npm run build` — ui-export gate + `tsc -b` + production build into `dist/`.
- `npm run lint` — oxlint.
- `npm run fynns-ui:check-exports` — standalone ui-export gate against this app.
- `npm run fynns-ui:check-update` — advisory core-version check.

The `pre*` and `postinstall` hooks run `scripts/fynns-ui-bootstrap.mjs`, which syncs/prepares the linked core. When the sibling core checkout must stay read-only, run npm with `FYNNS_UI_SKIP_SIBLING_SYNC=1` (PowerShell: `$env:FYNNS_UI_SKIP_SIBLING_SYNC='1'`).

Styling comes from `@fynns/ui` primitives and `var(--fynns-*)` tokens only; `src/app.css` carries consumer-owned additions (zoom-button reset and the shell chat-width floor).
