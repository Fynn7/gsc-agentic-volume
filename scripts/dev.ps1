param([string]$Python = $env:GAV_PYTHON, [int]$Port = 8765, [switch]$Rebuild)
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
if (-not $Python) {
    $bundled = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
    $Python = if (Test-Path -LiteralPath $bundled) { $bundled } else { 'python' }
}
& $Python -B -c 'import numpy,PIL'
if ($LASTEXITCODE -ne 0) { throw 'Select a Python with NumPy and Pillow using -Python or GAV_PYTHON.' }
$dist = Join-Path $root 'frontend\dist\index.html'
if ($Rebuild -or -not (Test-Path -LiteralPath $dist)) {
    if (-not (Get-Command npm -ErrorAction SilentlyContinue)) { throw 'Node.js/npm is required to build the frontend bundle.' }
    Push-Location (Join-Path $root 'frontend')
    try {
        & npm run build
        if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed.' }
    } finally { Pop-Location }
} else {
    Write-Host 'Serving existing frontend/dist (pass -Rebuild to refresh the GUI bundle).'
}
Push-Location $root
try { & $Python -B src/app.py --port $Port } finally { Pop-Location }
