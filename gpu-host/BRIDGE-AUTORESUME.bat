@echo off
chcp 65001 >nul
title Hermes Bridge (auto-resume ON)
cd /d "%~dp0"

REM 橋接器 + 自動接續：偵測到任務撞工具上限就自動用它的交接報告重新派工。
REM 一般用 _bridge.bat（只提示不自動執行）。這支是要離開電腦、
REM 想讓長任務自己跑下去時用的。最多自動接續 5 次。

set HERMES_AUTORESUME=1
echo [自動接續已開啟] 偵測到撞上限會自動重新派工，最多 5 次。
echo.
"C:\Users\pjunm\AppData\Local\Programs\Python\Python311\python.exe" "%~dp0hermes_bridge.py" 127.0.0.1 10
pause
