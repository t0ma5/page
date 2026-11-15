@echo off
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0unregister_context_menu.ps1"
if errorlevel 1 exit /b 1
echo.
pause
exit /b 0
