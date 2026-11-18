@echo off
setlocal
chcp 65001 >nul
set PYTHONUTF8=1
title Strata - 2 GPUs - 32K context
cd /d "%~dp0"
powershell -NoProfile -Command "if (Get-NetTCPConnection -LocalPort 8080 -State Listen -ErrorAction SilentlyContinue) { Write-Host 'Port 8080 is occupied. No second Strata was started.'; exit 1 }"
if errorlevel 1 exit /b 1
if not exist "packs\orca-iq3_xxs\native_experts.txt" (
  echo Model preparation has not finished. Run PREPARE-ORCA.bat first.
  pause
  exit /b 1
)
"%~dp0.venv\Scripts\python.exe" -u -m serve.server --engine strata --config "%~dp0strata-orca-iq3_xxs.json" --host 127.0.0.1 --port 8080
set "RESULT=%ERRORLEVEL%"
echo Strata stopped. Exit code: %RESULT%
pause
exit /b %RESULT%
