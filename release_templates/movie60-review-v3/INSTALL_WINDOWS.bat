@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0_runtime\install_windows.ps1"
if errorlevel 1 (
  echo.
  echo Installation failed. Read 01_CONFIGURE_PIP_MIRROR_FIRST.md and the message above.
  pause
  exit /b 1
)
echo.
echo Installation completed. You can now run START_REVIEW.bat.
pause
endlocal
