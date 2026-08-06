@echo off
setlocal
rem Resolve the repository from this file so the command also works from System32.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-quantdinger.ps1" %*
if errorlevel 1 (
  echo.
  echo QuantDinger failed to start. Check the message above and backend_api_python\logs\api.log.
  exit /b %errorlevel%
)
endlocal
