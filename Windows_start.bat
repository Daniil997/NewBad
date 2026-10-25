@echo off
setlocal
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start-manual.ps1" %*
set "NEWBAD_EXIT=%ERRORLEVEL%"
if not "%NEWBAD_EXIT%"=="0" pause
exit /b %NEWBAD_EXIT%
