@echo off
chcp 65001 >nul
title Qwen3.8 - context usage
cd /d "%~dp0"
python "%~dp0_checkctx.py" %1
echo.
pause
