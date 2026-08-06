@echo off
setlocal
set "SCRIPT=%~dp0stop-quantdinger.ps1"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT%" %*
endlocal
