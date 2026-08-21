@echo off
setlocal
cd /d "%~dp0"
set "CLI=%~dp0.venv\Scripts\retarget-engine.exe"

if not exist "%CLI%" (
  echo [ERROR] The local environment is missing.
  echo Run this once in PowerShell:
  echo   powershell -ExecutionPolicy Bypass -File scripts\bootstrap_windows.ps1
  pause
  exit /b 2
)

if "%~1"=="" (
  "%CLI%" review open
) else (
  "%CLI%" review open "%~f1"
)

if errorlevel 1 (
  echo.
  echo [ERROR] Review UI did not start. Run .venv\Scripts\retarget-engine.exe doctor.
  pause
)
