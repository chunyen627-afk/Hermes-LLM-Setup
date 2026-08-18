@echo off
chcp 65001 >nul
title STOP Qwen3.8 GPU Server
echo Stopping llama-server ...
taskkill /IM llama-server.exe /F 2>nul
if errorlevel 1 (echo   [INFO] not running) else (echo   [OK] stopped, VRAM freed)
echo.
nvidia-smi --query-gpu=index,memory.used --format=csv,noheader
echo.
pause