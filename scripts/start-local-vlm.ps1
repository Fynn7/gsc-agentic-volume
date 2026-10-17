param(
    [Parameter(Mandatory=$true)][string]$Config,
    [string]$Python=$env:GAV_PYTHON,
    [switch]$StartOllama,
    [switch]$ShowImages,
    [switch]$OpenImages
)
$ErrorActionPreference='Stop'
$repoRoot=Split-Path $PSScriptRoot -Parent
$configPath=(Resolve-Path -LiteralPath $Config).Path
$settings=Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json
if (-not $Python) {
    $bundled=Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
    $Python=if (Test-Path -LiteralPath $bundled) {$bundled} else {'python'}
}
& $Python -B -c 'import numpy,PIL'
if ($LASTEXITCODE -ne 0) {throw 'Select an existing Python with NumPy and Pillow using -Python or GAV_PYTHON.'}
$logRoot=Join-Path $repoRoot ('runtime\services-'+(Get-Date -Format 'yyyyMMdd-HHmmss'))
New-Item -ItemType Directory -Path $logRoot | Out-Null
$inferenceUri=[uri]$settings.endpoint
try {$null=Invoke-RestMethod ($settings.endpoint.TrimEnd('/')+'/api/version') -TimeoutSec 5}
catch {
    if (-not $StartOllama) {throw 'Ollama is unreachable. Start inference first, or use -StartOllama for the local endpoint.'}
    if ($inferenceUri.Host -notin @('127.0.0.1','localhost')) {throw 'Automatic inference startup is local only.'}
    $env:OLLAMA_HOST='127.0.0.1:'+$inferenceUri.Port
    $env:OLLAMA_CONTEXT_LENGTH=[string]$settings.contextSize
    $env:OLLAMA_NO_CLOUD='1'
    $env:OLLAMA_NUM_PARALLEL='1'
    $env:OLLAMA_MAX_LOADED_MODELS='1'
    $env:OLLAMA_FLASH_ATTENTION='1'
    $env:OLLAMA_KV_CACHE_TYPE='q8_0'
    $process=Start-Process -FilePath (Get-Command ollama.exe).Source -ArgumentList 'serve' -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logRoot 'ollama-stdout.log') -RedirectStandardError (Join-Path $logRoot 'ollama-stderr.log') -PassThru
    $process.Id | Set-Content (Join-Path $logRoot 'ollama.pid')
    $ready=$false
    for ($attempt=0;$attempt -lt 40;$attempt++) {
        try {$null=Invoke-RestMethod ($settings.endpoint.TrimEnd('/')+'/api/version') -TimeoutSec 1; $ready=$true; break} catch {Start-Sleep -Milliseconds 250}
    }
    if (-not $ready) {throw "Inference did not start; see $logRoot"}
}
$ps=Invoke-RestMethod ($settings.endpoint.TrimEnd('/')+'/api/ps') -TimeoutSec 5
$loaded=@($ps.models | Where-Object name -eq $settings.model)
if ($loaded.Count -gt 0 -and $loaded[0].context_length -ne $settings.contextSize) {
    throw 'Running model context differs from contextSize. Restart the inference service with the desired OLLAMA_CONTEXT_LENGTH; this launcher does not kill an existing service.'
}
$rendererUri=[uri]$settings.rendererService
if ($rendererUri.Host -notin @('127.0.0.1','localhost')) {throw 'Renderer must stay on this Windows machine.'}
try {$identity=Invoke-RestMethod ($settings.rendererService.TrimEnd('/')+'/api/service') -TimeoutSec 3}
catch {
    $appPath=Join-Path $repoRoot 'src\app.py'
    $process=Start-Process -FilePath $Python -ArgumentList @('-B',('"'+$appPath+'"'),'--port',[string]$rendererUri.Port) -WorkingDirectory $repoRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logRoot 'renderer-stdout.log') -RedirectStandardError (Join-Path $logRoot 'renderer-stderr.log') -PassThru
    $process.Id | Set-Content (Join-Path $logRoot 'renderer.pid')
    $identity=$null
    for ($attempt=0;$attempt -lt 40;$attempt++) {
        try {$identity=Invoke-RestMethod ($settings.rendererService.TrimEnd('/')+'/api/service') -TimeoutSec 1; break} catch {Start-Sleep -Milliseconds 250}
    }
}
if ($identity.service -ne 'gav-native-renderer') {throw 'The renderer URL does not identify this application. Check port conflicts.'}
Push-Location $repoRoot
$traceArgs=@()
if ($ShowImages) {$traceArgs+='--show-images'}
if ($OpenImages) {$traceArgs+='--open-images'}
try {& $Python -B scripts/run_local_agent.py --config $configPath @traceArgs; $taskExit=$LASTEXITCODE}
finally {Pop-Location}
exit $taskExit
