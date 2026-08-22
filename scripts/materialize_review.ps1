[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $RepoRoot
$Python = Join-Path $RepoRoot '.venv\Scripts\python.exe'
$Output = Join-Path $RepoRoot 'local_data\movie60-review-current'
$ReleaseConfig = Join-Path $RepoRoot 'CURRENT_RELEASE.json'

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

$Release = Get-Content -LiteralPath $ReleaseConfig -Raw -Encoding UTF8 | ConvertFrom-Json
$AssetDir = Join-Path $RepoRoot (Join-Path 'local_data\release_assets' $Release.github_release_tag)
$MissingAssets = @(
    $Release.release_asset_names |
        Where-Object { -not (Test-Path -LiteralPath (Join-Path $AssetDir $_) -PathType Leaf) }
)

$Arguments = @('scripts\materialize_movie60_release.py')
if ($MissingAssets.Count -eq 0) {
    Write-Host "Using local Movie60 Release assets: $AssetDir" -ForegroundColor Cyan
    $Arguments += @('--asset-dir', $AssetDir)
} else {
    if (Test-Path -LiteralPath $AssetDir -PathType Container) {
        Write-Warning "Local Release assets are incomplete. Missing: $($MissingAssets -join ', ')"
    }
    if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
        throw (
            "GitHub CLI is unavailable and local Release assets are incomplete. " +
            "Download all assets without renaming or extracting them into: $AssetDir. " +
            "Required: $($Release.release_asset_names -join ', ')"
        )
    }
}

& $Python @Arguments
if ($LASTEXITCODE -ne 0) {
    throw (
        'Movie60 materialization failed. Check local asset names/SHA256SUMS, ' +
        'or check gh auth status and Release availability.'
    )
}
Write-Host "Movie60 workspace ready: $Output" -ForegroundColor Green
