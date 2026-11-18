@echo off
chcp 65001 >nul
setlocal
title Qwen3.8 GPU Server [think=high]
cd /d "%~dp0"

echo ============================================
echo   Qwen3.8-27B GPU Server
echo   Think mode: high
echo.
echo   WARNING: measured low 2.5s vs high 80s per turn. One hardware
echo   debug task took 740s on a single turn. Only use this for a
echo   short, genuinely hard reasoning question - never a long task.
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