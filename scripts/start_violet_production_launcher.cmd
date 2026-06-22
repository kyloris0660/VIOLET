@echo off
setlocal

set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%..") do set "REPO_ROOT=%%~fI"

if exist "%REPO_ROOT%\venv\Scripts\python.exe" (
  set "PY=%REPO_ROOT%\venv\Scripts\python.exe"
) else if exist "%REPO_ROOT%\.venv\Scripts\python.exe" (
  set "PY=%REPO_ROOT%\.venv\Scripts\python.exe"
) else (
  set "PY=python"
)

"%PY%" "%REPO_ROOT%\scripts\violet_production_launcher.py"
if errorlevel 1 (
  echo.
  echo V.I.O.L.E.T. production launcher exited with an error.
  pause
)

endlocal
