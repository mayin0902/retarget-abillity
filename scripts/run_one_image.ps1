[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$InputImage,
    [string]$Target = '1536x1536'
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $RepoRoot
$Cli = Join-Path $RepoRoot '.venv\Scripts\retarget-engine.exe'

function Invoke-Checked {
    param(
        [Parameter(Mandatory = $true)][string]$Executable,
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [Parameter(Mandatory = $true)][string]$Label
    )
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$Label failed with exit code $LASTEXITCODE. See the command output above."
    }
}

if (-not (Test-Path -LiteralPath $Cli -PathType Leaf)) {
    throw 'Missing .venv. Run scripts\bootstrap_windows.ps1 first.'
}
if (-not (Test-Path -LiteralPath $InputImage -PathType Leaf)) {
    throw "Input image does not exist: $InputImage"
}
Invoke-Checked $Cli @('run', 'image', $InputImage, '--target', $Target) 'Image workflow'

Write-Host ''
Write-Host 'Single-image Rule pipeline completed.' -ForegroundColor Green
Write-Host 'Use START_REVIEW.bat to inspect the newest completed Run.'
