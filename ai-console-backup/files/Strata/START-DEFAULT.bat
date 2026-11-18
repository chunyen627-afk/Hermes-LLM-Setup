@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
start "" "%~dp0.venv\Scripts\pythonw.exe" "%~dp0media_control.py"
