# Desktop preparation evidence — 2026-10-17

Formal harness fixed by the user: Codex Desktop. The Worker did not submit a
Codex task, launch a nested Codex harness, automate the Codex GUI, or install the
prepared user configuration. Human acceptance procedure: [CODEX_DESKTOP.md](CODEX_DESKTOP.md).

Read-only Windows package inventory identified `OpenAI.Codex` version
`26.1013.3656.0`. This verifies installation, not custom-provider compatibility.

## Tested support interfaces

- 13 unit tests passed: two existing capability tests, six external-tool boundary/UTF8 tests
  and five new configuration backup/restore/concurrent edit, TOML merge, image
  bytes/hash, and independent MCP error-journal tests.
- `Start-VolumeDesktop.ps1 -ServicesOnly` actually started the independent support
  runtime and canonical renderer. It left the live user configuration unchanged.
- Real stdio MCP test completed in 15.960 s, exposing six tools and issuing eleven
  tool calls. It imported the actual development volume, obtained native initial
  and two modified PNGs, read a real voxel slice, rejected stale scene revision,
  preserved all unrelated fields in the sparse camera patch, saved a selected
  existing frame, and began/finished a second session on the same MCP connection.
- Every returned PNG's bytes matched the producing service SHA256. Final selected
  PNG bytes matched the corresponding frame. A deliberately wrong model request
  was rejected by the proxy before forwarding. No real inference occurred.
- The interface scope is explicitly `scripted-interface-test-NOT-VLM-experiment`;
  actions were mechanically specified by the test, not image-driven model actions.
  Its PNG is a real native artifact, not a GUI screenshot or VLM quality result.

Local, gitignored evidence:

```text
runtime/desktop/runs/20261017-011440-f785940b/interface-tests/interface-report.json
runtime/desktop/runs/20261017-011440-f785940b/interface-tests/mcp-transcript.json
runtime/desktop/runs/20261017-011440-f785940b/interface-tests/evidence.html
runtime/desktop/runs/20261017-011440-f785940b/interface-tests/mcp/6c4eeed2-18b4-4581-a3bd-700b884bf7b9/
runtime/64c6b8e2-7d29-48fa-b475-05df006d19d4/final/image.png
runtime/64c6b8e2-7d29-48fa-b475-05df006d19d4/final/scene.json
```

The parent support run is also marked interface-test scope. Its rejected test
POST is not a formal model call. Local CPU/RAM/GPU and Ollama PS sampling worked.
The model was unloaded during this test, so this turn provides no new VLM speed,
offload or visual-feedback measurement. Earlier measured CLI results retain their
own provenance and limitations in LOCAL_VLM_EVIDENCE.md.

## Pending human Desktop acceptance

| Check | Status |
| --- | --- |
| Installed Desktop accepts generated custom provider/catalog | Not executed |
| Model picker selects Qwen and actual inference reaches local recorder/Ollama | Not executed |
| Desktop connects MCP and displays useful tool traces/images | Not executed |
| Task A exact parameter operation by Desktop's local model | Not executed |
| Task B model chooses second action after receiving the preceding actual image | Not executed |
| Final target visibility judged from actual PNG by independent reviewer | Not executed |
| H200 server login, GPU inference and tunnel | Not available; onsite verification required |

These are not silently replaced with CLI or interface-test passes. No formal
Desktop GUI screenshot exists yet; the human saves screenshots under the run's
`gui/` directory. A model's own claim cannot close these checks.

## Archived exploration

The owned OpenCode 2.0.31 server was stopped. Its server/GUI pairing exploration
is stored separately under `runtime/exploratory/opencode-20261017/`, with a
classification record. No visualization prompt was submitted and no model
experiment was executed there. It is not a fallback or part of Codex data.
Private connection/session details remain ignored; do not publish that directory.
