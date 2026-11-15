@echo off
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0register_context_menu.ps1"
if errorlevel 1 exit /b 1
echo.
echo After you move this folder, run this bat again.
pause
exit /b 0
