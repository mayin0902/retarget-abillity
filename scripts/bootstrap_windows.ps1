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

if (Test-Path -LiteralPath '.venv') {
    throw '.venv already exists. Refusing to overwrite an environment.'
}

$Required = @(
    'pyproject.toml',
    'requirements\constraints-py311-313.txt',
    'strategies\movie60\v1\bundle.yaml',
    'strategies\movie60\v2\bundle.yaml',
    'strategies\movie60\v3_2_2\bundle.yaml',
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
    & $PythonExecutable -m venv .venv
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
    & py "-$PythonVersion" -m venv .venv
}
$Python = Join-Path $RepoRoot '.venv\Scripts\python.exe'
$Cli = Join-Path $RepoRoot '.venv\Scripts\retarget-engine.exe'
$Pip = @($Python, '-m', 'pip')

& $Python -m pip install --upgrade 'pip==25.2' 'setuptools==80.9.0' 'wheel==0.45.1'
& $Python -m pip install -c requirements\constraints-py311-313.txt -e '.[dev]'
& $Python -m pip install -r requirements\company-models-windows.txt
& $Python scripts\materialize_analyzer_models.py
& $Python scripts\materialize_company_models.py
& $Cli strategy show strategies\movie60\v1\bundle.yaml
& $Cli strategy show strategies\movie60\v2\bundle.yaml
& $Cli strategy show strategies\movie60\v3_2_2\bundle.yaml
& $Python -m pytest -q tests\test_strategy.py tests\test_single_image_workflow_tools.py

if ($WithMovie60Release) {
    & $Python scripts\materialize_movie60_release.py
}

Write-Host ''
Write-Host 'Bootstrap completed.' -ForegroundColor Green
Write-Host "Python: $Python"
Write-Host 'Next: docs\README.md'
