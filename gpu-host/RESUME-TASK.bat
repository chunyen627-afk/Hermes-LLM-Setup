@echo off
REM Resume the last task that was cut off by the tool-call limit.
REM Picks up the handoff report it wrote itself and reruns with --max-turns 500.
REM If the last task ended normally, it will say there is nothing to resume.
REM ASCII comments on purpose: cmd reads .bat in the OEM codepage.
chcp 65001 >nul
title Resume last task
cd /d "%~dp0"

set PY=C:\Users\pjunm\AppData\Local\Programs\Python\Python311\python.exe
if not exist "%PY%" set PY=python

"%PY%" "%~dp0_autorelay.py" --resume-last

echo.
pause
