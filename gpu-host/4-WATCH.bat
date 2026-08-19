@echo off
chcp 65001 >nul
title Qwen3.8 即時監控
cd /d "%~dp0"
python "%~dp0_watch.py" %1
pause
