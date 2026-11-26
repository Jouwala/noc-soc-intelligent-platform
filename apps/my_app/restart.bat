@echo off
setlocal
cd /d "%~dp0"
title my_app - restart
echo ============================================
echo   Restarting Data Insight MVP (stop then start)
echo   PostgreSQL is kept running.
echo ============================================
choice /c YN /n /m "Restart now? [Y/N] "
if errorlevel 2 goto :eof
echo.
echo [*] Stopping my_app ...
call "%~dp0stop.bat"
echo.
echo [*] Starting my_app again ...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_hidden.ps1" -Force
call "%~dp0..\..\ops\deploy\finish.bat" "7038,7090" "my_app restart"
endlocal
