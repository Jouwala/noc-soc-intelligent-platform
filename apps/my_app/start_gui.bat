@echo off
REM ============================================================
REM  start_gui.bat - double-click START entry for the my_app shortcut.
REM  Calls start_hidden.ps1 -Force (same as the shortcut always did), then
REM  shows the unified "complete + live status + Enter" ending.
REM
REM  Why separate from start.bat: start.bat is the legacy-controller entry
REM  (MY_PORTAL_NO_POPUP / MY_PORTAL_NO_PAUSE guards) and does NOT pass -Force.
REM  The shortcut needs -Force (force-free 7038 if a non-watchdog holds it),
REM  so this thin wrapper keeps that behavior and adds the ending. start.bat
REM  and start_hidden.ps1 are left unchanged for programmatic callers.
REM  Keep this file ASCII-only (cmd batch parsing).
REM ============================================================
setlocal
cd /d "%~dp0"
title my_app - start
echo [*] Starting my_app (backend + workers + frontend; PostgreSQL kept) ...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_hidden.ps1" -Force
call "%~dp0..\..\ops\deploy\finish.bat" "7038,7090" "my_app start"
endlocal
