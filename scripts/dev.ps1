param([string]$Python = $env:GAV_PYTHON, [int]$Port = 8765)
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
if (-not $Python) {
    $bundled = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
    $Python = if (Test-Path -LiteralPath $bundled) { $bundled } else { 'python' }
}
& $Python -B -c 'import numpy,PIL'
if ($LASTEXITCODE -ne 0) { throw 'Select a Python with NumPy and Pillow using -Python or GAV_PYTHON.' }
Push-Location $root
try { & $Python -B src/app.py --port $Port } finally { Pop-Location }
