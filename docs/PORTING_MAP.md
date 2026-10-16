# Capability porting map

Source root: `D:\fynns_local_ws\gsc-live-preview`, frozen HEAD in BASELINE_FREEZE.md. Target root: `D:\fynns_local_ws\gsc-agentic-volume`. `audit/ported-source-hashes.json` records source hashes at initial copy; it is a copy manifest, not a claim all copied files are unchanged after adaptation. No old GUI, Card assembly, Slot IR or Prompt-Fabrik is a runtime dependency.

| Source capability | New location / responsibility | Dependencies and verification |
|---|---|---|
| scripts/volume_context/readers/ | Same relative path; QVis, TU Wien binary DAT, detached NHDR | NumPy; reader tests, malformed/truncated/oversized input checks |
| convert.py, stats.py, hashing.py, errors.py, progress.py | Same relative paths; canonical values, deterministic facts, provenance | NumPy; existing conversion/stats tests |
| contract.py, budgets.py | Numeric-only extracted numeric-contract.json, no Dataset Card schema | Extracted Limits/Budgets/Dimension3; numeric tests |
| Binary DAT protocol tests | Adapted test_tuwien_reader.py against new numeric importer | Header exclusion, x-fastest layout, invalid dimensions, truncation, budget |
| Raycaster / Utils / VS sources, shaders, library headers | VirtualSampling/; unchanged math except new dumpstate GSC diagnostic | VS 2026/v145, Windows SDK, bundled static GL/GLFW/GLEW; actual build logs |
| Native runner/verification concepts | src/renderer.py, fresh implementation | Native command markers, process exit/timeout, actual decoded PNG, SHA-256 and actual TF/crop/camera readback |
| No old agent loop to port | src/model.py, src/app.py; GUI replaced by frontend/ (React 19 + Vite + @fynns/ui) | Python stdlib native Ollama HTTP; no AI framework or provider adapter in the harness; the frontend is a build-time bundle served read-only by app.py |

## Numeric and scene contracts

PreparedVolume binds source hashes, canonical hashes, renderer payload hash, dtype, dimensions, spacing provenance, source geometry, scalar transforms and unknown anatomical orientation. X is fastest; arrays use z,y,x. Uint8 is preserved; uint16 renderer mapping reuses the tested full-volume integer min/max rule. Rendering explicitly uses the mapped uint8 payload so GSC and numeric slices see the same domain. Scalar quantization is not asserted invertible.

SceneSpec records full supported settings and versions. Smoothstep uses start/width, not center/width; it changes native RGB and alpha. Crop is half-open storage voxel bounds. Rotation uses the renderer's existing handedness (native positive rotations have the opposite sign to the common mathematical column-vector convention), applied X then Y then Z. No anatomical orientation is invented. Native readback is added only to this fork. Mask and perspective are unsupported. Source world directions/origin are retained as metadata; display uses renderer storage axes and spacing, not an anatomical world-coordinate reconstruction.

Orthogonal numeric slices have explicit axis/index/pixel mapping; tested independently. They are not yet exposed as an Agent tool. There is no arbitrary agent GSC/shell execution. GSC remains a controlled implementation detail and an audit artifact.

## Attribution and rights

Renderer MIT license is retained verbatim at VirtualSampling/LICENSE (University of Duisburg-Essen visualization group, 2026). GLFW/GLEW and other bundled library notices remain in the original headers, including VirtualSampling/VS/include/GLFW/glfw3.h and GL/glew.h. Do not claim third-party code as original. The original renderer and baseline EXE are distinguished from this locally modified fork.

No root license was found for the original project's own Python/application code. Local copying/adaptation is explicitly authorized by this user's request; no public redistribution license is inferred. Public distribution clearance of project-owned files, bundled binary dependencies and datasets remains UNKNOWN and must precede Phase 4 distribution. Existing bundled dataset files are retained for native startup compatibility, excluded from new Git tracking, and never uploaded. Their redistribution rights are UNKNOWN; replacing eager built-in loading with a tested asset migration is backlog work. This is not a barrier to the authorized private Phase 1 local execution.

No provider SDK is used. The React frontend build uses npm (Vite, React, `@fynns/ui` from the sibling core checkout); the Python harness has no npm runtime dependency. Development Python is explicitly configured; final interpreter/native dependency packaging is Phase 4, not achieved here.
