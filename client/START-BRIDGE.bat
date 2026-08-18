@echo off
chcp 65001 >nul
title Hermes Bridge 1234 to 8001

REM ===== 用法 =====
REM   START-BRIDGE.bat              連預設 GPU 主機
REM   START-BRIDGE.bat 192.168.1.5  連指定 IP
REM
REM ===== 首次使用改這兩行 =====
set "GPU=%~1"
if "%GPU%"=="" set "GPU=10.35.219.64"
REM 若 python 不在 PATH，改成完整路徑，例如：
REM   set "PY=C:\Users\你的帳號\AppData\Local\Programs\Python\Python311\python.exe"
set "PY=python"

set "SCRIPT=%~dp0hermes_bridge.py"

REM 已經在跑就不重複開
netstat -ano | findstr /C:"127.0.0.1:1234" | findstr /C:"LISTENING" >nul
if not errorlevel 1 goto already

if not exist "%SCRIPT%" (
  echo [ERROR] 找不到 %SCRIPT%
  echo         hermes_bridge.py 要跟這個 .bat 放在同一個資料夾。
  echo.
  pause
  exit /b 1
)

"%PY%" "%SCRIPT%" %GPU%

echo.
echo 橋接器已結束。
echo 若上面顯示找不到 python，請編輯本檔把 PY 改成完整路徑。
pause
exit /b

:already
echo.
echo   橋接器已經在執行中，不需要重複開啟。
echo   若要重開，請先關掉原本那個視窗。
echo.
pause
exit /b
