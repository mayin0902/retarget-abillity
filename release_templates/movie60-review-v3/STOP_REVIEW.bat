@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0_runtime\stop_review.ps1"
if errorlevel 1 (
  echo.
  echo Review UI was not stopped. Read the message above.
  pause
  exit /b 1
)
endlocal
