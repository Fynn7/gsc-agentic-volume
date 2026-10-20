<#
.SYNOPSIS
  Single-command entry for the Volume Agent TUI (Textual IDE-style).
  Never calls a model, creates an experiment, or downloads a model on startup.
  Bootstraps runtime/tui-venv (bundled Python + pinned Textual) after prompting.
#>
param(
  [string]$Python = $env:GAV_PYTHON,
  [switch]$StartOllama,
  [switch]$NoBootstrap,
  [string]$Wheelhouse = $env:GAV_TUI_WHEELHOUSE
)
$ErrorActionPreference = 'Stop'
$repoRoot = $PSScriptRoot

function Find-Python {
  param([string]$Hint)
  if ($Hint -and (Test-Path -LiteralPath $Hint)) { return $Hint }
  $bundled = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
  if (Test-Path -LiteralPath $bundled) { return $bundled }
  return (Get-Command python -ErrorAction Stop).Source
}

$Python = Find-Python $Python
Write-Host "Python: $Python"
& $Python -B -c 'import numpy,PIL'
if ($LASTEXITCODE -ne 0) { throw 'Select an existing Python with NumPy and Pillow via -Python or $env:GAV_PYTHON. Daily Codex deps are never modified.' }

foreach ($file in @('experiments\local-vlm.example.json', 'scripts\run_local_agent.py', 'scripts\agent_console.py', 'scripts\verify_local_agent.py', 'scripts\volume_tui\app.py')) {
  if (-not (Test-Path -LiteralPath (Join-Path $repoRoot $file))) { throw "Missing required file: $file" }
}
if (-not (Get-Command codex -ErrorAction SilentlyContinue)) { Write-Warning 'codex not on PATH; Runs will fail preflight until Codex CLI is installed.' }
if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) { Write-Warning 'ollama not on PATH; Local inference checks will report DOWN.' }

# Isolated TUI state + venv (settings live under runtime/; Textual lives in runtime/tui-venv).
$tuiDir = Join-Path $repoRoot 'runtime\tui'
New-Item -ItemType Directory -Path $tuiDir -Force | Out-Null
$venvPython = Join-Path $repoRoot 'runtime\tui-venv\Scripts\python.exe'
$reqFile = Join-Path $repoRoot 'scripts\volume_tui\requirements.txt'
$hashFile = Join-Path $repoRoot 'runtime\tui\venv-requirements.sha256'

function Get-FileHashText($Path) {
  return (Get-FileHash -Algorithm SHA256 -LiteralPath $Path).Hash
}

$needInstall = $false
if (-not (Test-Path -LiteralPath $venvPython)) {
  $needInstall = $true
} else {
  try {
    & $venvPython -B -c 'import textual' 2>$null
    if ($LASTEXITCODE -ne 0) { $needInstall = $true }
    elseif ((Test-Path $reqFile) -and (Test-Path $hashFile)) {
      if ((Get-FileHashText $reqFile) -ne (Get-Content $hashFile -Raw).Trim()) { $needInstall = $true }
    } elseif (Test-Path $reqFile) { $needInstall = $true }
  } catch { $needInstall = $true }
}

if ($needInstall -and -not $NoBootstrap) {
  Write-Host ''
  Write-Host 'The TUI needs an isolated venv at runtime/tui-venv (Textual, pinned).'
  Write-Host ("Requirements: {0}" -f (Get-Content $reqFile -Raw).Trim())
  if ($Wheelhouse) { Write-Host ("Offline wheelhouse: {0}" -f $Wheelhouse) }
  $answer = Read-Host 'Create/update runtime/tui-venv now? [Y/n]'
  if ($answer -and $answer.Trim().ToLowerInvariant() -notin @('y', 'yes', '')) {
    throw 'Bootstrap declined. Re-run with -NoBootstrap to fail fast, or approve the venv install.'
  }
  if (-not (Test-Path -LiteralPath $venvPython)) {
    Write-Host 'Creating venv from the Codex bundled Python (--system-site-packages inherits NumPy/Pillow)...'
    & $Python -m venv --system-site-packages (Join-Path $repoRoot 'runtime\tui-venv')
    if ($LASTEXITCODE -ne 0) { throw 'venv creation failed. Check disk space and re-run.' }
  }
  Write-Host 'Installing Textual (pinned)...'
  if ($Wheelhouse) {
    & $venvPython -m pip install --no-index --find-links $Wheelhouse -r $reqFile
  } else {
    & $venvPython -m pip install -r $reqFile
  }
  if ($LASTEXITCODE -ne 0) {
    throw 'Textual install failed. Fix networking, or set $env:GAV_TUI_WHEELHOUSE to an offline wheel dir and re-run.'
  }
  (Get-FileHashText $reqFile) | Set-Content $hashFile -NoNewline
  Write-Host 'TUI venv ready.'
} elseif ($needInstall -and $NoBootstrap) {
  throw 'runtime/tui-venv is missing or stale. Re-run without -NoBootstrap to bootstrap it.'
}

$TuiPython = if (Test-Path -LiteralPath $venvPython) { $venvPython } else { $Python }

if ($StartOllama) {
  Write-Host 'Note: -StartOllama is legacy; the TUI starts missing local services itself on entry and stops its own on exit.'
}

# Read default settings for a one-line status (no model request).
$tuiPkg = Join-Path $repoRoot 'scripts\volume_tui'
$settingsText = & $TuiPython -B -c ("import json,sys; sys.path.insert(0, r'" + $tuiPkg + "'); import config; print(json.dumps(config.load_settings()))")
$settings = $settingsText | ConvertFrom-Json
Write-Host ("TUI settings: mode={0} model={1}" -f $settings.mode, $settings.model)
$statusEndpoint = if ($settings.mode -eq 'H200') { $settings.h200Endpoint } else { $settings.endpoint }
try {
  $null = Invoke-RestMethod ($statusEndpoint.TrimEnd('/') + '/api/version') -TimeoutSec 3
  Write-Host 'Inference (local default): reachable.'
} catch { Write-Warning ("Inference unreachable: {0}. See TUI preflight for the fix; nothing was started." -f $_.Exception.Message) }
try {
  $identity = Invoke-RestMethod ($settings.rendererService.TrimEnd('/') + '/api/service') -TimeoutSec 3
  if ($identity.service -eq 'gav-native-renderer') { Write-Host 'Renderer: this application (OK).' }
  else { Write-Warning 'Renderer URL does not identify this application; check port conflicts.' }
} catch { Write-Warning 'Renderer not reachable; it will start on the next Run and stay for reuse.' }

Write-Host 'Entering Volume Agent TUI (q quits and stops the agent run, the local Ollama and the owned services; Ctrl+C hard-stops everything, foreign processes included)...'
& $TuiPython -B (Join-Path $repoRoot 'scripts\volume_tui')
exit $LASTEXITCODE
