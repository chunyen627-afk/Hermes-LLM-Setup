@echo off
rem Starts the guarded miner in the background (asks for administrator permission; no window to
rem click or close by accident). Stop with the stop shortcut or from the phone app.
powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process cmd.exe -Verb RunAs -WindowStyle Hidden -ArgumentList '/c powershell -NoProfile -ExecutionPolicy Bypass -File C:\Users\pjunm\Pearl-mining\laptop_miner.ps1 > C:\Users\pjunm\Pearl-mining\guard-console.log 2>&1'"
echo Mining started in the background. Use the stop shortcut or the phone app to stop it.
timeout /t 5 >nul
