@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0prepare_s3a_m2_manual_gui_acceptance.ps1" %*
exit /b %ERRORLEVEL%
