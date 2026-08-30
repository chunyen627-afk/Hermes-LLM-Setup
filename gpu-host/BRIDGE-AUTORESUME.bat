@echo off
REM Bridge with auto-resume enabled.
REM When a task stops (hit tool-call limit, or finished a stage), the bridge
REM checks _autoguard.py first, then relaunches automatically if it passes.
REM Use _bridge.bat instead if you want a menu instead of auto-run.
REM Comments kept in ASCII on purpose: cmd reads .bat in the OEM codepage,
REM so UTF-8 Chinese here shows up as garbage and gets run as commands.
chcp 65001 >nul
title Hermes Bridge - AUTO RESUME
cd /d "%~dp0"

set HERMES_AUTORESUME=1
set PY=C:\Users\pjunm\AppData\Local\Programs\Python\Python311\python.exe
if not exist "%PY%" set PY=python

echo [AUTO-RESUME ON] max 8 relays, 12h cap, guarded by _autoguard.py
echo.
"%PY%" "%~dp0hermes_bridge.py" 127.0.0.1 10
pause
