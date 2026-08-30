@echo off
chcp 65001 >nul
title 接續上一個任務
cd /d "%~dp0"

REM 接續最近一個「撞工具上限被中斷」的任務。
REM 會自動抓它自己寫的交接報告當新的 prompt，用 --max-turns 500 重跑。
REM 如果上一個任務是正常結束（不是撞上限），會直接告訴你沒東西可接。

set PY=C:\Users\pjunm\AppData\Local\Programs\Python\Python311\python.exe
if not exist "%PY%" set PY=python

"%PY%" "%~dp0_autorelay.py" --resume-last

echo.
pause
