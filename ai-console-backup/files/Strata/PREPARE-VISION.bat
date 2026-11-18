@echo off
setlocal
chcp 65001 >nul
set PYTHONUTF8=1
title Strata - own vision projector download
cd /d "%~dp0"
"C:\Users\pjunm\ComfyUI-H3\.venv\Scripts\python.exe" -u "%~dp0fetch_orca_vision.py"
echo Vision preparation exited with code %ERRORLEVEL%.
pause
