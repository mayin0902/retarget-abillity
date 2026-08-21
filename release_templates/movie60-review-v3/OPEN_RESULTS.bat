@echo off
setlocal
if not exist "%~dp0all60\index.html" (
  echo Missing all60\index.html. Extract both v3 ZIP files into the same parent folder first.
  pause
  exit /b 1
)
start "" "%~dp0all60\index.html"
endlocal
