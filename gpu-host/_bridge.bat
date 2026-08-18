@echo off
chcp 65001 >nul
title Hermes Bridge (1234 - 8001)
cd /d "%~dp0"
"C:\Users\pjunm\AppData\Local\Programs\Python\Python311\python.exe" "%~dp0hermes_bridge.py" 127.0.0.1 8001
pause
