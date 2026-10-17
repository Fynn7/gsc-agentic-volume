[CmdletBinding()]
param(
  [ValidateSet('Local','H200')][string]$Mode='Local',
  [string]$VolumePath,
  [string]$Config,
  [string]$Python,
  [switch]$PrepareOnly,
  [switch]$ServicesOnly,
  [ValidateRange(1,3)][int]$StartAttempts=2,
  [switch]$Verify,
  [switch]$CheckSingleInstance,
  [switch]$Protocol,
  [switch]$Collect,
  [switch]$Stop
)
$ErrorActionPreference='Stop'
$repoDir=$PSScriptRoot
if (-not $Python) {
  $bundledPython=Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
  if (Test-Path -LiteralPath $bundledPython) { $Python=$bundledPython } else { $Python=(Get-Command python -ErrorAction Stop).Source }
}
$checker=Join-Path $repoDir 'scripts\desktop_isolation.py'
& $Python -B (Join-Path $repoDir 'scripts\check_desktop_python.py')
if ($LASTEXITCODE) { throw 'Use an existing 64-bit native Windows Python interpreter' }
$activeFile=Join-Path $repoDir 'runtime\desktop\active.json'
if ($Verify -or $Protocol -or $CheckSingleInstance -or $Collect -or $Stop) {
  if (-not (Test-Path -LiteralPath $activeFile)) { throw 'No prepared experiment exists' }
  $activeConfig=Get-Content -LiteralPath $activeFile -Raw | ConvertFrom-Json
  if (-not (Test-Path -LiteralPath (Join-Path $activeConfig.resultDirectory 'desktop-launch.json'))) { throw 'No isolated Desktop launch exists for the current run. Historical Desktop runs are not isolated launches.' }
  if ($Verify) { & $Python -B $checker verify --config $activeFile; if ($LASTEXITCODE) { throw 'Isolation is not verified; read isolation-running.json. Do not submit model tasks yet.' }; return }
  if ($Protocol) { & $Python -B $checker protocol --config $activeFile; if ($LASTEXITCODE) { throw 'Protocol acceptance is pending or failed; read protocol-acceptance.json.' }; return }
  if ($CheckSingleInstance) { & $Python -B $checker probe-second --config $activeFile; if ($LASTEXITCODE) { throw 'Single-instance check failed; inspect single-instance.json and close extra experiment windows manually.' }; return }
  if ($Collect) {
    & $Python -B (Join-Path $repoDir 'scripts\collect_desktop_evidence.py') --run $activeConfig.resultDirectory
    if ($LASTEXITCODE) { throw 'Evidence collection failed' }
    & $Python -B $checker protocol --config $activeFile
    if ($LASTEXITCODE) { Write-Warning 'Protocol acceptance pending/failed; evidence was preserved.' }; return
  }
  if ($Stop) {
    & $Python -B $checker exit-check --config $activeFile
    if ($LASTEXITCODE) { throw 'Fully quit the experiment Desktop first, or inspect the daily-file mismatch. No processes were killed or files restored.' }
    try { $status=Invoke-RestMethod ($activeConfig.recordingProxy+'/desktop/status') -TimeoutSec 2 } catch { $status=$null }
    if ($status) {
      if ($status.resultDirectory -ne $activeConfig.resultDirectory) { throw 'Different run owns support port' }
      Invoke-RestMethod ($activeConfig.recordingProxy+'/desktop/stop') -Method Post | Out-Null
    }
    Write-Host 'Experiment exited; protected daily files checked; support stopped. No Restore is required.'; return
  }
}
$supportArgs=@{Mode=$Mode;Python=$Python}
if ($Config) { $supportArgs.Config=$Config }
if ($VolumePath) { $supportArgs.VolumePath=$VolumePath }
if ($PrepareOnly) {
  & (Join-Path $repoDir 'Start-VolumeDesktop.ps1') @supportArgs -PrepareOnly
  if ($LASTEXITCODE) { throw 'Preparation failed' }
  Write-Host 'Isolated candidate prepared; GUI runtime verification remains a human step.'; return
}
$recovery=Join-Path $repoDir 'scripts\desktop_startup_recovery.py'
function Invoke-StartupRecovery {
  param([switch]$FailedAttempt,[string]$Reason='next-start recovery')
  $recoveryArgs=@('-B',$recovery,'--config',$activeFile,'--reason',$Reason)
  if ($FailedAttempt) { $recoveryArgs+='--failed-attempt' }
  $recoveryJson=& $Python @recoveryArgs
  $recoveryCode=$LASTEXITCODE
  $result=($recoveryJson | Out-String) | ConvertFrom-Json
  if ($recoveryCode -ne 0) {
    $detail=if ($result.error) { $result.error } else { 'Protected daily files changed. Inspect startup-recovery.json and isolation-alerts.jsonl.' }
    throw ('Recovery refused; no automatic restart: '+$detail)
  }
  return $result
}
$previous=Invoke-StartupRecovery
if ($previous.action -eq 'reuse') { Write-Host $previous.message; return }
if ($previous.action -eq 'already-closed') { Write-Host $previous.message }
for ($attempt=1;$attempt -le $StartAttempts;$attempt++) {
  try {
    & (Join-Path $repoDir 'Start-VolumeDesktop.ps1') @supportArgs -ServicesOnly
    if ($LASTEXITCODE) { throw 'Support service startup failed' }
    if (-not $ServicesOnly) {
      # HUMAN EXECUTES THIS AFTER THE WORKER ENDS. Worker tests never launch Codex.
      & $Python -B $checker launch --config $activeFile
      if ($LASTEXITCODE) { throw 'Isolated Desktop launch failed; inspect desktop-launch-failure.json' }
    }
    break
  } catch {
    $startupFailure=$_.Exception.Message
    Write-Warning ('Startup attempt '+$attempt+' failed: '+$startupFailure)
    $cleanup=Invoke-StartupRecovery -FailedAttempt -Reason $startupFailure
    if ($attempt -ge $StartAttempts) { throw ('Startup failed after cleanup. Logs preserved at '+$cleanup.run+'. Last error: '+$startupFailure) }
    Write-Host 'Owned failed-startup processes stopped. Retrying with a fresh run; previous logs preserved.'
  }
}
if ($ServicesOnly) { Write-Host 'Support-only startup ready; no Desktop/model was launched.'; return }
Write-Host 'Open the Codex view in the NEW experiment window, complete any manual first-use steps, then run:'
Write-Host '.\Start-IsolatedVolumeDesktop.ps1 -Verify'
Write-Host 'Do not send a model prompt until verification passes. No daily configuration was installed.'
