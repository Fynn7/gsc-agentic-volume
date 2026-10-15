# Build VirtualSampling/Raycaster with the same toolchain as upstream (v145 / VS 2026).
# Requires: Visual Studio Build Tools 2026 with "Desktop development with C++" / VCTools workload.
# See VirtualSampling/docs/installation.md and thesis README.

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path $PSScriptRoot -Parent
$sln = Join-Path $repoRoot "VirtualSampling\Raycaster\VS\Raycaster.sln"

$vswhere = Join-Path ${env:ProgramFiles(x86)} "Microsoft Visual Studio\Installer\vswhere.exe"
if (-not (Test-Path $vswhere)) {
    throw "vswhere not found. Install Visual Studio Build Tools 2026."
}

$installPath = & $vswhere -latest -version "[18.0,19.0)" -products * -requires Microsoft.Component.MSBuild -property installationPath
if (-not $installPath) {
    throw "No Visual Studio 2026 (v18) with MSBuild found. Install Microsoft.VisualStudio.BuildTools 18.x with VCTools workload."
}

$msbuild = Join-Path $installPath "MSBuild\Current\Bin\MSBuild.exe"
if (-not (Test-Path $msbuild)) {
    throw "MSBuild not found under: $installPath"
}

Write-Host "Using MSBuild: $msbuild"
& $msbuild $sln /p:Configuration=Release /p:Platform=x64 /m /v:minimal @args

$outDir = Join-Path $repoRoot "VirtualSampling\Raycaster\VS\x64\Release"
Write-Host ""
Write-Host "Output: $outDir\Raycaster.exe"
