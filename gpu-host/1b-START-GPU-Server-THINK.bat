@echo off
chcp 65001 >nul
setlocal
title Qwen3.8 GPU Server [think=high]
cd /d "%~dp0"

echo ============================================
echo   Qwen3.8-27B GPU Server
echo   Think mode: high  --  STM32 / hardware design / hard debugging
echo ============================================
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0_ensure_38.ps1" -Think high
if errorlevel 1 ( echo. & echo [ERROR] failed to start & pause & exit /b 1 )

echo.
echo   [OK] Server ready  ^(think=high^)
echo   Other PC connects to: http://10.35.219.64:8001
echo   Closing this window does NOT stop the server.
echo.
pause
endlocal