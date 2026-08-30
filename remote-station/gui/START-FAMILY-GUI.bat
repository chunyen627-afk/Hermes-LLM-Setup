@echo off
chcp 65001 >nul
title Family chat GUI :5000
cd /d "%~dp0"

REM Hardcode the interpreter: python on PATH may point at the Hermes venv,
REM which has no flask/requests and fails with ImportError.
set PY=C:\Users\pjunm\AppData\Local\Programs\Python\Python311\python.exe
if not exist "%PY%" set PY=python

REM Vision runs on the local 27B via the bridge (:1234); needs 2 slots.
REM Start the server first via the desktop shortcut if it is not up.
"%PY%" app.py

echo.
echo Service stopped.
pause
