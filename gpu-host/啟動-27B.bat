@echo off
chcp 65001 >nul
title Qwen3.8-27B GPU Server
cd /d "%~dp0"

REM 跑跟開機排程（Qwen38-GPU-Server）完全一樣的流程：
REM _bootmenu.ps1 會顯示模式選單、10 秒沒選就用預設（韌體模式 2 slot 各 120K）
REM 再由 _ensure_38.ps1 帶起 llama-server（含 mmproj 視覺）。
REM 已經在跑的話 _ensure_38.ps1 會偵測到並直接結束，不會重複啟動。
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0_bootmenu.ps1"

REM 順便把橋接器帶起來（Hermes 走 :1234 連它）
powershell -NoProfile -Command ^
  "try { Invoke-RestMethod -Uri 'http://127.0.0.1:1234/v1/models' -TimeoutSec 3 -EA Stop | Out-Null; Write-Host '[OK] 橋接器已在執行' -ForegroundColor Green } catch { Write-Host '[..] 啟動橋接器' -ForegroundColor Cyan; Start-Process -FilePath '%~dp0_bridge.bat' }"

echo.
echo 完成。這個視窗可以關掉，server 會繼續在背景跑。
timeout /t 5 /nobreak >nul
