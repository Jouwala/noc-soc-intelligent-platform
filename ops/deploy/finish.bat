@echo off
REM ============================================================
REM  finish.bat - unified interactive ending for double-click entries.
REM  Prints a completion banner + live port status + a labeled pause.
REM
REM  Usage:  call finish.bat <ports-csv> "<action-label>"
REM          call finish.bat 7038,7090 "my_app restart"
REM          call finish.bat "" "OLBR start"   (no port block; caller already showed status)
REM
REM  Read-only: port-status.ps1 only queries listeners. The pause is for
REM  double-click entries ONLY. Programmatic cores (start_hidden.ps1 /
REM  stop_services.ps1 / my_app stop.bat / nginx.exe) never call this, so
REM  portal-ctl and the scheduled tasks are unaffected.
REM  Keep this file ASCII-only (cmd batch parsing).
REM ============================================================
setlocal
set "PORTS=%~1"
set "LABEL=%~2"
if "%LABEL%"=="" set "LABEL=operation"
echo.
echo ============================================
echo   [DONE] %LABEL% complete.
if not "%PORTS%"=="" (
    echo   Live status:
    powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0port-status.ps1" -Ports %PORTS%
)
echo ============================================
echo   Press Enter to close this window.
pause >nul
endlocal
