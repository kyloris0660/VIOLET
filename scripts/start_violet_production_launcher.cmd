@echo off
setlocal

set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%..") do set "REPO_ROOT=%%~fI"

where npm >nul 2>nul
if errorlevel 1 (
  echo npm was not found. Install Node.js, then run npm install in the launcher directory.
  pause
  exit /b 1
)

if not exist "%REPO_ROOT%\launcher\node_modules\.bin\electron.cmd" (
  echo Electron dependencies are not installed.
  echo.
  echo Run:
  echo   cd /d "%REPO_ROOT%\launcher"
  echo   npm install
  pause
  exit /b 1
)

pushd "%REPO_ROOT%\launcher"
npm start
set "LAUNCHER_EXIT=%ERRORLEVEL%"
popd

if not "%LAUNCHER_EXIT%"=="0" (
  echo.
  echo V.I.O.L.E.T. production launcher exited with an error.
  pause
)

endlocal
exit /b %LAUNCHER_EXIT%
