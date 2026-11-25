@echo off
setlocal
cd /d "%~dp0"
title liberia-noc_soc Portal - restart
echo ============================================
echo   Restarting portal + WhatsApp analysis (9267/9268)
echo ============================================
choice /c YN /n /m "Restart now? [Y/N] "
if errorlevel 2 goto :eof
echo.
echo [*] Stopping portal + WhatsApp analysis ...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0stop_services.ps1" -Quiet
echo.
echo [*] Starting portal again ...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_hidden.ps1"
echo.
pause
endlocal
