@echo off
chcp 65001 >nul
title Qwen3.8 - Context 用量
cd /d "%~dp0"

REM 預設查 GPU 那台，也可傳入其他 IP：3-CHECK-CTX.bat 192.168.1.50
set "TARGET=10.35.219.64"
if not "%~1"=="" set "TARGET=%~1"

python "%~dp0_checkctx.py" %TARGET%
if errorlevel 1 (
  echo.
  echo [提示] 如果說找不到 python，改用完整路徑，例如：
  echo        "C:\Users\%USERNAME%\AppData\Local\Programs\Python\Python311\python.exe" "%~dp0_checkctx.py" %TARGET%
)
echo.
pause
