@echo off
chcp 65001 >nul 2>&1
title OLBR - restart
echo This will RESTART the production stack (stop then start):
echo   my_app (7038/7090) + portal (9267/9268) + nginx (80/443) + WhatsApp bot (9269).
echo   P1 alert push (notify.js) is NOT touched. PostgreSQL (5432) is left running.
choice /c YN /n /m "Restart now? [Y/N] "
if errorlevel 2 goto :eof
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0portal-ctl.ps1" -Action restart
call "%~dp0finish.bat" "" "OLBR restart"
