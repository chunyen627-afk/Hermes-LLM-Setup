@echo off
setlocal enabledelayedexpansion

REM Hermes desktop launcher - run minimized via the desktop shortcut.
REM PURE ASCII ONLY - Chinese text in a .bat breaks cmd parsing on this box.
REM Starts llama-server and the bridge automatically if they are not running.

set "MENU_DIR=C:\Users\pjunm\OneDrive\Desktop\Qwen3.8-27B"
set "BRIDGE=%MENU_DIR%\_bridge.bat"
set "ENSURE=%MENU_DIR%\_ensure_38.ps1"

REM Hermes lives under the Claude package container on this machine.
REM Official installer path first; the old git-version path is the fallback.
set "APP=C:\Users\pjunm\AppData\Local\hermes\hermes-agent\apps\desktop\release\win-unpacked\Hermes.exe"
if not exist "%APP%" set "APP=C:\Users\pjunm\AppData\Local\Packages\Claude_pzs8sxrjxfjjc\LocalCache\Local\hermes\hermes-agent\apps\desktop\release\win-unpacked\Hermes.exe"
if not exist "%APP%" goto no_app

REM --- 1. llama-server: start it if it is not up, then wait for it ---
curl -s -m 5 http://127.0.0.1:8001/v1/models >nul 2>&1
if not errorlevel 1 goto server_ok

if not exist "%ENSURE%" goto no_server
powershell -NoProfile -ExecutionPolicy Bypass -File "%ENSURE%" -Think low -Mode fw -Ctx 245760 -Slots 2 >nul 2>&1

set /a tries=0
:wait_server
curl -s -m 3 http://127.0.0.1:8001/v1/models >nul 2>&1
if not errorlevel 1 goto server_ok
set /a tries+=1
if !tries! geq 60 goto no_server
ping -n 3 127.0.0.1 >nul
goto wait_server

:server_ok

REM --- 2. bridge: start it if it is not listening ---
curl -s -m 5 http://127.0.0.1:1234/v1/models >nul 2>&1
if errorlevel 1 (
    if exist "%BRIDGE%" (
        start "Hermes Bridge" /min "%BRIDGE%"
        ping -n 4 127.0.0.1 >nul
    )
)

set "HERMES_INFERENCE_BASE_URL=http://127.0.0.1:1234/v1"
curl -s -m 5 http://127.0.0.1:1234/v1/models >nul 2>&1
if errorlevel 1 set "HERMES_INFERENCE_BASE_URL=http://127.0.0.1:8001/v1"

set "HERMES_MODEL=qwen38_mtp"
set "LM_API_KEY=lmstudio"
set "OPENAI_API_KEY=lmstudio"
set "HERMES_DESKTOP_CWD=%MENU_DIR%"

start "" "%APP%"
exit /b 0

:no_server
powershell -NoProfile -Command "Add-Type -AssemblyName PresentationFramework; [System.Windows.MessageBox]::Show('Could not start llama-server on :8001.`n`nTry running 0-MENU.bat manually.','Hermes Desktop','OK','Warning')" >nul 2>&1
exit /b 10

:no_app
powershell -NoProfile -Command "Add-Type -AssemblyName PresentationFramework; [System.Windows.MessageBox]::Show('Hermes.exe not found.','Hermes Desktop','OK','Error')" >nul 2>&1
exit /b 11
