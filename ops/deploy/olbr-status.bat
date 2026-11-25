@echo off
chcp 65001 >nul 2>&1
title OLBR - status
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0portal-ctl.ps1" -Action status
call "%~dp0finish.bat" "" "OLBR status"
