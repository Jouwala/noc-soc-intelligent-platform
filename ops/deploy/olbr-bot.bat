@echo off
REM ============================================================
REM  olbr-bot.bat - WhatsApp knowledge-base bot: start / stop / restart / status
REM  Thin wrapper around apps\whatsapp-kb-bot\autostart.ps1, the same
REM  script the WhatsAppKB_Bot_* scheduled tasks run. No logic here.
REM  Covers src/index.js (group Q&A bot) + src/http-server.js (:9269).
REM  Does NOT touch notify.js (P1 alert push, autostart-notify.ps1).
REM
REM  Usage:  olbr-bot.bat start | stop | restart | status
REM  ASCII-only + CRLF on purpose; non-ASCII breaks cmd parsing.
REM ============================================================
setlocal
chcp 65001 >nul 2>&1
set "BOT=%~dp0..\..\apps\whatsapp-kb-bot\autostart.ps1"
set "PSEXE=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
set "ACT=%~1"
if "%ACT%"=="" set "ACT=status"
title OLBR - whatsapp bot %ACT%

if not exist "%BOT%" goto missing
if /i "%ACT%"=="start" goto start
if /i "%ACT%"=="stop" goto stop
if /i "%ACT%"=="status" goto status
if /i "%ACT%"=="restart" goto restart
echo [ERROR] Unknown action: %ACT%  - use start, stop, restart or status.
goto done

:missing
echo [ERROR] Not found: %BOT%
goto done

:start
echo Starting WhatsApp bot (src/index.js) + web Q^&A assistant (:9269) ...
echo Waits up to 60s for the WhatsApp handshake.
echo.
"%PSEXE%" -NoProfile -ExecutionPolicy Bypass -File "%BOT%"
echo.
echo ---- current status ----
"%PSEXE%" -NoProfile -ExecutionPolicy Bypass -File "%BOT%" -Status
goto done

:stop
echo This will STOP the WhatsApp bot (src/index.js) + web Q^&A assistant (:9269).
echo The P1 alert push (notify.js) is NOT touched.
choice /c YN /n /m "Stop now? [Y/N] "
if errorlevel 2 goto done
"%PSEXE%" -NoProfile -ExecutionPolicy Bypass -File "%BOT%" -Stop
echo.
echo NOTE: the WhatsAppKB_Bot_EnsureRunning task brings the bot back within
echo       5 minutes. To keep it down, disable both tasks first:
echo         Disable-ScheduledTask -TaskName WhatsAppKB_Bot_Autostart
echo         Disable-ScheduledTask -TaskName WhatsAppKB_Bot_EnsureRunning
goto done

:status
"%PSEXE%" -NoProfile -ExecutionPolicy Bypass -File "%BOT%" -Status
goto done

:restart
echo This will RESTART the WhatsApp bot (src/index.js) + web Q^&A assistant (:9269).
echo The P1 alert push (notify.js) is NOT touched.
choice /c YN /n /m "Restart now? [Y/N] "
if errorlevel 2 goto done
"%PSEXE%" -NoProfile -ExecutionPolicy Bypass -File "%BOT%" -Stop
echo.
echo [*] Starting WhatsApp bot again ...
"%PSEXE%" -NoProfile -ExecutionPolicy Bypass -File "%BOT%"
echo.
echo ---- current status ----
"%PSEXE%" -NoProfile -ExecutionPolicy Bypass -File "%BOT%" -Status
goto done

:done
echo.
pause
endlocal
