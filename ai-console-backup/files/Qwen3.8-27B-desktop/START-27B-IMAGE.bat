@echo off
setlocal
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0_start_coexist.ps1" -Media Image
if errorlevel 1 pause
