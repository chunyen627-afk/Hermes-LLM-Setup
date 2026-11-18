@echo off
chcp 65001 >nul
title Qwen3.8 - live monitor
cd /d "%~dp0"
python "%~dp0_watch.py" %1
pause
