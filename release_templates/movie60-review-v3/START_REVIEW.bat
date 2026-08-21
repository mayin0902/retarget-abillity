@echo off
setlocal
set "REVIEW_ROOT=%~dp0"
set "REVIEW_ROOT=%REVIEW_ROOT:~0,-1%"
set "REVIEW_PYTHON=%REVIEW_ROOT%\.review-venv\Scripts\python.exe"
if not exist "%REVIEW_PYTHON%" (
  echo Missing .review-venv. Fill PIP_MIRROR.ini and run INSTALL_WINDOWS.bat first.
  pause
  exit /b 1
)
set "PYTHONPATH=%REVIEW_ROOT%\_runtime\src"
echo Starting Movie60 review UI at http://127.0.0.1:8766/
echo Keep this window open. Close it or run STOP_REVIEW.bat to stop the server.
"%REVIEW_PYTHON%" "%REVIEW_ROOT%\_runtime\run_review_ui.py" --workspace "%REVIEW_ROOT%" --host 127.0.0.1 --port 8766 --open-browser
if errorlevel 1 (
  echo.
  echo Review UI exited with an error. Read the message above.
  pause
  exit /b 1
)
endlocal
