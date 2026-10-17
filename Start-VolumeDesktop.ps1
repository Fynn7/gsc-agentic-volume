[CmdletBinding()]
param(
  [ValidateSet('Local','H200')][string]$Mode='Local',
  [string]$VolumePath,
  [string]$Config,
  [string]$Python,
  [switch]$PrepareOnly,
  [switch]$ServicesOnly,
  [switch]$Restore,
  [switch]$Collect,
  [switch]$Stop,
  [switch]$NoOpen
)
$ErrorActionPreference='Stop'
$repoDir=$PSScriptRoot
if (-not $Python) {
  $bundledPython=Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
  if (Test-Path -LiteralPath $bundledPython) { $Python=$bundledPython } else { $Python=(Get-Command python -ErrorAction Stop).Source }
}
$support=Join-Path $repoDir 'scripts\desktop_environment.py'
if ($Collect) {
  & $Python -B (Join-Path $repoDir 'scripts\collect_desktop_evidence.py')
  if ($LASTEXITCODE) { throw 'Evidence collection failed' }
  return
}
if ($Restore) { & $Python -B $support restore; if ($LASTEXITCODE) { throw 'Restore failed' }; return }
$activeFile=Join-Path $repoDir 'runtime\desktop\active.json'
if ($Stop) {
  if (Test-Path -LiteralPath $activeFile) {
    $activeConfig=Get-Content -LiteralPath $activeFile -Raw | ConvertFrom-Json
    $status=Invoke-RestMethod ($activeConfig.recordingProxy+'/desktop/status')
    if ($status.resultDirectory -ne $activeConfig.resultDirectory) { throw 'Another run owns the recording port' }
    Invoke-RestMethod ($activeConfig.recordingProxy+'/desktop/stop') -Method Post | Out-Null
  }
  Write-Host 'Support services stopped. Close the experiment chat before changing configurations.'; return
}
& $Python -B -c 'import numpy, PIL, tomllib'
if ($LASTEXITCODE) { throw 'Python requires NumPy and Pillow. Select the existing development interpreter with -Python.' }
# Never overwrite a live run or lose its immutable config.
if (Test-Path -LiteralPath $activeFile) {
  $previous=Get-Content -LiteralPath $activeFile -Raw | ConvertFrom-Json
  try { $live=Invoke-RestMethod ($previous.recordingProxy+'/desktop/status') -TimeoutSec 2 } catch { $live=$null }
  if ($live) { throw 'A recording run is already active. Keep using it, or run .\Start-VolumeDesktop.ps1 -Stop before starting a fresh run.' }
}
if (Test-Path -LiteralPath (Join-Path $repoDir 'runtime\desktop\installed.json')) {
  throw 'An experiment config is installed. Close Desktop and run .\Start-VolumeDesktop.ps1 -Restore before preparing a different run.'
}
if (-not $Config) { $Config=Join-Path $repoDir ('experiments\desktop.'+$Mode.ToLower()+'.json') }
$prepareArgs=@('-B',$support,'prepare','--template',$Config)
if ($VolumePath) { $prepareArgs+=@('--volume',$VolumePath) }
$runDir=& $Python @prepareArgs
if ($LASTEXITCODE) { throw 'Preparation failed' }
$activeConfig=Get-Content -LiteralPath $activeFile -Raw | ConvertFrom-Json
Write-Host ('Run directory: '+$runDir)
if ($PrepareOnly) { Write-Host 'Prepared only. User config unchanged; no services, GUI or model invocation started.'; return }
try { Invoke-RestMethod ($activeConfig.endpoint+'/api/version') -TimeoutSec 2 | Out-Null } catch {
  if ($Mode -eq 'H200') { throw 'Remote Ollama unavailable. Open the documented SSH tunnel first.' }
  $ollamaExe=Join-Path $env:LOCALAPPDATA 'Programs\Ollama\ollama.exe'
  if (-not (Test-Path -LiteralPath $ollamaExe)) { throw 'Ollama is not installed at the audited path. Start your installed Ollama service first.' }
  $env:OLLAMA_CONTEXT_LENGTH=[string]$activeConfig.contextSize
  $env:OLLAMA_NO_CLOUD='1'
  $env:OLLAMA_NUM_PARALLEL='1'
  Start-Process -FilePath $ollamaExe -ArgumentList 'serve' -WindowStyle Hidden -RedirectStandardOutput (Join-Path $runDir 'ollama-stdout.log') -RedirectStandardError (Join-Path $runDir 'ollama-stderr.log') | Out-Null
  $ollamaReady=$false
  for ($attempt=0;$attempt -lt 40;$attempt++) {
    try { Invoke-RestMethod ($activeConfig.endpoint+'/api/version') -TimeoutSec 1 | Out-Null; $ollamaReady=$true; break } catch { Start-Sleep -Milliseconds 500 }
  }
  if (-not $ollamaReady) { throw 'Ollama failed to start; inspect run logs' }
}
# This launches only the passive recorder/service process, never Codex CLI.
$supportArgs='-B "'+$support+'" serve --config "'+(Join-Path $runDir 'config.json')+'"'
Start-Process -FilePath $Python -ArgumentList $supportArgs -WorkingDirectory $repoDir -WindowStyle Hidden -RedirectStandardOutput (Join-Path $runDir 'support-stdout.log') -RedirectStandardError (Join-Path $runDir 'support-stderr.log') | Out-Null
$ready=$false
for ($attempt=0;$attempt -lt 80;$attempt++) {
  try { $status=Invoke-RestMethod ($activeConfig.recordingProxy+'/desktop/status') -TimeoutSec 1; $ready=$true; break } catch { Start-Sleep -Milliseconds 500 }
}
if (-not $ready) { throw ('Support service failed. Read '+(Join-Path $runDir 'support-stderr.log')) }
if ($status.resultDirectory -ne $activeConfig.resultDirectory) { throw 'Recording port belongs to a different run; user config was not installed' }
if ($ServicesOnly) { Write-Host 'Passive services ready. User config unchanged; no Codex or model task started.'; return }
# USER RUNS THIS AFTER ENDING THE WORKER AND FULLY QUITTING DESKTOP.
& $Python -B $support install
if ($LASTEXITCODE) { throw 'User configuration installation failed; backup/candidate remain in run directory' }
Write-Host ('Configured model: '+$activeConfig.model+'; route: '+$activeConfig.recordingProxy+' -> '+$activeConfig.endpoint)
Write-Host 'Open a NEW Codex Desktop chat in runtime\desktop\workspace. Choose Local VLM: qwen3.6:35b.'
Write-Host 'First prompt: experiments\desktop-prompts\00-check.txt; then 01-parameters.txt and 02-feedback.txt.'
Write-Host 'Desktop model selection, MCP image display and task execution require your manual acceptance.'
if (-not $NoOpen) { Start-Process explorer.exe -ArgumentList 'shell:AppsFolder\OpenAI.Codex_2p2nqsd0c76g0!App' }
