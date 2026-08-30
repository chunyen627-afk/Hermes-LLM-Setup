@echo off
chcp 65001 >nul
title 派新任務給 27B
cd /d "%~dp0"

REM 把題目寫在 task.txt，雙擊這支就派工。
REM 用檔案而不是命令列 —— 中文、換行、引號都不會被 shell 弄壞。

set PY=C:\Users\pjunm\AppData\Local\Programs\Python\Python311\python.exe
if not exist "%PY%" set PY=python
set H=%LOCALAPPDATA%\hermes\hermes-agent\venv\Scripts\hermes.exe
set TASK=%~dp0task.txt

if not exist "%TASK%" (
    echo 找不到 task.txt
    echo.
    echo 請先在這個資料夾建立 task.txt，把題目寫進去，再跑一次。
    echo   %~dp0task.txt
    echo.
    pause
    exit /b 1
)

echo 題目內容：
echo ----------------------------------------
type "%TASK%"
echo ----------------------------------------
echo.
set /p GO=確定要派這個任務嗎？(Y/N) 
if /i not "%GO%"=="Y" (
    echo 已取消。
    pause
    exit /b 0
)

echo.
echo 派工中（--max-turns 500）。這個視窗可以縮小，但不要關。
echo.
"%H%" chat --query-file "%TASK%" --max-turns 500

echo.
echo 任務結束。
pause
