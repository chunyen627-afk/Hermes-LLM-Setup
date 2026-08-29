@echo off
chcp 65001 >nul
title Qwen3.8-27B - start all
cd /d "%~dp0"

REM ============================================================
REM  一次把整套帶起來：
REM    1. llama-server :8001  模型本體（2 slot 各 120K + mmproj 視覺）
REM    2. 橋接器      :1234  Hermes 和家人 GUI 都走這個
REM    3. 家人 GUI    :5000  手機/家人用，看圖走本機 27B
REM  每一項都先偵測，已經在跑就跳過，不會重複啟動。
REM
REM  開機排程 Qwen38-GPU-Server 也是跑這支，所以開機和手動同一條路。
REM  檔名和路徑都用 ASCII —— 中文檔名在排程/PowerShell 之間會編碼壞掉。
REM ============================================================

echo [1/3] llama-server ...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0_bootmenu.ps1"

echo.
echo [2/3] bridge ...
powershell -NoProfile -Command "try { Invoke-RestMethod -Uri 'http://127.0.0.1:1234/v1/models' -TimeoutSec 3 -EA Stop | Out-Null; Write-Host '      [OK] already running' -ForegroundColor Green } catch { Write-Host '      starting...' -ForegroundColor Cyan; Start-Process -FilePath '%~dp0_bridge.bat' }"

echo.
echo [3/3] family GUI ...
powershell -NoProfile -Command "$g='C:\Users\pjunm\OneDrive\Desktop\hermes\remote-station\gui\START-FAMILY-GUI.bat'; try { Invoke-WebRequest -Uri 'http://127.0.0.1:5000/' -TimeoutSec 3 -UseBasicParsing -EA Stop | Out-Null; Write-Host '      [OK] already running' -ForegroundColor Green } catch { if (Test-Path -LiteralPath $g) { Write-Host '      starting...' -ForegroundColor Cyan; Start-Process -FilePath $g -WindowStyle Minimized } else { Write-Host '      [SKIP] launcher not found' -ForegroundColor DarkYellow } }"

echo.
echo ============================================================
echo   done. 這三個視窗可以縮到最小，但不要關掉。
echo     model   http://127.0.0.1:8001
echo     bridge  http://127.0.0.1:1234
echo     family  http://127.0.0.1:5000
echo ============================================================
timeout /t 8 /nobreak >nul
