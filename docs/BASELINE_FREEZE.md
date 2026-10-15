# Baseline freeze — 2026-10-15

The current user request and `01_ADHD_AGENTIC_VOLUME_HARNESS_PLAN.md` authorize execution. The older handoff is historical context; its statements about absent implementation authorization and preparation-first architecture are superseded. The 16 decisions are frozen. This independent rebuild does not edit the old thesis documents.

## Baseline identity

- Old repository: `D:\fynns_local_ws\gsc-live-preview`.
- HEAD: `926eed6018d5c7547f4be14b15bf1e620efa7195`.
- Initial tracked/untracked Git status: clean (`git status --porcelain=v1` returned empty).
- Exact identity, attachment SHA-256 and branch: `audit/baseline-state.json`.
- Post-audit identity comparison and historical counts: `audit/verification.json`.
- Historical artifact paths, sizes and SHA-256: `audit/historical-artifact-hashes.json`. Original data/experiments remain at their original paths. No baseline writes, branch changes, commits, cleanup or dependency operations were performed.

## Reproduction entry points (existing commands; not executed in the old repo)

From old root: `./scripts/build-raycaster.ps1` (VS 2026, v145); `npm run dev` (GUI, runs lifecycle hooks); `npm --prefix tools/gsc-live-preview run volume-context -- inspect --input <path> --python <python>`; `facts` and `understand` subcommands are documented in the old software README. Python numeric tests: `python -B -m unittest discover -s scripts/volume_context/tests`. The old tests/builds were not executed because they can write caches, output or runtime artifacts. Relevant numeric tests and actual compilation were instead run in the new repo.

Native binary existed at `VirtualSampling/Raycaster/VS/x64/Release/Raycaster.exe`; assets cwd is `VirtualSampling/Raycaster/VS`. A copy was retained only as an ignored reference binary under `runtime/baseline-Raycaster.exe`; the new executor uses its own newly built binary.

## Existing experiment evidence

The actual report and JSON summaries exist under `tools/gsc-live-preview/tmp/prompt-only-20261014/`. Their reported configuration is Ollama 0.40.4, qwen3.6:35b, digest `25eb3f4af70f7d94b022e3104298884064f3ee64756be7abd8bcb6e453915531`, temperature 0, seed 42, think false, context 16384, prediction limit 4096, zero retries. These are historical recorded settings, not the new harness defaults.

Recorded results: 26 calls, 26 schema valid, 22 formal Card valid, 36 proposed displays, five black displays. These totals are cross-checked against retained raw record collections by `scripts/audit.py`. Original prompt files, model-show, protocols, request/response artifacts, frames and scene scripts are hashed in place. Historical semantic claims are not independently re-scored; no active feedback result is inferred from them. Old fixed-observation and Card/Slot IR paths remain baseline-only.

## Environment evidence

Node v24.11.1; default Python 3.11.15 lacks NumPy (actual import failure). Bundled interpreter `C:\Users\fynna\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe` reports NumPy 2.3.5 and Pillow 12.3.0. VS installation was located with vswhere at `C:\Program Files (x86)\Microsoft Visual Studio\18\BuildTools`. Local Ollama initially refused connections at 127.0.0.1:11434; it was started with logs redirected to the new repo. Live model inventory and model configuration are saved under `runtime/gate-model.json`.

Native original build: `runtime/native-build.log`; added readback build: `runtime/native-readback-build.log`. First readback compile failed on matrix accessor syntax; corrected only in the new source and rebuilt. Git shell helper command `git submodule status` failed because basename/sed were absent from its PATH. This snapshot has ordinary tracked renderer files and no submodule workflow requirement; native Git commands work. That failure does not block porting.

Baseline native EXE hash verified against the isolated reference copy: `18cdb236a8d555a7d28d9a0db1d0de336f3a082d1bd6ef774f54953e2a4a6453`. See `audit/baseline-binary.json`.
