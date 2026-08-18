@echo off
chcp 65001 >nul
title Qwen3.8 - Context 用量
cd /d "%~dp0"
python "%~dp0_checkctx.py" %1
echo.
pause
