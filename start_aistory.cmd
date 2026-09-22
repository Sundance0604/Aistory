@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start_aistory.ps1" %*
set "AISTORY_EXIT=%ERRORLEVEL%"
if not "%AISTORY_EXIT%"=="0" pause
exit /b %AISTORY_EXIT%
