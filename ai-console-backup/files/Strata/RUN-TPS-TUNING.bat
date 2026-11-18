@echo off
setlocal
chcp 65001 >nul
set PYTHONUTF8=1
title Strata TPS tuning - close this window to stop
cd /d "%~dp0"
"%~dp0.venv\Scripts\python.exe" -u -m serve.server --engine strata --config "%~dp0%~1" --host 127.0.0.1 --port 8001
echo Tuning service stopped.
