@echo off
REM ============================================================
REM  OLBR portable copy - stop only services this copy started
REM  Portable: every path is derived from %~dp0, never a fixed drive.
REM  ASCII-only + CRLF on purpose; non-ASCII breaks cmd parsing.
REM ============================================================
setlocal
cd /d "%~dp0.."
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0..\my_portal_ctl.ps1" 9-stop-all

echo.
echo ---- done. Press any key to close. ----
pause >nul
endlocal
