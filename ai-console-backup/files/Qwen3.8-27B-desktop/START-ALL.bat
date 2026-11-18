@echo off
setlocal
REM ============================================================
REM  Start everything in one go:
REM    1. llama-server :8001  (model, 2 slots x 120K ctx + mmproj vision)
REM    2. bridge       :1234  (Hermes and the family GUI both go through it)
REM    3. family GUI   :5000  (phone/family, vision runs on the local 27B)
REM    4. image services :8188 / :8003 (visible windows; close to stop)
REM  Each step probes first and skips if already running.
REM
REM  The boot task Qwen38-GPU-Server runs this same file, so boot and
REM  manual launch take the same path.
REM
REM  ASCII only, on purpose: cmd reads .bat in the OEM codepage, so UTF-8
REM  Chinese comments turn into garbage and get executed as commands.
REM  Chinese filenames break Set-ScheduledTask the same way.
REM ============================================================
chcp 65001 >nul
title Qwen3.8-27B - start all
cd /d "%~dp0"

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0_switch_gpu_mode.ps1" -Mode Chat
if errorlevel 1 ( pause & exit /b 1 )

echo [1/4] llama-server ...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0_bootmenu.ps1"

echo.
echo [2/4] bridge ...
powershell -NoProfile -Command "try { Invoke-RestMethod -Uri 'http://127.0.0.1:1234/v1/models' -TimeoutSec 3 -EA Stop | Out-Null; Write-Host '      [OK] already running' -ForegroundColor Green } catch { Write-Host '      starting...' -ForegroundColor Cyan; Start-Process -FilePath '%~dp0_bridge.bat' }"

echo.
echo [3/4] family GUI ...
powershell -NoProfile -Command "$g='C:\Users\pjunm\OneDrive\Desktop\hermes\remote-station\gui\START-FAMILY-GUI.bat'; try { Invoke-WebRequest -Uri 'http://127.0.0.1:5000/' -TimeoutSec 3 -UseBasicParsing -EA Stop | Out-Null; Write-Host '      [OK] already running' -ForegroundColor Green } catch { if (Test-Path -LiteralPath $g) { Write-Host '      starting...' -ForegroundColor Cyan; Start-Process -FilePath $g -WindowStyle Minimized } else { Write-Host '      [SKIP] launcher not found' -ForegroundColor DarkYellow } }"

echo.
echo [4/4] image generation ...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0_start_image_services.ps1"

echo.
echo ============================================================
echo   Done. Keep these windows open (minimizing is fine).
echo     model   http://127.0.0.1:8001
echo     bridge  http://127.0.0.1:1234
echo     family  http://127.0.0.1:5000
echo     images  ComfyUI and Image Proxy windows (close to stop)
echo ============================================================
timeout /t 8 /nobreak >nul
