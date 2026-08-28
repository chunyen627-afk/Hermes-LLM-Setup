@echo off
chcp 65001 >nul
setlocal
title Qwen3.8 GPU Server [FIRMWARE mode]
cd /d "%~dp0"

echo ============================================
echo   Qwen3.8-27B GPU Server
echo   FIRMWARE mode - STM32 / RTOS / hardware
echo.
echo   think = low   (see note below)
echo   temp  = 0.3   (numbers/registers stay stable)
echo   ctx   = 204800 (200K, single slot)
echo ============================================
echo.
echo   NOTE: this used to run think=high. Measured: low 2.5s vs
echo   high 80s per turn, and one hardware-debug task hit 740s
echo   on a single turn. A long task never finishes that way.
echo   Firmware work needs low temp, not deep thinking.
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0_ensure_38.ps1" -Think low -Mode fw -Ctx 204800 -Slots 1
if errorlevel 1 ( echo. & echo [ERROR] failed to start & pause & exit /b 1 )

echo.
echo   [OK] Server ready (firmware mode)
echo   Closing this window does NOT stop the server.
echo.
pause
endlocal
