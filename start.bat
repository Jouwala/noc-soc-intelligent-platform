@echo off
REM OLBR portable copy - start all four subsystems.
REM Thin shell only: real logic lives in ops\deploy\start_all.ps1.
REM Keep this file ASCII-only and CRLF; non-ASCII breaks cmd parsing.
setlocal
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0ops\deploy\start_all.ps1" %*
endlocal