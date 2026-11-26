@echo off
chcp 65001 >nul 2>&1
title OLBR - start
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0portal-ctl.ps1" -Action start
call "%~dp0finish.bat" "" "OLBR start"
