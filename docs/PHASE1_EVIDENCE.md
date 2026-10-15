# Phase 1 execution evidence — 2026-10-15

Phase 0 passed with `audit/phase0-gate.json`. At the end of the Phase 1 audit, the independent repository had no remote and no commits. Main/dev branch initialization followed. The current acceptance concerns the actual image/tool execution loop, not semantic task success or a final product.

## Real GUI-triggered loop

Session `901b32e4-f5c0-4dec-9c05-8a7cf5c71092`, under ignored `runtime/`, was initiated using the GUI at http://127.0.0.1:8765. Task: acquire two NEW views beyond the initial screenshot and compare geometry; model chooses the views after seeing current images. Source was the existing anonymized V02 scalar file (256 × 256 × 44). It is development data, not held out. No dataset identity, expected target parameters, mask or dataset-specific rule was added.

Actual model: local qwen3.5:9b, Q4_K_M, digest `c97eb11d70b1acdc88af01eef566c1fe4f7fbe93eb1afc06871132f293ff425a`, Ollama 0.40.4. Raw `/api/show` (including model license/configuration) and version response: session `model.json`. Native renderer hash is bound in every SceneSpec.

1. Initial scene revision 0 produces `frame-0/image.png`, hash `79eca4501f8841f860b78f867f8c7342b761482bb55535aace86954c9de52487`.
2. Model call 1 receives the actual initial PNG and selects rotation `[45,0,0]`. Native rendering produces frame 1, hash `de707a58907d...` (full hash in result.json).
3. Model call 2 receives the actual frame-1 PNG in the new user message, retains prior context, and selects `[45,90,0]`. Native rendering produces frame 2, hash `d120b8a217f8...`.

The rotations came from raw model responses, not a preset sequence. TF and crop stayed at their existing values. Each frame directory stores script.gsc, scene.json, process.json, native-state.json, result.json and the newly generated PNG. Native readback compares TF, dimensions, crop, translation and full rotation matrix. Requests include actual PNG base64 bytes and SHA-256; raw streaming responses, extracted actions, immutable event lines and session snapshots remain available. No hidden call retries or JSON repair occurred.

`scripts/verify-phase1.py` checks raw request hashes, image bytes versus the preceding native frame, native process completion, scene/result bindings, actual model-selected parameters and the existence of two consecutive render decisions. Result: `audit/phase1-gate.json`. GUI screenshot: `audit/phase1-gui.jpg`. Continue was exercised from the paused revision-2 scene; its subsequent action remains in the same session and uses the same total budget. This is not a Phase 2 recovery certification.

## Failures retained

- `ed2ebf68-2474-4925-9d9d-2a4119d85489`: model stopped on the initial image and falsely claimed it had inspected two views. No new rendered frame existed. Excluded from feedback-loop acceptance.
- `a360e2a7-4e00-42e1-a71f-e5bcd537c97f`: model said it could not issue render operations because the original generic protocol omitted an explicit tool-effect description; it returned finish with harmless native float roundoff in TF parameters, and the original strict equality check rejected it. All original artifacts remain.

Implementation correction was generic: explicitly document that render_scene actually executes its parameters and returns a fresh PNG; finish_exploration performs no mutation. The same two-new-view task was rerun under the corrected protocol. No dataset-specific strategy, mandatory angle, black-frame repair sequence or hidden retry was introduced. These are development iterations and cannot establish a performance comparison.

## Actual checks and limits

- Original bundled renderer built in isolation with VS 2026/v145; new readback command rebuilt successfully after one recorded compiler error.
- Ported numeric suite: 104 tests run, 1 skipped because this host cannot create test symlinks; all other tests passed. It covers QVis/NHDR/TU Wien parsing, axis order, declared spacing, scalar conversion and numeric provenance. TU Wien protocol-dependent tests were adapted to the new numeric importer; unrelated Card protocol tests were excluded and documented in the port map.
- New capability tests: 2 passed, checking actual slice pixels/axes with anisotropic spacing and invalid TF/crop preservation. They do not alone validate all optical rendering effects.
- Actual native image/scene/readback verification, actual Ollama image input and both dependent render calls passed. Original and new images were visually inspected, without claiming semantic task success.
- Phase 2 durable restoration, complete Stop/Continue edge cases, recipe replay and subsequent chat are pending. Orthogonal slices are implemented and numerically tested but not yet exposed as an agent tool. The first GUI accepts a local header path rather than a native file picker.
- Generated runtime evidence is gitignored. No volumes, model responses or images were uploaded to an external provider. Ollama is loopback with an installed local model. No commits, push or publication.

## Recheck commands

Use the bundled Python path recorded in BASELINE_FREEZE.md (default PATH Python lacks NumPy):

```powershell
$env:PYTHONPATH="$PWD\src;$PWD\scripts"
python -B -m unittest discover -s scripts/volume_context/tests -v
python -B -m unittest discover -s tests -v
python -B scripts/verify-phase1.py
python -B scripts/audit.py
```

`python` in these examples denotes the selected interpreter, not an assertion that PATH is configured. `scripts/dev.ps1` selects the existing bundled interpreter or accepts `-Python`. Do not run the old repository's lifecycle scripts to verify this rebuild.

Continue result: real call 3 received frame 2 and returned finish_exploration at scene revision 2. Total: three physical model calls, two model-selected new renderings plus one initial rendering. The model completion claim remains externally unverified.
