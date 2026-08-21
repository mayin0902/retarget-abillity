[CmdletBinding()]
param(
    [ValidateSet('3.11', '3.12', '3.13')]
    [string]$PythonVersion = '3.12',
    [string]$PythonExecutable,
    [switch]$WithMovie60Release
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $RepoRoot

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

if (Test-Path -LiteralPath '.venv') {
    throw '.venv already exists. Refusing to overwrite an environment.'
}

$Required = @(
    'pyproject.toml',
    'requirements\constraints-py311-313.txt',
    'strategies\movie60\v1\bundle.yaml',
    'strategies\movie60\v2\bundle.yaml',
    'strategies\movie60\v3_2_2\bundle.yaml',
    'strategies\movie60\v3_3\bundle.yaml',
    'datasets\analyzer_models_v1\model_manifest.csv'
)
$Missing = $Required | Where-Object { -not (Test-Path -LiteralPath $_ -PathType Leaf) }
if ($Missing) {
    throw "Repository is incomplete: $($Missing -join ', ')"
}

if ($PythonExecutable) {
    if (-not (Test-Path -LiteralPath $PythonExecutable -PathType Leaf)) {
        throw "Python executable does not exist: $PythonExecutable"
    }
    Invoke-Checked $PythonExecutable @('-m', 'venv', '.venv') 'Virtual environment creation'
} else {
    if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
        Write-Host 'Python launcher was not found.' -ForegroundColor Red
        Write-Host "Install Python $PythonVersion, reopen PowerShell, then rerun this script."
        Write-Host "Suggested command (run manually): winget install Python.Python.$PythonVersion"
        exit 2
    }
    & py "-$PythonVersion" -c 'import sys; print(sys.executable)' *> $null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Python $PythonVersion is not installed." -ForegroundColor Red
        Write-Host "Suggested command (run manually): winget install Python.Python.$PythonVersion"
        exit 2
    }
    Invoke-Checked 'py' @("-$PythonVersion", '-m', 'venv', '.venv') 'Virtual environment creation'
}
$Python = Join-Path $RepoRoot '.venv\Scripts\python.exe'
$Cli = Join-Path $RepoRoot '.venv\Scripts\retarget-engine.exe'

Invoke-Checked $Python @('-m', 'pip', 'install', '--upgrade', 'pip==25.2', 'setuptools==80.9.0', 'wheel==0.45.1') 'Build-tool installation'
Invoke-Checked $Python @('-m', 'pip', 'install', '-c', 'requirements\constraints-py311-313.txt', '-e', '.[dev]') 'Project installation'
Invoke-Checked $Python @('-m', 'pip', 'install', '-r', 'requirements\company-models-windows.txt') 'Company-model runtime installation'
Invoke-Checked $Python @('scripts\materialize_analyzer_models.py') 'Analyzer model materialization'
Invoke-Checked $Python @('scripts\materialize_company_models.py') 'Company model materialization'
Invoke-Checked $Cli @('strategy', 'show', 'strategies\movie60\v1\bundle.yaml') 'v1 strategy validation'
Invoke-Checked $Cli @('strategy', 'show', 'strategies\movie60\v2\bundle.yaml') 'v2 strategy validation'
Invoke-Checked $Cli @('strategy', 'show', 'strategies\movie60\v3_2_2\bundle.yaml') 'v3.2.2 strategy validation'
Invoke-Checked $Cli @('strategy', 'show', 'strategies\movie60\v3_3\bundle.yaml') 'v3.3 strategy validation'
Invoke-Checked $Python @('-m', 'pytest', '-q', 'tests\test_strategy.py', 'tests\test_single_image_workflow_tools.py') 'Bootstrap smoke tests'

if ($WithMovie60Release) {
    Invoke-Checked $Python @('scripts\materialize_movie60_release.py') 'Movie60 Release materialization'
}

Write-Host ''
Write-Host 'Bootstrap completed.' -ForegroundColor Green
Write-Host "Python: $Python"
Write-Host 'Next: docs\README.md'
