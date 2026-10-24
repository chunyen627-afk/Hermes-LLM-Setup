@echo off
cd /d "%~dp0"
title Pearl - guarded mining
".venv\Scripts\python.exe" portable_guard.py
pause
