# Phase 0 blockers and gate

Only issues preventing the first private local Volume → Native PNG → Ollama image → action → Native PNG path are Phase 1 blockers. Evidence is from actual commands, not historical handoff assumptions.

| ID | Classification/status | Evidence and impact | Minimal resolution and verification |
|---|---|---|---|
| B01 | BLOCKER, resolved | Default `python -B -c "import numpy,PIL"` failed: No module named numpy. Numeric import cannot run. | Use already available bundled Python with NumPy 2.3.5/Pillow 12.3.0. No installation. Run numeric tests and actual import with that interpreter. |
| B02 | BLOCKER, resolved | 127.0.0.1:11434 initially connection-refused; Ollama CLI 0.40.4 installed. No live image path then. | Started local `ollama serve`, logs under runtime/. Verify `/api/tags`, `/api/show`, then actual new Native PNG in `/api/chat`. Actual image call completed; raw request/response retained. |
| B03 | BLOCKER, resolved | No original native scene readback command; echoed script alone cannot establish actual parameters. | Added dumpstate only in new renderer, fixed initial accessor compile error, rebuilt. `runtime/gate-frame/native-state.json` records actual TF, dimensions, rotation, translation, crop, compared to SceneSpec. Actual PNG decoded, checked and hashed. |
| N01 | NON-BLOCKER | Git submodule shell helper cannot find basename/sed; this snapshot has bundled ordinary tracked sources. | Native git works; source files copied individually, no submodule operation needed. |
| N02 | NON-BLOCKER, UNKNOWN for redistribution | Project Python root license absent; bundled dataset/binary redistribution clearance incomplete. | Explicit user authorization covers private port/development. Retain notices and private data. Resolve before packaging/distribution; no publication authorized. |
| N03 | NON-BLOCKER | Original path parser rejects unquoted whitespace/special characters. | Import into neutral new runtime path; reject unsupported native script paths explicitly. General native path quoting is backlog. |
| N04 | NON-BLOCKER | Source origin/directions preserved but renderer uses storage axes; unknown anatomical orientation. | Expose storage coordinates honestly; no named anatomical views. World-transform visualization is later work. |
| N05 | NON-BLOCKER | Compiler C4819/codepage and APIENTRY redefinition warnings. | Original and readback builds complete; do not polish unrelated upstream files. |

## Gate evidence

New-repo commands (run with the explicit bundled Python interpreter):

```powershell
./scripts/build-raycaster.ps1
$env:PYTHONPATH="$PWD\src;$PWD\scripts"
python -B -m unittest discover -s scripts/volume_context/tests -v
python -B -m unittest discover -s tests -v
python -B scripts/audit.py
```

`runtime/gate-render.log`, `runtime/gate-frame/script.gsc`, `native-state.json`, `process.json`, `scene.json`, `image.png`, `result.json` provide actual native execution evidence. `runtime/gate-image-call/` saves actual image-bearing request and raw Ollama stream; `gate-image-action.json` records the schema-checked decision. These checks passed; Phase 0 PASS is recorded in audit/phase0-gate.json. Final gate disposition is recorded in `audit/phase0-gate.json` after verification, superseding pending statuses in this chronological issue table.

## Backlog (not gate blockers)

- Phase 2 durable restart recovery, task-level failure recovery, stopped-call continuation, immutable recipes and real replay, subsequent chat on current scene. Current prototype persists evidence but cannot restore sessions into the UI after process restart.
- Agent slice tool exposure, controlled GSC extension, richer TF/opacity, diagnostic Mask triggers after actual tool-limitation evidence.
- Multiple independently validated visualization tasks and controlled P/S/A experiments. Current dev volumes are not held out; no semantic success or Agent superiority claimed.
- Windows packaging, clean-machine verification, licenses/asset migration, general path quoting, dependency bundling.
