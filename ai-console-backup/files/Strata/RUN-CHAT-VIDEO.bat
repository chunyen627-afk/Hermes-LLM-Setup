@echo off
setlocal
chcp 65001 >nul
set PYTHONUTF8=1
title Strata chat - Video profile - GPUs 0 and 2
cd /d "%~dp0"
if not exist "packs\swift-abliterated-iq3_xxs\native_experts.txt" exit /b 1
powershell -NoProfile -Command "$loading = @(Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'python.exe' -and $_.CommandLine -like '*C:\Users\pjunm\Strata\*' -and $_.CommandLine -match '-m serve\.server' -and $_.CommandLine -match '--port\s+8001(?:\s|$)' }); if ($loading.Count -or (Get-NetTCPConnection -LocalPort 8001 -State Listen -ErrorAction SilentlyContinue)) { Write-Host 'Chat is already running or loading. No duplicate was started.'; exit 1 }"
if errorlevel 1 exit /b 1
"%~dp0.venv\Scripts\python.exe" -u -m serve.server --engine strata --config "%~dp0strata-swift-iq3_xxs-video.json" --host 127.0.0.1 --port 8001
echo Chat service stopped.
pause
