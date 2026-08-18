@echo off
chcp 65001 >nul
setlocal
title Qwen3.8 GPU Server [think=off]
cd /d "%~dp0"

echo ============================================
echo   Qwen3.8-27B GPU Server
echo   Think mode: off  --  chat / simple Q&A (fastest)
echo ============================================
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0_ensure_38.ps1" -Think off
if errorlevel 1 ( echo. & echo [ERROR] failed to start & pause & exit /b 1 )

echo.
echo   [OK] Server ready  ^(think=off^)
echo   Other PC connects to: http://10.35.219.64:8001
echo   Closing this window does NOT stop the server.
echo.
pause
endlocal