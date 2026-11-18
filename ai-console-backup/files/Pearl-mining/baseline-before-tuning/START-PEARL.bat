@echo off
title Pearl PRL - guarded low-power mode
cd /d "%~dp0"
echo Stop the Local AI Console services before mining.
echo Close this window or press Ctrl+C to stop mining.
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$p = Start-Process powershell.exe -Verb RunAs -WindowStyle Hidden -ArgumentList '-NoProfile -ExecutionPolicy Bypass -File C:\Users\pjunm\Pearl-mining\SET-LOW-CLOCKS.ps1' -Wait -PassThru; exit $p.ExitCode"
if errorlevel 1 goto end
"C:\Users\pjunm\AppData\Local\Programs\Python\Python311\python.exe" "%~dp0guarded_miner.py"
:end
echo To use AI again, run RESTORE-AI.bat first.
pause
