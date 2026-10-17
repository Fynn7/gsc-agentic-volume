> Desktop experiment launch is currently suspended: global configuration installation has been retired. See [the incident analysis and repair](docs/CODEX_DESKTOP_INCIDENT.md) before using earlier Desktop instructions. Historical CLI evidence remains valid with its original scope.

# Agentic Volume Visualization

Independent local Windows rebuild. Phase 0 evidence lives in docs/; Phase 1 feedback evidence is recorded in docs/PHASE1_EVIDENCE.md; full product delivery is incomplete. No semantic success or product completion is claimed.

Requires development Python 3.11+ with NumPy and Pillow, VS 2026/v145 for builds, local Ollama with an installed vision model, and Node.js/npm to build the UI bundle. No commercial API. Runtime output stays gitignored in runtime/.

```powershell
./scripts/build-raycaster.ps1
./scripts/dev.ps1 -Port 8765        # builds frontend/dist when missing; -Rebuild to refresh
```

Open http://127.0.0.1:8765. Set the local volume header path (QVis .dat, TU Wien binary .dat or detached NHDR) with the composer `+` entry and the vision model with the model button, describe the task and send. Associated payloads must exist. Anatomical orientation stays unknown. Paths with spaces are imported to neutral runtime paths before native execution. Mask and perspective are unsupported. Screenshots in the conversation are real native renderer captures bound to their producing event and scene revision; model claims stay visibly unverified.

Ported renderer license: VirtualSampling/LICENSE. See docs/PORTING_MAP.md for attribution and distribution limitations. Runtime data and private audit evidence stay local and gitignored.

On this audited host the launcher selects the existing bundled interpreter; override with `-Python <absolute python.exe>`. Default PATH Python lacks NumPy. No installation is performed. The current GUI picks the volume through the OS-native file browser (composer `+` entry), which requires the harness itself to be running since the dialog opens on its machine.

## Git workflow

Use main for the stable baseline and dev for ongoing development. Current development branch: dev. Feature branches use the codex/ prefix. Do not push private volumes, screenshots, model responses or local audit artifacts. Committing and pushing require the current user authorization.

## External local VLM experiments

See [local VLM operations and H200 preparation](docs/LOCAL_VLM.md) and [measured development evidence](docs/LOCAL_VLM_EVIDENCE.md). This optional Codex/MCP path reuses the same native renderer and Session service; Phase 1 and the GUI remain available.

## Codex Desktop experiment entry

The current formal harness is Codex Desktop. After ending the Worker and fully quitting Desktop, run `./Start-VolumeDesktop.ps1` in external PowerShell. See [Desktop startup, fixed prompts, recording and H200 steps](docs/CODEX_DESKTOP.md) and [tested support interfaces versus pending human acceptance](docs/CODEX_DESKTOP_PREPARATION_EVIDENCE.md). No Desktop acceptance is claimed from CLI or scripted interface tests.
