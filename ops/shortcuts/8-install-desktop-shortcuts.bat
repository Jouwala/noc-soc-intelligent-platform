@echo off
REM ============================================================
REM  OLBR - create the desktop shortcuts for THIS machine
REM  The generator now lives in desktop_shortcuts\: it no longer
REM  depends on the 01_RPA-era my_portal_ctl.ps1 and builds the three
REM  OLBR master links (start / stop / status) plus ten per-component
REM  links (my_app / portal / nginx incl. reload / WhatsApp bot)
REM  into %USERPROFILE%\Desktop\olbr_app.
REM  ASCII-only + CRLF on purpose; non-ASCII breaks cmd parsing.
REM ============================================================
setlocal
cd /d "%~dp0..\.."
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "%~dp0..\..\desktop_shortcuts\make_desktop_shortcuts.ps1" -WriteDesktop

echo.
echo ---- done. Press any key to close. ----
pause >nul
endlocal
