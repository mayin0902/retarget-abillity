[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $RepoRoot
$Python = Join-Path $RepoRoot '.venv\Scripts\python.exe'
$Output = Join-Path $RepoRoot 'local_data\movie60-review-current'

if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw 'Missing .venv. Run scripts\bootstrap_windows.ps1 first.'
}

if (Test-Path -LiteralPath $Output -PathType Container) {
    & $Python -c "from pathlib import Path; from retarget_agent.movie60_review_app import Movie60ReviewWorkspace; print(Movie60ReviewWorkspace(Path(r'$Output')).ready())"
    if ($LASTEXITCODE -ne 0) {
        throw 'Existing Movie60 workspace failed validation. Move it aside and rerun.'
    }
    Write-Host "Movie60 workspace already ready: $Output" -ForegroundColor Green
    exit 0
}

if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
    throw 'GitHub CLI (gh) is required to download the private Movie60 Release.'
}

& $Python scripts\materialize_movie60_release.py
if ($LASTEXITCODE -ne 0) {
    throw 'Movie60 materialization failed. Check gh auth status and Release availability.'
}
Write-Host "Movie60 workspace ready: $Output" -ForegroundColor Green
