@echo off
REM Model switcher launcher. PURE ASCII ONLY (Chinese breaks cmd parsing here).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0_switch_model.ps1"
