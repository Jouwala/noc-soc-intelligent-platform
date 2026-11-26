@echo off
REM ============================================================
REM  stop_gui.bat - double-click STOP entry for the my_app shortcut.
REM  Calls stop.bat (unchanged), then shows the unified ending.
REM
REM  Why a wrapper: stop.bat is ALSO called programmatically by
REM  portal-ctl.ps1 -Action stop (Start-Process -Wait) and by restart.bat,
REM  so it must NOT pause. This wrapper adds the interactive ending for the
REM  shortcut only; stop.bat stays byte-identical for those callers.
REM  Keep this file ASCII-only (cmd batch parsing).
REM ============================================================
setlocal
cd /d "%~dp0"
title my_app - stop
echo [*] Stopping my_app (PostgreSQL kept) ...
call "%~dp0stop.bat"
call "%~dp0..\..\ops\deploy\finish.bat" "7038,7090" "my_app stop"
endlocal
