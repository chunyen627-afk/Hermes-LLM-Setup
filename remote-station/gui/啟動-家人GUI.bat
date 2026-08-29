@echo off
chcp 65001 >nul
title 家人聊天介面 :5000
cd /d "%~dp0"

REM 寫死直譯器路徑：PATH 上的 python 可能指到 Hermes 的 venv，
REM 那裡沒裝 flask/requests，會直接 ImportError。
set PY=C:\Users\pjunm\AppData\Local\Programs\Python\Python311\python.exe
if not exist "%PY%" set PY=python

REM 看圖走本機 27B（:1234 橋接器），需要 llama-server 跑 2 slot。
REM 沒開 server 的話先跑桌面的「啟動 27B」。
"%PY%" app.py

echo.
echo 服務結束。
pause
