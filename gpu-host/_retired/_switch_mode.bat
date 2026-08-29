@echo off
REM Slot mode switcher. PURE ASCII ONLY (Chinese breaks cmd parsing here).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0_switch_mode.ps1"
