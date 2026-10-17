> **Current Desktop entry:** [isolated human-operated startup and verification](CODEX_DESKTOP_ISOLATED.md). Global installation/Explorer activation instructions below are historical and retired. Actual Desktop gates remain manual; see also [the preserved incident report](CODEX_DESKTOP_INCIDENT.md).

# Codex Desktop local VLM workflow

Formal harness: **Codex Desktop**. No automatic fallback. The Worker prepares and
tests the support interfaces; the human runs Desktop after the Worker ends.
Desktop model selection, provider acceptance, tool-image display and autonomous
feedback have **not** been executed as part of this preparation. Earlier CLI
evidence is in [LOCAL_VLM_EVIDENCE.md](LOCAL_VLM_EVIDENCE.md), not Desktop evidence.

## First local run

1. End the Worker and **fully quit Codex Desktop**, including other active Workers.
   In an external PowerShell window run:

   ```powershell
   cd D:\fynns_local_ws\gsc-agentic-volume
   .\Start-VolumeDesktop.ps1
   ```

   If script execution is disabled, use
   `powershell -NoProfile -ExecutionPolicy Bypass -File .\Start-VolumeDesktop.ps1`.
   The script opens the installed Codex Desktop app. It does not launch Codex CLI,
   send prompts, or initiate inference. If the app was still running, quit/reopen
   it so the new startup configuration is loaded.

2. Start a **new local chat**, using project folder
   `D:\fynns_local_ws\gsc-agentic-volume\runtime\desktop\workspace` (created by
   preparation). Choose **Local VLM: qwen3.6:35b** / `qwen3.6:35b`. Do not reuse a
   previous cloud-model chat. If this model is absent or an account/policy error
   blocks the custom provider, stop and retain the logs; this is a Desktop
   compatibility failure, not a VLM quality result. Desktop account login, if
   required by the installed app, is a human step, not an inference API key.

3. Paste [00-check.txt](../experiments/desktop-prompts/00-check.txt). The chat
   should show a `volume_renderer.desktop_status` tool call. Its configured model
   must be `qwen3.6:35b`, endpoint `http://127.0.0.1:11434`, and recording proxy
   `http://127.0.0.1:11436`. This confirms MCP invocation; actual provider routing
   must also be checked below. If needed inspect Desktop's MCP settings and confirm
   `volume_renderer` is enabled with these six tools:
   `desktop_status`, `begin_task`, `inspect_scene`, `render_scene`, `inspect_slice`,
   `finish_task`. Approve the bounded renderer tools if the app requests it.

4. Paste [01-parameters.txt](../experiments/desktop-prompts/01-parameters.txt)
   (Task A). After it finishes, paste
   [02-feedback.txt](../experiments/desktop-prompts/02-feedback.txt) (Task B) in the
   **same chat**. Each calls `begin_task` for a fresh canonical scene. Check that
   actual tool images appear, the second Task B action follows the previous image,
   and the final frame is useful. No screenshot or parameter sequence is supplied
   to the model in advance. If an active task is abandoned, close its chat; use a
   new chat/connection rather than requesting a second overlapping task.

The default volume is the existing uncompressed development sample
`runtime/gate-volume/render.dat` plus its associated `render.raw`. To select your
own volume at launch:

```powershell
.\Start-VolumeDesktop.ps1 -VolumePath 'D:\data\volume.dat'
```

Use a supported scalar QVis DAT/raw, TU Wien DAT or detached NHDR. Payload files
must be accessible. LZ4 QVis input remains unsupported; Christmas Present Task C
is still blocked by that importer limitation. No correct crop or TF is hardcoded.

## Evidence and route verification

In the external PowerShell window, after the tasks:

```powershell
.\Start-VolumeDesktop.ps1 -Collect
$runDir=Get-Content .\runtime\desktop\LATEST.txt
Start-Process (Join-Path $runDir 'evidence.html')
Invoke-RestMethod http://127.0.0.1:11436/desktop/status
```

The independent MCP journal exists even if Desktop hides or collapses tool output.
The HTML is an artifact inspection report, **not** a replacement agent GUI or a
claim that Desktop displays images. Save human GUI screenshots in `$runDir\gui`
(model picker, expanded tool image, second action, final result). Desktop chat
messages/thought summaries need a manual app export or screenshots; the Worker
cannot automate that export. Model wire responses, including tool decisions and
available reasoning/text, are independently recorded regardless.

| Location | Contents |
| --- | --- |
| `runtime/desktop/LATEST.txt` | Current absolute run directory |
| `<run>/config.json`, `model.json`, `ready.json`, `desktop-package.json` | Frozen configuration, actual Ollama metadata/digest/quantization/capabilities, source/native hashes, service identity, installed Desktop package version |
| `<run>/http/*.request.bin`, `*.forwarded.bin`, `*.response.bin`, `*.metadata.json` | Actual inference input/output bytes, upstream options, errors, per-request timing and first response byte |
| `<run>/mcp/<connection>/call-*.request.json`, `*.result.json`, `events.jsonl` | Every tool request/result including actual base64 PNG, errors, time and session ID |
| `<run>/sessions.jsonl` | Goal and canonical service artifact directory for every task |
| `runtime/<session>/frame-N/` | Actual native PNG, Scene JSON, native readback, GSC and result |
| `runtime/<session>/final/` | Selected existing native PNG and producing parameters |
| `<run>/telemetry.jsonl` | Windows CPU counters/RAM, local GPU, endpoint Ollama loaded-model/offload/context samples every 3 s |
| `<run>/evidence.json`, `evidence.html` | Call summary, tokens if supplied, timings, exact returned-PNG/input-image matches and gallery |

The selected model's requests must actually reach the recorder and return from
Ollama; a model self-report or GUI model label is insufficient. The recorder
rejects generation requests for any other model and removes upstream API auth.
There is no paid API configuration. A zero inference count after submitting a
prompt indicates incorrect Desktop routing or an earlier failure. The status
counts completed request metadata, so a running request may not count yet.

`exactPngTransportMatches` proves byte-identical MCP PNGs entered a recorded model
request. If Desktop reencodes images, zero exact matches is inconclusive and needs
pixel-level investigation; it is not a pass. Image delivery alone does not prove
that an action used the image. Review the ordered response/tool/image records.
`contextMismatch` flags actual Ollama allocation differing from the requested
16,384; missing samples leave allocation unverified. Existing Ollama instances
are not restarted automatically. Model loading/offload performance comes from
Ollama logs/PS and response timings; the proxy does not invent unavailable
provider metrics. The HTML and collector never mark semantic visibility passed.

All runtime data remain gitignored and local. Logs contain prompts, full image
payloads and volume paths; share only deliberately selected evidence. There is no
automatic upload, commit, replay inference, or deletion of earlier artifacts.

## Configuration and recovery

`experiments/desktop.local.json` and `desktop.h200.json` fix model ID, endpoint,
context, inference/tool timeout, temperature, thinking, output limit, render budget,
renderer/proxy addresses and volume. Copy a template into `runtime/` for private
edits. Task prompts are versioned separately. The tool adapter forwards sparse
actions to the existing canonical service and never owns another scene or loop.
MCP server startup snapshots its run configuration; finish a task before changing
the configuration or starting another concurrent chat.

On a normal launch the script backs up `%USERPROFILE%\.codex\config.toml`, installs
a generated provider/catalog/MCP configuration and opens Desktop. Other MCP
servers and plugin/shell/parallel-agent features are disabled only in this
temporary experiment configuration. Unrelated settings remain in the backup.
No configuration has been installed by the Worker. Official startup rules:
[gateway configuration](https://learn.chatgpt.com/docs/enterprise/connect-to-a-gateway),
[advanced configuration](https://learn.chatgpt.com/docs/config-file/config-advanced),
[configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference).
Provider keys belong at user level; CLI-only profile selection is not assumed to
work in Desktop. The model catalog is generated using the installed cache's
schema, with Qwen identity, vision input and 16K context; Desktop parsing and model
selection remain a human acceptance gate. Desktop/local policy may override user
settings. No attempt is made to bypass such restrictions.

To return to ordinary development or switch runs: finish/close the experiment,
fully quit Desktop, then in external PowerShell:

```powershell
.\Start-VolumeDesktop.ps1 -Collect
.\Start-VolumeDesktop.ps1 -Stop
.\Start-VolumeDesktop.ps1 -Restore
```

Restore is byte-exact and refuses to overwrite concurrent user edits; in that
case compare `<run>/user-config.original.toml` with the live config manually.
Start the next run only after restore. The launcher does not kill Desktop or an
unrelated Ollama service. `-PrepareOnly` generates files only; `-ServicesOnly`
starts passive support without touching live Codex settings or opening a GUI.

## H200: exact first lab procedure (not yet executed)

Keep Windows GUI, tools, renderer, volume, prompts and budget fixed. First change
only the inference endpoint using the same Qwen model, then deliberately create
a separate run/configuration for a stronger VLM. No strict comparison is claimed
when model, digest, quantization, context or task changes.

1. On the lab server, after obtaining the real host/user and permissions:

   ```bash
   ssh USER@HOST
   nvidia-smi
   ollama --version
   ollama list
   curl -fsS http://127.0.0.1:11434/api/version
   ollama show qwen3.6:35b
   ```

   If the model is absent, obtain permission/storage and install it with
   `ollama pull qwen3.6:35b`. If no approved Ollama service is running, start one
   in a separate server terminal, bound only to loopback:

   ```bash
   mkdir -p "$HOME/volume-vlm-evidence"
   OLLAMA_HOST=127.0.0.1:11434 OLLAMA_NO_CLOUD=1 \
   OLLAMA_CONTEXT_LENGTH=16384 OLLAMA_NUM_PARALLEL=1 \
   ollama serve 2>&1 | tee "$HOME/volume-vlm-evidence/ollama.log"
   ```

   Do not start a second service on an occupied port. Ask the administrator to
   confirm the existing service's context/offload configuration or assign an
   approved free port and update the tunnel. Linux GPU support, service policy,
   permissions, available model builds and memory are onsite checks, not assumed.

2. In a dedicated **Windows** terminal keep this tunnel running:

   ```powershell
   ssh -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -N -L 127.0.0.1:11435:127.0.0.1:11434 USER@HOST
   ```

   Verify the host key through the lab's approved channel. Use its authorized SSH
   key/MFA flow; do not expose Ollama on `0.0.0.0` or put credentials in JSON.
   If a jump host is required add the lab-supplied `-J USER@JUMP`.

3. In another Windows terminal:

   ```powershell
   Invoke-RestMethod http://127.0.0.1:11435/api/version
   Invoke-RestMethod http://127.0.0.1:11435/api/tags
   # Finish/quit Desktop and stop/restore the previous run as above first.
   .\Start-VolumeDesktop.ps1 -Mode H200
   ```

   Repeat the same three prompt files. Here 00-check must show endpoint port
   **11435**, while the recording proxy remains **11436** and renderer **8766**.
   After the first call inspect `/api/ps` through port 11435 and the evidence
   report for actual context/offload. Remote inference metadata/PS are recorded;
   `nvidia-smi` in Windows telemetry still describes the Windows renderer GPU.

4. Capture H200 GPU/host utilization in separate server terminals during the run:

   ```bash
   nvidia-smi --query-gpu=timestamp,name,memory.used,memory.total,utilization.gpu,power.draw --format=csv -l 3 > "$HOME/volume-vlm-evidence/h200-gpu.csv"
   # Optional second terminal; stop both monitors with Ctrl+C after the task.
   vmstat 3 > "$HOME/volume-vlm-evidence/host-vmstat.txt"
   ```

   Collect Windows evidence with `-Collect`; copy the server logs to the run
   directory yourself when permitted. Clock offsets mean cross-machine absolute
   timestamps are not automatically equivalent. Save server driver/Ollama version.

For another model, copy `desktop.h200.json` to a private runtime JSON and edit
`model`, context and necessary timeouts; launch with
`-Mode H200 -Config <absolute-json-path>`. The same tools/prompts/record format
remain. The remote endpoint must support the same Ollama metadata and Responses
interface; arbitrary OpenAI-compatible/vLLM services have **not** been validated.
Their compatibility must be checked rather than presumed.
