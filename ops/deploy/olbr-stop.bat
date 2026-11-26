@echo off
chcp 65001 >nul 2>&1
title OLBR - stop
echo This will STOP the production stack: my_app (7038/7090) + portal (9267/9268) + nginx (80/443)
echo   + WhatsApp bot (index.js + web Q^&A 9269). P1 alert push (notify.js) is NOT touched.
echo PostgreSQL (5432) is left running.
choice /c YN /n /m "Stop now? [Y/N] "
if errorlevel 2 goto :eof
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0portal-ctl.ps1" -Action stop
call "%~dp0finish.bat" "" "OLBR stop"
