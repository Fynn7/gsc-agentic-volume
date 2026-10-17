# Desktop incident: global configuration and Responses Lite — 2026-10-17

**Desktop experiment launch is suspended.** The default entry no longer installs
user configuration or opens Desktop. Do not follow the former one-click Desktop
instructions. Codex Desktop remains the formal harness; OpenCode is not a fallback.
No new Desktop/model experiment was executed during this investigation.

## Confirmed evidence

Affected run: `runtime/desktop/runs/20261017-025636-0dfdd274/`.
Installed Desktop package: `OpenAI.Codex 26.1013.3656.0`; endpoint Ollama: `0.40.4`.

### Configuration incident

The old script deliberately installed the experiment at
`%USERPROFILE%/.codex/config.toml`. Its intended changes were:

- Top-level model `gpt-6.1-sol` to `qwen3.6:35b`, reasoning `high` to `none`,
  reviewer `guardian_subagent` to `user`.
- Added provider `volume_local`, its Responses URL, custom model catalog,
  16,384 context, zero project-document bytes and disabled web search.
- Added `mcp_servers.volume_renderer`, disabled `node_repl` and several
  shell/plugin/parallel-agent/memory features.

This changed the daily app's model list/provider and tools. A repository or Git
worktree boundary does not isolate this user configuration. The default design
was inappropriate for switching between daily coding and experiments.

Configuration SHA256 and size:

| Snapshot | SHA256 | Bytes |
| --- | --- | --- |
| `user-config.original.toml` | `3cf2aa7e091568a84712b20464976b4d5e43b8db83a3b33f3d6c1d0e9660846e` | 4306 |
| `codex-desktop.config.toml` (installed candidate) | `8ecd5d18e0435b39cc6e9474619f8d89f3451448ffd039bf89b53c47e3dd99b1` | 5537 |
| User's pre-manual-restore snapshot | `804e8fc7f2bf581b0f8d2f3d0510c2cacfaec6b19e8f781fb661591cf9d5323f` | 5742 |
| Current, after manual restore, at audit time | `f5322e8e2d8387e6fb7664e68fcb56099f677c2f8f372cbd31628073e888cae9` | 4306 |

Pre-manual-restore snapshot:
`%USERPROFILE%/.codex/config.toml.before-manual-restore-20261017-033423`.
Compared with the installed candidate it differs at exactly these six keys:

```text
mcp_servers.cua_repl.command
mcp_servers.cua_repl.enabled
mcp_servers.node_repl.enabled
mcp_servers.node_repl.env.NODE_REPL_INSTRUCTIONS_USE_CASE_BROWSER
mcp_servers.node_repl.env.NODE_REPL_INSTRUCTIONS_USE_CASE_CHROME
mcp_servers.node_repl.env.SKY_CUA_NATIVE_PIPE_DIRECTORY
```

`cua_repl` was added disabled, `node_repl.enabled=false` was removed, browser
instruction values changed and the native pipe changed. These are consistent with
Desktop's own runtime provisioning. Static inspection of the installed app's
`app.asar` found the same native-pipe/bootstrap configuration identifiers. This
is attribution evidence, not a filesystem audit identifying the exact writer.
Manual edits or another app component cannot be excluded from snapshots alone.

The unchanged installed-file hash was therefore no longer a reliable restoration
precondition after running Desktop. Refusing to overwrite a changed file was
correct; the missing behavior was recognizing a completed manual restoration and
closing stale `installed.json` state. The old restore also left
`preparation.json.installed=true` after successful restoration.

After the user's manual restoration, TOML semantics match the original at every
key except `mcp_servers.node_repl.env.SKY_CUA_NATIVE_PIPE_DIRECTORY`. The model,
provider, catalog, context and other experiment settings are all back to their
original values/absence. This dynamic pipe value is preserved; the repair does
not copy the old pipe or overwrite the live user configuration.

### Protocol incident

Recorded POST requests `http-005` and `http-006` both went to `/v1/responses`.

| Evidence | Observed |
| --- | --- |
| Model requested | `qwen3.6:35b` |
| Generated model catalog | `use_responses_lite: true` |
| Top-level `tools` | Absent |
| First `input` item | `type: additional_tools`, developer role |
| Tool representation | Nested `namespace` entries |
| Proxy behavior | `input` identical before and after forwarding |
| Endpoint response | HTTP 400, `input[0]: unknown input item type: "additional_tools"` |
| Response duration | 0.006767 s / 0.033968 s |
| MCP tool calls recorded | 0 |

The custom catalog generator copied the first cached GPT model, removed some
capability fields, but failed to override its `use_responses_lite=true` setting.
The generated Qwen catalog and actual Lite-shaped requests directly establish
the bad metadata/wire combination. The flag-to-client-branch attribution is
consistent with those records; turning it off in a future real Desktop session
has **not** yet been validated.

The parser rejection is at **Ollama's Responses input boundary**, after Desktop
request assembly and proxy forwarding, before model inference or rendering.
This is an observed protocol incompatibility of this request shape with this
Ollama version. It does not establish that every Desktop/local-model combination
is incompatible, or that Qwen has inadequate vision/tool ability.

Historical Task A CLI request
`runtime/local-vlm-task-a/http/http-001.request.bin` contains ordinary messages
and six top-level tools, without `additional_tools`. Its successful experiment
evidence is preserved. CLI success with that different request format cannot
validate the current Desktop format.

The recorded GET `/v1/responses` requests received 405 (POST is required). This
is a separate transport/probing observation, not the cause of the recorded POST
400 errors. Exact headers were not recorded by the old proxy, so the specific
cause of those GET attempts is not established.

The tool namespaces also included `node_repl`, and the second request included
many app connectors despite the generated experiment settings. Tool isolation
was therefore not achieved either. Do not claim `features.plugins=false` alone
provides complete Desktop integration isolation.

## Minimal fixes implemented

1. **Retire global installation.** Python `install` refuses before touching any
   user file. Default `Start-VolumeDesktop.ps1` refuses before preparation,
   service startup, installation or GUI launch. `-NoOpen` is not an escape hatch.
   `-PrepareOnly`, `-ServicesOnly`, `-Collect`, `-Stop` and legacy `-Restore` remain
   explicit support actions; none launches a Codex task.
2. **Reconcile manual restore without a config write.** Verify the original
   backup's hash; compare complete TOML semantics. Only an exact semantic match
   or a difference limited to the known dynamic pipe key can close stale state.
   Preserve the live bytes; archive the installation closure, mark
   `preparation.json.installed=false`, then remove the stale pointer. Other
   changes, invalid TOML or corrupted backups remain blocked without overwrites.
   Repeated restore with no pending installation is a no-op. Exact untouched
   legacy installations retain the explicit, hash-guarded restore path.
3. **Prepare an independent candidate only.** A newly prepared run contains
   `desktop-home/config.toml`, generated from an empty TOML rather than the daily
   user's file. It has only the intended renderer MCP configuration and does not
   copy daily auth, notifications or integrations. `use_responses_lite` is
   explicitly false. Original failed catalogs/requests are not edited.
4. **No protocol masking.** No adapter removes `additional_tools`, flattens
   namespaces, fabricates tool definitions or changes recorded model input.
   Such a translation would require separate contract/image tests and is outside
   this minimal repair.

Reproduce the read-only audit (writes only the requested redacted report):

```powershell
cd D:\fynns_local_ws\gsc-agentic-volume
& "$env:USERPROFILE\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -B scripts\diagnose_desktop.py --run runtime\desktop\runs\20261017-025636-0dfdd274 --output runtime\desktop\incidents\20261017\audit.json
```

The report contains hashes, changed keys, recorded request types, namespace names
and errors, not full user configurations or prompt/image bodies. Keep runtime
private; it remains ignored. The original installation and failed run remain
available for comparison even after state reconciliation.

Verification completed during this repair:

- All 18 tests passed, including global-install rejection, exact legacy restore,
  manual-restore reconciliation with only the volatile pipe change, refusal of
  other user changes/corrupted backups, and empty isolated candidate generation
  with `use_responses_lite=false`.
- Actual state reconciliation preserved the current user config SHA256 exactly
  (`f5322e8e2d8387e6fb7664e68fcb56099f677c2f8f372cbd31628073e888cae9`).
  `installed.json` was removed only after recording the closure, and the failed
  run's `preparation.json.installed` is now false. See
  `runtime/desktop/incidents/20261017/state-repair.json` and the failed run's
  `installation-closed.json`.
- The normal startup command was actually checked: it stops with the suspension
  error before configuration installation or service/GUI startup.
- The daily model cache contains nine models again; no model-cache edits were
  made. Daily GUI model switching itself was not automated or claimed verified.

## Isolation boundary and next gate

Official docs describe `CODEX_HOME` as the config/state root, and explicitly
disallow project-local overrides of provider keys:
[advanced configuration](https://learn.chatgpt.com/docs/config-file/config-advanced),
[configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference).
Neither fact alone verifies isolated Windows Desktop startup.

Read-only static inspection of this installed app found:

- A resolver using `process.env.CODEX_HOME` or the user's `.codex` directory.
- Startup environment loading; explicit home preservation depends on
  `CODEX_ELECTRON_USER_DATA_PATH` also being set in the inspected implementation.

These are implementation clues, not a supported or runtime-verified isolation
contract. The former launcher used Explorer `shell:AppsFolder` activation, not an
explicitly tested child process environment. An existing Desktop instance and
its persisted app state add further uncertainty. Merely setting `CODEX_HOME` in
a PowerShell window or selecting a new project is not accepted isolation proof.

**Reliable same-account Desktop isolation is currently unverified.** There is no
replacement automatic Desktop launcher in this fix. A separate Windows user or
VM with its own profile is the preferred stronger isolation boundary to evaluate;
its Desktop login, GPU/volume access and account permissions still need manual
verification. No such account/VM was created or tested here.

Before any new Desktop model experiment:

1. Verify the daily config remains unchanged while the isolated app starts and
   shuts down; verify both the actual app-server home and GUI state directories.
2. Verify no daily MCP/app namespaces leak into its tool inventory. A GUI label
   is insufficient.
3. Verify the new request actually has supported top-level tools and no
   `additional_tools`; a candidate JSON flag alone is not a pass.
4. Only then repeat real PNG reception and image-driven next-action acceptance.

The Worker cannot run a recursive Codex experiment to close these gates.
Desktop remains the formal harness; any onsite H200 migration also waits for
these gates. Changing GPU does not resolve an input-parser incompatibility.
