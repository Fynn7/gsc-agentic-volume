# Local VLM experiments with the native renderer

This optional path uses an installed external agent harness. It preserves the
Phase 1 driver and GUI. The external harness decides actions; this repository's
launcher records transport and resources, but contains no agent decision loop.
Measured development runs and limitations belong in [LOCAL_VLM_EVIDENCE.md](LOCAL_VLM_EVIDENCE.md).

## Run on Windows

Prerequisites: the existing built Native Raycaster, an existing Python with NumPy
and Pillow, installed Codex CLI, installed Ollama, an installed local vision model,
and a supported local volume header/payload. No dependency installation, paid API,
or GUI is required. Relative paths below assume the repository root.

1. Copy `experiments/local-vlm.example.json` to a gitignored local file, for example
   `runtime/my-experiment.json`. Set `volumePath`, the complete natural-language
   `task`, a fresh `resultDirectory`, and the desired model/service settings.
   The result directory must not already exist: failed runs are never overwritten.
2. Run:

   ```powershell
   ./scripts/start-local-vlm.ps1 -Config runtime/my-experiment.json -StartOllama
   ```

   Omit `-StartOllama` when inference is already running or tunneled from a server.
   Use `-Python <absolute-python.exe>` or `GAV_PYTHON` if necessary. The launcher
   never kills an existing inference server. On this audited host the existing
   bundled Python works; PATH Python lacks NumPy. No frontend build is required.
3. Verify the exact run, optionally replaying native scenes without model calls:

   ```powershell
   python -B scripts/verify_local_agent.py runtime/local-vlm/my-unique-run --replay
   ```

   Here `python` means the selected interpreter. Replay writes a new `replay/`
   directory and refuses to overwrite a previous replay. Pixel/scene checks do
   not establish semantic correctness.

If Codex is installed elsewhere, set `codexExecutable` to its native executable.
Windows `.cmd`/`.bat` wrappers are rejected because multiline prompts can truncate
the command and discard provider options. The launcher uses an isolated Codex home,
explicit local Responses provider, no OpenAI authentication, no shell, no web
search, and only the bounded renderer MCP tools are preapproved. Child HTTP proxy
settings reject accidental public destinations. This is not an OS network firewall.
The renderer-only child also defaults `projectDocumentBytes` to zero, preventing
unrelated repository UI implementation instructions from filling its context.
Its task and canonical tool contracts remain explicit. This does not alter the
development agent's repository instructions. The corresponding Codex setting is
documented in the [configuration reference](https://developers.openai.com/codex/config-reference).

## One canonical renderer and scene

`src/app.py` serves the GUI and tool API. `src/external_tools.py` operates its
existing `Session` objects and uses `src/renderer.py` and `src/volume.py` directly.
No second renderer, parallel scene store, new planning framework, or model repair
loop is introduced. A native execution lock prevents overlapping native processes.
Revision checks and validation precede mutation; only a verified render advances
the scene. External sessions cannot be continued by the Phase 1 model driver.

The service identity is `GET /api/service`, not merely an occupied port. Port
8765 may also be used by unrelated visualization software; verify identity.

- `GET /api/tools`: the canonical tool names, descriptions and JSON Schemas.
- `POST /api/tools/open`: `{source, goal, model, budget}` imports the volume and
  obtains the initial native render without calling any model.
- `POST /api/tools/call`: `{id, name, arguments}` invokes one operation on that
  same session. The initial image is outside the budget; new renders and slices
  consume the shared observation budget. Errors remain in the session trace.
- `inspect_scene`: current numeric facts, scene, native readback and actual PNG.
- `render_scene`: sparse TF/camera/translation/crop patch plus `expectedRevision`.
  Omitted parameters remain unchanged. No arbitrary GSC or shell is exposed.
- `inspect_slice`: actual storage-axis grayscale slice, explicitly marked as a
  voxel slice rather than a native raycast; does not change the scene.
- `finish_task`: saves a selected previous native frame and its matching scene,
  native readback and GSC under the session's `final/`. Model claims remain
  unverified; finishing never creates a new image or modifies the scene.

`scripts/renderer_mcp.py` is an optional stdio MCP adapter over these HTTP routes.
It has no renderer/model logic. It emits standard MCP `image/png` image blocks,
fetches the real image bytes and verifies their SHA-256. Both initial and subsequent
images travel as image content, not file-path descriptions. It requires no SDK
installation. Configure its Python command with `--service`, `--session`, and
optional `--timeout`. MCP image blocks are defined by the
[MCP tool specification](https://modelcontextprotocol.io/specification/2025-11-25/server/tools).

## Configuration and evidence

The JSON configuration owns the model endpoint/ID, context budget, HTTP timeout,
whole-task timeout, temperature/thinking/output limit, harness, renderer URL,
volume path, task, observation budget and result directory. Optional Python and
native harness executable paths may be supplied. MCP is a local stdio connection
whose underlying renderer service URL is configurable.

**Context size has two sides.** `contextSize` tells the harness its budget. For
Ollama's OpenAI-compatible endpoint, set `OLLAMA_CONTEXT_LENGTH` on the inference
service to the same value (or use an appropriately configured model). The startup
script applies it when it starts Ollama and rejects a known loaded-model mismatch.
It cannot prove the default of an already-running but unloaded service. Inspect
the recorded `/api/ps` `context_length` after the first call; an actual mismatch
invalidates the requested configuration. Model advertised maximum context is not
the allocated runtime context. See [Ollama compatibility](https://docs.ollama.com/api/openai-compatibility).

Each run saves configuration, model inventory/digest/projector metadata, the exact
CLI arguments, full harness events, original and forwarded HTTP request bytes,
raw streamed responses, request timing, periodic GPU/system RAM/CPU counters,
and a final session snapshot. The recorder changes only declared inference options
and strips authorization; it does not translate image/tool protocols. Failed
startup/import/preflight runs retain `failure.json`. The service session directory
contains every original volume hash/conversion, actual GSC, scene, native readback,
process output, PNG, render result and chronological tool/error event.

Usage counts come from the actual provider response. They include repeated input
context and distinguish cached input where reported. CPU counters are whole-system
`GetSystemTimes` samples; GPU and RAM measurements are also host-wide, not isolated
process peaks. Ollama logs and `/api/ps` record model offload/runtime allocation.
Slow or failed calls are retained. This is development evidence, not a held-out
benchmark or semantic success rate.

`verify_visual_control.py <run>` optionally generates two fresh native views and
performs a blind A/B aspect-ratio diagnostic in both orders using direct Ollama
image calls. The answer is computed independently from actual pixel bounding
boxes; scene parameters/measurements are withheld. This is a separate perception
diagnostic, not an alternate agent harness or a semantic target score.

All data, screenshots and model responses stay under gitignored `runtime/`.
Check `git status` before any explicitly authorized commit. The frozen old
repository is a read-only source; do not run its lifecycle scripts.

## First H200 lab test

The Windows harness, tool service, Native Raycaster, data and artifact format stay
here. Inference alone moves. No server access has been confirmed.

1. Confirm login, GPU identity and memory quota/MIG, available disk, OS, CUDA/driver,
   permitted inference software/model downloads, authentication, port policy and
   SSH tunneling. Do not assume H200 access means unrestricted model/GPU access.
2. Start the permitted Ollama service with cloud disabled, one parallel request
   and the requested context. Install/identify the actual permitted VLM and record
   its digest, quantization and vision/tool capabilities. For another server such
   as vLLM, separately verify Responses/tool-image compatibility and metadata
   endpoints; this launcher currently requires Ollama's `/api/*` inventory API.
3. If permitted, create an SSH tunnel on Windows, for example
   `ssh -N -L 11435:127.0.0.1:11434 <lab-user>@<lab-host>`.
   Keep renderer port 8765 on Windows loopback. Do not expose private volumes or
   unauthenticated services to a public interface.
4. Copy the same local experiment config, change `endpoint` to
   `http://127.0.0.1:11435`, set the actual model/context/timeout and a fresh output
   directory, then run the same startup script **without** `-StartOllama`.
5. Repeat the image-delivery check and Task A before exploratory tasks. Compare
   only after freezing model, quantization, inference options, tools, dataset,
   prompts and budgets. A different H200 model is a new condition, not an
   automatically comparable repeat. Confirm policy permits sending volume
   screenshots to that school inference service before doing so.

OpenCode v2 was inventoried only. No alternate launcher or model benchmark is
included because Codex already met the transport/execution requirement. Do not
claim OpenCode or arbitrary OpenAI-compatible services work until their real
tool-image cycle is checked.
