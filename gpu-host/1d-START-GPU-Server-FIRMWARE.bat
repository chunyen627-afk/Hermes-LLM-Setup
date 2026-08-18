@echo off
chcp 65001 >nul
setlocal
title Qwen3.8 GPU Server [FIRMWARE mode]
cd /d "%~dp0"

echo ============================================
echo   Qwen3.8-27B GPU Server
echo   FIRMWARE mode - STM32 / RTOS / hardware
echo.
echo   think = high  (deep reasoning)
echo   temp  = 0.3   (numbers/registers stay stable)
echo   ctx   = 143360 (140K, safety margin)
echo ============================================
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0_ensure_38.ps1" -Think high -Mode fw -Ctx 143360
if errorlevel 1 ( echo. & echo [ERROR] failed to start & pause & exit /b 1 )

echo.
echo   [OK] Server ready (firmware mode)
echo   Other PC connects to: http://10.35.219.64:8001
echo   Closing this window does NOT stop the server.
echo.
pause
endlocal
