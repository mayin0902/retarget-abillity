param([string]$PythonVersion = "3.12")

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Config = Join-Path $Root "PIP_MIRROR.ini"
$Venv = Join-Path $Root ".review-venv"

if (-not (Test-Path -LiteralPath $Config -PathType Leaf)) {
    throw "Missing PIP_MIRROR.ini"
}
$Values = @{}
foreach ($Line in Get-Content -LiteralPath $Config) {
    if ($Line -match '^\s*#' -or -not $Line.Contains('=')) { continue }
    $Key, $Value = $Line.Split('=', 2)
    $Values[$Key.Trim()] = $Value.Trim()
}
if ([string]::IsNullOrWhiteSpace($Values['INDEX_URL'])) {
    throw "INDEX_URL is blank. Ask the project owner to fill PIP_MIRROR.ini before installing."
}
if (Test-Path -LiteralPath $Venv) {
    throw ".review-venv already exists. Reuse it, or move this immutable release to a new folder."
}

$Py = Get-Command py.exe -ErrorAction SilentlyContinue
if ($null -eq $Py) { throw "Python Launcher py.exe was not found. Install Python 3.11-3.13 first." }
& $Py.Source "-$PythonVersion" -c "import sys; assert (3,11) <= sys.version_info[:2] < (3,14)"
if ($LASTEXITCODE -ne 0) { throw "Python $PythonVersion is unavailable or unsupported." }
& $Py.Source "-$PythonVersion" -m venv $Venv
if ($LASTEXITCODE -ne 0) { throw "Failed to create .review-venv." }

$Python = Join-Path $Venv "Scripts\python.exe"
$PipArgs = @('--index-url', $Values['INDEX_URL'])
if (-not [string]::IsNullOrWhiteSpace($Values['TRUSTED_HOST'])) {
    $PipArgs += @('--trusted-host', $Values['TRUSTED_HOST'])
}
& $Python -m pip install @PipArgs --upgrade pip
if ($LASTEXITCODE -ne 0) { throw "Failed to upgrade pip from the configured company mirror." }
& $Python -m pip install @PipArgs -r (Join-Path $Root "_runtime\requirements-review-ui.txt")
if ($LASTEXITCODE -ne 0) { throw "Failed to install review UI dependencies." }

$env:PYTHONPATH = Join-Path $Root "_runtime\src"
& $Python -c "from retarget_agent.movie60_review_app import create_movie60_review_app; print('review-ui-import-ok')"
if ($LASTEXITCODE -ne 0) { throw "Review UI import check failed." }
Write-Host "Installed review UI into $Venv"
