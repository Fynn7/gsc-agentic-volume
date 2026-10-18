<#
.SYNOPSIS
  Single-command entry for the Volume Agent TUI (control/display over Codex CLI).
  Never calls a model, creates an experiment, or downloads a model on startup.
#>
param(
  [string]$Python = $env:GAV_PYTHON,
  [switch]$StartOllama
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

foreach ($file in @('experiments\local-vlm.example.json', 'scripts\run_local_agent.py', 'scripts\agent_console.py', 'scripts\verify_local_agent.py', 'scripts\volume_tui\ui.py')) {
  if (-not (Test-Path -LiteralPath (Join-Path $repoRoot $file))) { throw "Missing required file: $file" }
}
if (-not (Get-Command codex -ErrorAction SilentlyContinue)) { Write-Warning 'codex not on PATH; Runs will fail preflight until Codex CLI is installed.' }
if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) { Write-Warning 'ollama not on PATH; Local inference checks will report DOWN.' }

# Isolated TUI state (settings live under runtime/; no venv needed: stdlib-only MVP).
$tuiDir = Join-Path $repoRoot 'runtime\tui'
New-Item -ItemType Directory -Path $tuiDir -Force | Out-Null

if ($StartOllama) {
  Write-Host 'Note: -StartOllama is accepted for parity but the TUI itself never auto-starts inference silently; use Settings + Run preflight messages.'
}

# Read default settings for a one-line status (no model request).
$tuiPkg = Join-Path $repoRoot 'scripts\volume_tui'
$settingsText = & $Python -B -c ("import json,sys; sys.path.insert(0, r'" + $tuiPkg + "'); import config; print(json.dumps(config.load_settings()))")
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

Write-Host 'Entering Volume Agent TUI (Esc quits; quitting never stops a running agent)...'
& $Python -B (Join-Path $repoRoot 'scripts\volume_tui')
exit $LASTEXITCODE
