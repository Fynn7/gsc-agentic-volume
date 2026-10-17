# Isolated Codex Desktop: human-operated startup and acceptance

Preferred entry: `Start-IsolatedVolumeDesktop.ps1`. It never installs into the
daily `%USERPROFILE%/.codex/config.toml` and never uses Explorer activation.
The old global-install launcher remains retired for GUI use.

**Status:** preparation and nonrecursive tests completed; actual Desktop startup,
single-instance behavior, request format and image feedback remain human gates.
The Worker did not launch a Codex GUI/CLI instance or submit an agent prompt.
Same-account profile separation is not a Windows security sandbox: the application
still has the current user's privileges, and common binaries, runtime caches and
possibly OS credential storage can be shared. Use the separate-account procedure
below when a stronger boundary is required or any same-account gate fails.

## Preparation evidence

- 26 tests passed. The real Windows observer test launched only a benign Python
  child and verified its environment and open profile file. Desktop launch-command
  tests mocked process creation; they are not GUI startup evidence.
- A real `-PrepareOnly` invocation produced private config with one renderer MCP
  and `use_responses_lite=false`. Daily config hashes at preparation and after
  checks matched: `1566de2c6871460d02dbfff73b61e297b5bd66a8abaac10a4e9aa8f98f51ca94`.
- Installed ASAR source hash:
  `163a18c84db57ad5c50222b313dc4d11d45febc03ec065b516ba9f0380f2eb3f`.
- Local evidence: `runtime/desktop/isolation-preparation/static-audit.json` and
  `verification.json`. Every actual Desktop gate is explicitly marked pending.
- Historical CLI/Native/image evidence and the failed Desktop run remain intact.
  No new model request was sent in this preparation.

## Failed startup recovery and bounded retry

The normal isolated entry now recovers an owned unused failed startup before
starting a fresh run. Each failed attempt cleans up its verified private GUI,
recorder and renderer processes and retries once (two attempts by default;
`-StartAttempts 1` disables the retry). Original logs and configurations remain.
Support ownership is recorded immediately at process creation; renderer ownership
is recorded before later initialization can fail. Early support exit fails fast.

PID, executable, command/config path, private-profile markers and creation time
are checked before termination. Healthy unused Desktop instances are reused;
model/tool activity prevents automatic discard. Daily file changes stop the
current retry sequence, and unknown ownership is never overridden. Existing
Ollama and daily Codex processes are not terminated. After a failure was fully
closed and archived, a later explicit launch creates a new run/baseline without
rewriting the old one. This is not automatic continuation after an isolation alert.

Use `-ServicesOnly` for nonrecursive support lifecycle diagnostics; it never
launches Desktop or inference. The Worker tested real support startup/cleanup,
a two-attempt unavailable-model-metadata failure, and owned benign Python process
termination/PID-reuse protection. All 34 tests passed; no new Codex or model task
was run. Evidence: `runtime/desktop/isolation-preparation/startup-recovery-verification.json`.
Errors and cleanup identities are retained in `startup-recovery.json`,
`startup-recovery-events.jsonl`, and `desktop-launch-failure.json` where applicable.

## First run: five bounded stages

1. End the Worker, fully quit the daily Desktop for the **first** test, and open
   external PowerShell. Keeping daily Desktop closed avoids unrelated daily
   preference writes being mistaken for isolation breaches. Run:

   ```powershell
   cd D:\fynns_local_ws\gsc-agentic-volume
   powershell -NoProfile -ExecutionPolicy Bypass -File .\Start-IsolatedVolumeDesktop.ps1
   ```

   This starts the existing passive recorder/renderer and launches the registered
   `ChatGPT.exe` as an explicit child with a private environment. There is no
   `codex exec`, no app command that creates a turn, and no submitted prompt.
   Do not click the ordinary app shortcut expecting the experimental profile.

2. In the **new experiment window**, complete any first-use account login or
   native sandbox setup manually. Open the Codex view; do not submit a task yet.
   In the external terminal:

   ```powershell
   .\Start-IsolatedVolumeDesktop.ps1 -Verify
   ```

   Require `passed: true`. The check reads actual Windows process environments
   and open disk handles. It requires one new GUI main, native app-server,
   private GUI files and private home files actually open, the expected environment,
   unchanged protected daily files, and no observed daily home/GUI handles.
   Missing evidence is a failure/pending result, not a pass. Startup may not have
   completed; retry after the Codex view finishes initialization. Access-denied
   probes are reported; do not elevate Desktop or loosen Windows protections just
   to obtain a pass. If necessary use the separate-account method.

3. Still before an Agent task, test same-profile single-instance behavior:

   ```powershell
   .\Start-IsolatedVolumeDesktop.ps1 -CheckSingleInstance
   ```

   This human-only action starts a second child with the SAME private profile and
   no task. It requires that child to exit successfully, the original experiment
   main PID to remain, and isolation checks still to pass. It never kills a
   process by name. Extra windows or handoff to a daily window are failures.
   This proves only the observed private-profile instance behavior, not that
   daily/experiment sessions have been validated concurrently.

4. In the experiment Codex view, create a local Chat in
   `D:\fynns_local_ws\gsc-agentic-volume\runtime\desktop\workspace`. Select
   **Local VLM: qwen3.6:35b** / `qwen3.6:35b`. Paste
   [03-isolated-first-check.txt](../experiments/desktop-prompts/03-isolated-first-check.txt).
   This calls `desktop_status`, obtains a real native initial image and saves
   frame 0 without changing scene parameters. If the model is absent or the
   image is unavailable, preserve that failure; do not use a cloud/smaller model.

   After the response completes:

   ```powershell
   .\Start-IsolatedVolumeDesktop.ps1 -Protocol
   .\Start-IsolatedVolumeDesktop.ps1 -Collect
   $runDir=Get-Content .\runtime\desktop\LATEST.txt
   Start-Process (Join-Path $runDir 'evidence.html')
   ```

   Require protocol `passed: true`, actual PNG transport evidence, and visible GUI
   tool images. A green protocol report alone is not a visual pass. Save GUI
   screenshots in `<run>/gui/`; the Worker does not automate Codex UI/export.

5. After those gates, run the existing fixed
   [Task A](../experiments/desktop-prompts/01-parameters.txt) then
   [Task B](../experiments/desktop-prompts/02-feedback.txt) in that Chat. Each
   begins a fresh scene. Task B requires two image-driven native renders within
   four observations. Collect again. No target-specific answer parameters are
   supplied. Check final actual visibility independently; retain poor results.

If PowerShell blocks later `.ps1` commands, open an external shell with
`powershell -NoProfile -ExecutionPolicy Bypass` and run them there. No persistent
machine execution-policy change is needed.

## Exit and subsequent runs

1. Collect evidence before exiting. Fully quit the **experiment** Desktop using
   its UI; do not send a task to make it close itself.
2. Run `./Start-IsolatedVolumeDesktop.ps1 -Stop`. It checks that the profile's
   GUI/app-server processes exited and daily protected files still match, then
   stops the support service. **No Restore is necessary.** It does not kill daily
   processes or silently restore a changed daily file.
3. A new ordinary launch creates a fresh private run/profile. Previous profiles,
   credentials, logs and images are retained. First-use login may be needed again;
   no daily auth or browser state is copied. A second normal launch with a healthy unused Desktop reuses it; with an owned
   unused failed startup it recovers automatically. An experiment with model/tool
   activity is preserved and is not automatically restarted.

If launch fails after support starts, use the legacy support-only
`./Start-VolumeDesktop.ps1 -Stop` to stop that support service; this is not a
global-config restore. Close any failed experiment GUI manually first. Preserve
the failed run. Never delete profiles or overwrite daily config to recover.

## How isolation is established and limited

Each run has:

```text
<run>/desktop-home/             CODEX_HOME and CODEX_SQLITE_HOME
<run>/electron-user-data/       CODEX_ELECTRON_USER_DATA_PATH and --user-data-dir
<run>/config.json               immutable tool/model/service configuration
```

The launcher passes a child-specific environment and removes inherited Codex
tokens, API keys, old helper pipes and Node REPL settings. It never changes
system/user environment variables or copies daily auth. The private config is
generated from an empty TOML and explicitly sets `use_responses_lite=false` in
the private model catalog. Native renderer/service and tool contracts are reused.

Read-only static inspection of installed package `26.1013.3656.0` found a GUI
directory resolver for the explicit variable, assignment of Electron `userData`
before acquiring the instance lock, and preservation of `CODEX_HOME` during
startup environment loading. The launcher records the ASAR hash and refuses an
unrecognized implementation. It does not patch ASAR, disable locks, register a
new protocol handler, or bypass package/Windows security.

These static checks are only preconditions. Actual runtime evidence is:

| File | Evidence |
| --- | --- |
| `desktop-static-audit.json` | Installed executable, ASAR hash and implementation snippets |
| `desktop-launch.json` | Requested child PID, executable and four whitelisted environment values |
| `isolation-baseline.json` | Daily config/preference hashes and baseline process IDs |
| `isolation-running.json` | Actual GUI/app-server environment and relevant open state-file handles |
| `single-instance.json` | Second child's exit and unchanged experiment main PID |
| `isolation-exit.json` | Profile processes exited; protected files unchanged |
| `isolation-alerts.jsonl` | Latched protected-file breach; no automatic restore |
| `protocol-acceptance.json` | Actual request types, model, tool namespaces, status and MCP call count |

`isolation-baseline.json` protects the daily config and selected GUI preference
files discovered from existing process arguments plus the standard Codex profile
location. It is not a complete filesystem audit. Open-handle inspection is a
snapshot and cannot prove absence of every transient access. Shared caches,
Windows registration and OS credential-manager isolation are not claimed.

Before each model POST and renderer MCP call, a guard compares the protected
files and blocks further inference/tools on a change, latching an alert. It does
not prevent the Desktop process itself from writing a file, and it cannot stop
a request already in flight. It never restores user files or kills arbitrary
processes. An active daily app may legitimately change its own files and trigger
this guard; concurrent use is not yet validated. For a stricter boundary use
the separate Windows user below instead of weakening the guard.

## Real protocol and image acceptance

`-Protocol` reads recorded input, not the model's self-report. It requires:

- At least one completed Responses POST for the configured model.
- No `additional_tools` input item in any recorded POST.
- Actual top-level tools, HTTP 200, and a real MCP invocation.
- No unexpected tool namespaces or other MCP integrations.

Any remaining Lite item, parser error, leaked `node_repl`/connector namespace,
missing request or missing MCP call fails the check. Nothing is translated or
stripped to manufacture success. Inspect `http/*.request.bin`, `*.forwarded.bin`,
`*.response.bin` and `*.metadata.json`; the original error evidence is retained.

`-Collect` reuses the evidence collector. Require a real native PNG returned by
MCP to occur in actual model input (`exactPngTransportMatches`). Zero exact
matches is inconclusive if Desktop reencodes images and needs pixel-level
investigation; do not pass it automatically. Review Task B's ordered images and
decisions, not just image hashes or the model's success claim. Canonical final
PNGs/Scene/native readback are in `runtime/<session>/final/`; `sessions.jsonl`
provides exact paths. All logs remain ignored and private.

## H200 and other volumes

The same entry supports:

```powershell
.\Start-IsolatedVolumeDesktop.ps1 -VolumePath 'D:\data\volume.dat'
.\Start-IsolatedVolumeDesktop.ps1 -Mode H200
.\Start-IsolatedVolumeDesktop.ps1 -Mode H200 -Config 'D:\private\h200.json'
```

End the prior run first. Use the documented SSH tunnel at local port 11435 before
H200 mode; model proxy stays 11436 and renderer 8766. See the SSH/telemetry
sections of [the original operations reference](CODEX_DESKTOP.md); its old global
startup/restore instructions are obsolete, this entry replaces them. No H200
connection is claimed. QVis LZ4 import remains unsupported. H200 does not resolve
a client protocol mismatch. First keep Qwen/dataset/prompts/budget fixed, then
deliberately change models/context in separate recorded runs.

## If same-account verification fails: separate Windows user

This is an actionable stronger OS-profile boundary, not something already tested
or automatically provisioned. Use it immediately if policy rejects the explicit
child, instance handoff occurs, required runtime evidence cannot be obtained, or
daily settings change. Do not disable locks/sandbox, elevate Desktop, patch the
package, or relabel a failed check as passed.

1. In an administrator terminal create a standard experiment account (manual;
   `*` prompts for a password rather than putting it into the command):

   ```powershell
   net user VolumeVlm * /add
   New-Item -ItemType Directory -Force D:\VolumeVlmExperiment | Out-Null
   $experimentAccount="$env:COMPUTERNAME\VolumeVlm"
   icacls D:\VolumeVlmExperiment /grant "${experimentAccount}:(OI)(CI)M"
   ```

   Do not add it to Administrators or grant access to the daily user's entire
   profile. If account creation is managed, ask the administrator for this
   standard account and only the dedicated experiment directory permissions.

2. Copy the new repo and default development sample into that dedicated folder;
   do not copy daily auth, existing experiment logs, secrets, models or the frozen
   old repo. Run as the current owner/administrator:

   ```powershell
   robocopy D:\fynns_local_ws\gsc-agentic-volume D:\VolumeVlmExperiment\gsc-agentic-volume /E /XD .git runtime node_modules .venv /XF .git .env .env.* /R:1 /W:1
   robocopy D:\fynns_local_ws\gsc-agentic-volume\runtime\gate-volume D:\VolumeVlmExperiment\gsc-agentic-volume\runtime\gate-volume render.dat render.raw /R:1 /W:1
   ```

   Robocopy codes 0–7 are nonfatal; 8 or higher means copy failure. These commands
   do not use `/MIR` or delete source data. The existing built native EXE must be
   present at `VirtualSampling\Raycaster\VS\x64\Release\Raycaster.exe` in the copy.

3. End the Worker and quit daily Desktop. Keep local Ollama running under the
   original account; **switch user** rather than logging that account out if it
   owns the inference process. Sign in as VolumeVlm. Install/register Desktop for
   that user using the official Windows installation flow, for example:

   ```powershell
   winget install --id 9PLM9XGG6VKS -s msstore
   ```

   Open it once, perform manual account/native-sandbox setup, do not submit a
   model task, then quit it. This also initializes that user's model metadata
   cache used by catalog generation. If no cache was created, retain the setup
   error rather than copying the daily user's `.codex` folder.

4. Use a Python with NumPy/Pillow available to **this account**, not an interpreter
   that requires opening the daily user's private cache permissions. If none is
   installed, the official Windows guide lists Python 3.14 as an installation
   option. In the new account, after approving the normal installation:

   ```powershell
   winget install --id Python.Python.3.14
   # Open a new terminal if installation updated PATH.
   cd D:\VolumeVlmExperiment\gsc-agentic-volume
   py -m venv .venv
   .\.venv\Scripts\python.exe -m pip install numpy Pillow
   Invoke-RestMethod http://127.0.0.1:11434/api/version
   .\Start-IsolatedVolumeDesktop.ps1 -Python "$PWD\.venv\Scripts\python.exe"
   ```

   Repeat the checks above under this user. The old account's config/GUI files
   belong to another OS profile even if this Desktop falls back to its own default
   location. Do not open the daily user's data directories to make checks pass.

5. If custom-profile startup itself is incompatible, run Desktop in this dedicated
   user's default profile instead. This is the only manual user-level installation
   alternative; **never run this block in the daily account**. Close the failed
   experiment GUI/support first. Then, as VolumeVlm:

   ```powershell
   if ($env:USERNAME -ne 'VolumeVlm') { throw 'Wrong Windows account' }
   cd D:\VolumeVlmExperiment\gsc-agentic-volume
   .\Start-VolumeDesktop.ps1 -ServicesOnly -Python "$PWD\.venv\Scripts\python.exe"
   $experiment=Get-Content .\runtime\desktop\active.json -Raw | ConvertFrom-Json
   $accountConfig=Join-Path $env:USERPROFILE '.codex\config.toml'
   if (Test-Path $accountConfig) { Copy-Item $accountConfig ($accountConfig+'.before-volume-experiment-'+(Get-Date -Format 'yyyyMMdd-HHmmss')) }
   Copy-Item (Join-Path $experiment.desktopHome 'config.toml') $accountConfig
   ```

   Open Desktop from this account's Start menu, choose Qwen, then use the same
   fixed prompts. The configuration explicitly uses `use_responses_lite=false`.
   Verify this account with `whoami` and `%USERPROFILE%`; model/MCP/image evidence
   is still recorded by the same service. Use the direct commands below (the
   private-launch verifier is intentionally not a pass for default-profile mode):

   ```powershell
   .\.venv\Scripts\python.exe -B scripts\desktop_isolation.py protocol
   .\Start-VolumeDesktop.ps1 -Collect -Python "$PWD\.venv\Scripts\python.exe"
   ```

   Record default-profile mode and the separate account as changed deployment
   conditions. On exit collect, quit this Desktop and stop support; only this
   dedicated account's backup may be restored manually. No daily-user config is
   read or overwritten by these copy commands.

Official references: [Windows installation](https://learn.chatgpt.com/docs/windows/windows-app),
[public home/state variables](https://learn.chatgpt.com/docs/config-file/environment-variables).
`CODEX_ELECTRON_USER_DATA_PATH` was identified in the installed implementation;
it is not presented here as a stable public isolation guarantee.
