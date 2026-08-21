$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$PidFile = Join-Path $Root ".state\review-ui.pid"
if (-not (Test-Path -LiteralPath $PidFile -PathType Leaf)) {
    Write-Host "Review UI is not running (no PID file)."
    exit 0
}
$ReviewPid = [int](Get-Content -LiteralPath $PidFile -Raw)
$Process = Get-CimInstance Win32_Process -Filter "ProcessId=$ReviewPid" -ErrorAction SilentlyContinue
if ($null -eq $Process) {
    Remove-Item -LiteralPath $PidFile -Force
    Write-Host "Removed a stale PID file."
    exit 0
}
$Expected = (Join-Path $Root "_runtime\run_review_ui.py")
if ($Process.CommandLine -notlike "*$Expected*") {
    throw "Refusing to stop PID $ReviewPid because it is not this release's review server."
}
Stop-Process -Id $ReviewPid
Remove-Item -LiteralPath $PidFile -Force
Write-Host "Movie60 review UI stopped."
