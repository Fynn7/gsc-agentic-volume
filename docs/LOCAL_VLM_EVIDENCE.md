# Local high-quality VLM development evidence — 2026-10-16

These are real local development runs, not held-out thesis results, semantic
success rates, or a harness benchmark. The user confirmed that implementation
belongs in this repository; the old `gsc-live-preview` checkout stayed read-only.
Phase 1 and the GUI were retained. No new agent decision loop was implemented.
Operational instructions and H200 prerequisites: [LOCAL_VLM.md](LOCAL_VLM.md).

## G0: independently checked environment

- Windows host: Intel i5-12400F, 6 cores / 12 logical CPUs; 34,205,974,528 bytes
  physical RAM (31.86 GiB); NVIDIA RTX 4060, 8,188 MiB VRAM, driver 560.94.
- Installed/running inference: Ollama 0.40.4. It initially was not listening.
  Sandbox-denied access to its user log directory was an environment failure,
  resolved by starting the installed service with redirected local logs.
- `qwen3.6:35b`: digest
  `25eb3f4af70f7d94b022e3104298884064f3ee64756be7abd8bcb6e453915531`,
  GGUF Q4_K_M, architecture `qwen35moe`, 35,505,251,456 model parameters.
  Ollama's display rounds the parameter label to 36.0B; the requested tag remains
  35b. The inventory includes a real CLIP/Qwen3VL-merger projector with
  446,571,248 parameters and reports completion/vision/tools/thinking capabilities.
  It is local, with no remote model/host. The model package is 23,947,173,606 bytes.
- Installed Codex CLI: 0.159.0. OpenCode v2.0.31 was inventoried but not benchmarked:
  Codex ultimately met the required multimodal loop, so no further harness was needed.
- Runtime context: **16,384**, confirmed by `/api/ps`; advertised maximum is
  262,144, which is not the allocated context. Flash attention, q8_0 KV,
  one parallel request, temperature 0 and thinking disabled were used.
- Native binary SHA-256:
  `897ee57f42a92c5f94ef2099e0a4eb6716dcb2f17fd53c5fcaa53fb1d605a97e`.
  Rendering remained 512 x 512 with the existing deterministic scene validator
  and native readback. Perspective and masks remain unsupported.

Fresh G0 native rendering took 2.563 s. The first direct image call took 32.796 s,
including 26.486 s loading, 2.972 s prompt evaluation and 3.300 s generation;
first content token arrived at 29.5 s. Usage was 315 prompt / 101 output tokens
(approximately 30.6 generated tokens/s). The VLM described visible contour-like
geometry but also made unsupported claims about occlusion: this is not semantic
acceptance. A separate initial-image Codex diagnostic also completed locally.

Ollama loading logs report approximately **4,504 MiB CUDA model buffers and
16,940 MiB CPU model buffers**, with expert tensors overflowing to host RAM.
The log's "41/42 layers offloaded" must not be read as all weights fitting VRAM:
its fitting report explicitly has 34 overflowing layers. `/api/ps` reported
5,541,745,130 bytes of model VRAM allocation. The model is practical here through
offload; it is not a GPU-resident 35B deployment. It was selected because the
existing high-quality candidate actually worked, not because this run proves it
is universally the highest-quality model this hardware could possibly run.

G0 requests, outputs, inventory, frames and telemetry were archived under
`runtime/local-vlm-g0/`. Original absolute paths in those copied diagnostic
artifacts still refer to the task's local audit output directory. No dataset or
image was sent to a paid model service.

## G1: Codex + MCP + local 35B + Native Raycaster

The original HTTP service could only start the Phase 1 driver. It did not expose
external-agent scene operations. The implementation added a small tool API on its
existing `Session` objects and an optional stdio MCP adapter. All rendering still
calls `src/renderer.py`; the MCP adapter contains no renderer or model logic.

Successful run: `runtime/local-vlm-codex-g1-images/`; service session
`21b88afe-52f0-4dbc-92c3-65d5346b1c88`.

The task asked for two new views selected after inspecting the latest image,
preserving TF/crop/translation. The model chose `[90,0,0]`, then `[0,90,0]` after
receiving the first new PNG, and saved revision 2. These choices were model
outputs, not launcher presets. The actual model wire requests contain image
content matching **all three actual native frames**. Native state matched the
requested scenes. Model-free replay reproduced all three PNGs byte-for-byte.

Five real model requests took 31.27, 12.62, 10.63, 34.09 and 7.02 seconds.
Harness phase: 101.16 s; approximate end-to-end archive timing: 103.87 s.
Total usage: 52,736 input tokens (40,262 cached) and 1,184 output tokens.
The chosen final profile view is narrow and is not independently established as
the most informative view. Model references to anatomical directions are
unsupported. Transport/execution/replay passed; semantic usefulness is unverified.

### Real failures retained and classified

- `runtime/local-vlm-codex-g1/`: Windows npm `.cmd` truncated a multiline prompt,
  discarding subsequent CLI flags. Codex attempted its default OpenAI endpoint
  without authentication and received 401 errors; no model completion or VLM
  result was obtained. This is excluded from local-model measurements. The
  launcher now uses the native executable, explicit local provider, isolated home
  and a fail-closed child proxy. No paid API was used to complete an experiment.
- `runtime/local-vlm-codex-g1-native/`: Codex rejected configuration overriding
  its reserved built-in `ollama` provider. No model request occurred. A named
  local Responses provider resolved that configuration error.
- `runtime/local-vlm-codex-g1-recorded/`: model selected `inspect_scene`, but
  default MCP approval required interaction in a noninteractive run. Five local
  model calls produced repeated denied attempts. Explicit preapproval of only
  the four bounded, user-authorized renderer tools resolved the problem.
- Unknown-model metadata and model-tag telemetry warnings remain in Codex logs.
  Actual image/tool requests work despite these warnings; support for arbitrary
  local models or future Codex versions is not inferred.
- Windows redirected Python stdin initially used the host code page, corrupting
  Unicode reasons/summaries in the service-side trace. Raw harness/HTTP records
  preserved the original text, and numeric scene operations and image bytes were
  unaffected. MCP stdin/stdout now explicitly use UTF-8. Later launcher acceptance
  exercised the corrected adapter. Historical traces were not rewritten.

These are different failure layers; none establishes that the VLM lacks vision.
No protocol-translating compatibility proxy or replacement harness was needed.
The recorder forwards Responses requests unchanged except for declared inference
options. MCP image blocks and Codex's multimodal function outputs were accepted
by the installed Ollama version in these actual runs.

## G3: actual user tasks

The source volume for A/B is the existing anonymized 256 x 256 x 44 scalar volume,
hash `e226bd84e0f6869daaf28d975b60a83688a0e9980d735592137f836b8580f224`.
It is development data already used previously, not unseen data. Its physical
spacing and anatomical orientation are unknown. Source names/catalog labels were
not included in tool feedback. Full natural-language tasks and actual generation
conditions are in each `config.json`; every model request/response and tool call
is retained. Development changes below prevent a claim of strict cross-run comparability.

| Run | Model calls | Actual new observations | Approx. full time | Outcome |
| --- | ---: | --- | ---: | --- |
| Task A `local-vlm-task-a` | 4 | 1 native render | 66.31 s | Exact camera `[25,-35,10]`; every unrelated scene parameter preserved; final PNG saved |
| Launcher acceptance `local-vlm-launcher-acceptance` | 4 | 1 native render | 71.22 s | Same parameter assertion passed through delivered startup script and UTF-8 adapter |
| Task B first pilot `local-vlm-task-b` | 10 | 3 native renders + 1 real slice | 206.32 s | Feedback exploration and save completed; visual target isolation unverified; memory/budget errors retained |
| Task B renderer-only context `local-vlm-task-b-context` | 7 | 3 native renders + 1 real slice | 118.30 s | TF/camera/slice/crop loop and save completed; final crop visibly truncates the object, so useful internal isolation is not accepted |
| Task C `local-vlm-task-c` | 0 | 0 | Import failed before inference | Existing Christmas Present QVis LZ4 rejected by importer |

Full-time estimates for the first pilots use Windows archive creation to recorded
runner finish, excluding CLI startup. Harness-only times are 63.58, 68.46 and
203.63 s for A, launcher acceptance and B respectively. The delivered recorder now
also explicitly stores run-start and total time for future runs.

Task A service session: `65ed79b1-4ba0-4a23-9a25-5436c02a120f`.
Launcher acceptance session: `7f1e905c-dcff-4d56-8e9d-a433102c8e81`.
Both have `parameter-verification.json` in their run archive and `final/` under
their service session. The deterministic assertion compares the entire scene,
allowing only the requested rotation and revision change. Native rotation matrix
readback is independently checked by the executor.

Task B first-pilot session: `5cfd1eac-bb8f-4c84-a6f7-dde3184160db`.
The model widened TF, examined a new view, rotated 60 degrees around Y, widened TF
again, then requested z=22. A response explicitly observed that width 0.8 looked
very similar to width 0.6 and selected a slice because exterior material still
dominated. All four native revisions and the real slice pixels are present in
the recorded model inputs. All four native frames replayed byte-for-byte.

This pilot triggered Codex context compaction on request 6 (80.38 s). The compacting
request did contain the slice image, and its generated summary initially retained
the slice fact. In subsequent continuation, the model asked for another slice
after exhausting the budget and later falsely claimed no slice had been obtained.
The service rejected the extra request. This is a context/continuation reliability
failure, not evidence of a missing image transport. The model's tissue/identity
labels and internal-structure claims are unverified visual hypotheses. Manual
developer inspection finds softer grayscale variation and changed projection;
it does not establish isolation of a named internal structure.

Task C's exact retained error is
`unsupported_qvis_compression: QVis lz4 payloads are not supported in this milestone`.
The native renderer can load its bundled dataset, but the canonical numeric import
path cannot prepare this input. `failure.json` retains the service response.
No model was tested on it, and no correct crop/TF or target visibility is claimed.
No decoder, mask feature, or renderer change was improvised to bypass the contract.

### Targeted context correction and final acceptance condition

The initial renderer task inherited roughly 2,700 tokens of repository UI coding
instructions. These are useful to a developer but irrelevant to the renderer-only
child. The launcher now sets `project_doc_max_bytes=0` by default; task instructions
and tool contracts remain explicit. This is a harness context configuration change,
not a dataset-specific prompt repair. The exact same Task B was rerun through the
delivered startup script with the corrected UTF-8 adapter. Source hashes, Codex
version and complete conditions are recorded in its manifest. Do not interpret the
different timings as a controlled speed/quality benchmark.

Final-condition run: `runtime/local-vlm-task-b-context/`, session
`8914eef2-c1d2-4fd7-a22a-d8a31445d813`.
The model chose TF width 0.6, Y rotation 90 degrees, z=22, then crop
`[60,196,40,216,8,36]`, saving revision 3. The code did not prescribe these values.
All four native revisions and the actual slice pixels are verified in model
requests; all four native frames replayed byte-for-byte. There were no tool errors
or automatic compaction requests in this run; the summary retained the slice.
Per-call latencies were 23.280, 13.675, 12.776, 11.746, 14.348, 19.035 and
11.313 seconds. Usage: 64,125 input tokens (51,880 cached), 1,221 output tokens;
individual input contexts ranged from 5,630 to 12,221 tokens. Explicit end-to-end
time was 118.296 s (harness phase 115.580 s).

**The final visual result is still weak.** Independent developer image inspection
shows a thin edge-on projection with substantial structure clipped away. Crop
clips voxels; it does not automatically zoom, recenter or fit the remaining object.
The model's assertion that the crop maximizes internal detail is not accepted.
Its named tissue/anatomy statements also lack an independent reference. This run
validates real perception/action plumbing and correct execution of self-selected
parameters; it does not pass useful internal-structure isolation. The bad final
image remains saved exactly as selected, alongside all prior frames and the slice.

### Blind visual discrimination

`runtime/local-vlm-codex-g1-images/blind-visual-control/` contains two **fresh** native
views and two direct Ollama diagnostics with reversed A/B order. The task asks
which foreground is wider relative to height. Scene parameters, file names,
pixel bounding boxes and the answer are withheld from the model. Both responses
were correct against deterministic pixel measurements (7.063 s and 6.297 s;
597 input tokens each, 59/50 output tokens). This supports actual image reading
without a textual answer leak. It is not semantic ground truth, an agent benchmark,
or a controlled estimate of the causal benefit of feedback.

## Resource and quality boundaries

For G1/A/first B, whole-host sampled peak GPU memory was 7,409/7,406/7,497 MiB;
peak used system RAM was 28.43/28.93/30.34 GiB. Mean host CPU utilization was
33.6/27.1/31.9%; maxima were 58.3/53.7/56.3%. Launcher acceptance reached 7,780 MiB
GPU and 30.88 GiB system RAM. These include other host applications and native
renders, are sampled rather than continuous peaks, and are not per-process model
memory claims. Large volumes, larger contexts and concurrent applications have
limited local memory headroom. Detailed samples and every individual call latency
are in `telemetry.jsonl` and `verification.json`.
The final renderer-only-context B run reached 7,720 MiB GPU / 31.09 GiB used system
RAM; mean/max sampled host CPU utilization was 42.9/85.9%. Larger context/volume
settings need explicit memory validation rather than an assumption of headroom.

The 35B model can run the required tool/image loop on this machine without
downgrading to 9B. This does not establish accurate anatomy, optimal TF/crop,
stable long-task memory, or semantic task success. There were no cloud-model
replacements, hidden retries or hand-authored parameter sequences. Development
agent image inspection is explicitly separate from local VLM output and was never
fed back as the model's observation or scientific reference.

## G4 and next experiments

The Windows startup/configuration, same Session API, standard MCP image adapter,
raw transport archive, timing/usage telemetry and model-free replay are available.
The H200 workflow keeps renderer/data/harness on Windows and changes the inference
endpoint/model/context/timeouts. The operations guide lists minimum SSH/service
checks and requires the same image and Task A gates before further work.
No school login, H200 quota, inference deployment or network compatibility was
verified. Only Ollama's actual inventory/Responses service has been tested.

Next narrow work: provide a tested canonical conversion path for the compressed
Christmas Present input and assess the model's failure to reject unhelpful crops;
then freeze one model/tool/context condition and run a few independently reviewed
occlusion/crop tasks. Use independent references for named structure visibility.
Retain both successful and failed explorations, and report compaction explicitly.

The existing capability and new tool-boundary suites pass (8 tests), including a
redirected-codepage Unicode regression. Actual native
state/image checks and model-free replay pass, and startup-script acceptance
completed. No UI migration, package installation, commit, push or publication was
performed. Old repository HEAD remains
`926eed6018d5c7547f4be14b15bf1e620efa7195` with clean Git status.
