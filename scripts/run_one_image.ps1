[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$InputImage,
    [string]$CaseId = 'handoff-demo',
    [string]$Strategy = 'strategies\movie60\v3_2_2\bundle.yaml'
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $RepoRoot
$Python = Join-Path $RepoRoot '.venv\Scripts\python.exe'
$Cli = Join-Path $RepoRoot '.venv\Scripts\retarget-engine.exe'

if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw 'Missing .venv. Run scripts\bootstrap_windows.ps1 first.'
}
if (-not (Test-Path -LiteralPath $InputImage -PathType Leaf)) {
    throw "Input image does not exist: $InputImage"
}
if ($CaseId -notmatch '^[a-z0-9][a-z0-9_-]{0,79}$') {
    throw 'CaseId must use lowercase letters, digits, underscore or hyphen.'
}

$Dataset = "local_data\datasets\$CaseId"
$RunId = "$CaseId-square-v1"
$RunDir = "runs\$RunId"
$EvaluationId = "$CaseId-rule-v2"

foreach ($Path in @($Dataset, $RunDir)) {
    if (Test-Path -LiteralPath $Path) {
        throw "Refusing to overwrite existing artifact: $Path"
    }
}

& $Python scripts\prepare_single_image_dataset.py `
    $InputImage `
    --output-dir $Dataset `
    --source-id $CaseId `
    --run-id $RunId `
    --scene-category movie_poster `
    --split calibration
& $Cli dataset validate $Dataset
& $Cli run generate "$Dataset\run.yaml"
& $Cli evaluate $RunDir --evaluation-id $EvaluationId --strategy $Strategy

Write-Host ''
Write-Host 'Single-image Rule pipeline completed.' -ForegroundColor Green
Write-Host "Run: $RunDir"
Write-Host "Evaluation: $RunDir\evaluations\$EvaluationId"
Write-Host "Strategy snapshot: $RunDir\evaluations\$EvaluationId\strategy"
Write-Host "Candidates: $RunDir\candidates"
