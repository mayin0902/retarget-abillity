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

Invoke-Checked $Python @(
    'scripts\prepare_single_image_dataset.py',
    $InputImage,
    '--output-dir', $Dataset,
    '--source-id', $CaseId,
    '--run-id', $RunId,
    '--scene-category', 'movie_poster',
    '--split', 'calibration'
) 'Single-image dataset preparation'
Invoke-Checked $Cli @('dataset', 'validate', $Dataset) 'Dataset validation'
Invoke-Checked $Cli @('run', 'generate', "$Dataset\run.yaml") 'Candidate generation'
Invoke-Checked $Cli @(
    'evaluate',
    $RunDir,
    '--evaluation-id', $EvaluationId,
    '--strategy', $Strategy
) 'Rule evaluation'

Write-Host ''
Write-Host 'Single-image Rule pipeline completed.' -ForegroundColor Green
Write-Host "Run: $RunDir"
Write-Host "Evaluation: $RunDir\evaluations\$EvaluationId"
Write-Host "Strategy snapshot: $RunDir\evaluations\$EvaluationId\strategy"
Write-Host "Candidates: $RunDir\candidates"
