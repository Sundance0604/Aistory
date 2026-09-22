@echo off
setlocal
title Aistory Server
echo [Aistory] Preparing the local service...
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start_aistory.ps1" %*
set "AISTORY_EXIT=%ERRORLEVEL%"
if not "%AISTORY_EXIT%"=="0" pause
exit /b %AISTORY_EXIT%
