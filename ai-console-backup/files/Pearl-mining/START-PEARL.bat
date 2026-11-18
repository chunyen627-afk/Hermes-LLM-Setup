@echo off
title Pearl PRL - guarded balanced mode
cd /d "%~dp0"
powershell.exe -NoProfile -Command "Start-Process powershell.exe -Verb RunAs -WindowStyle Normal -ArgumentList '-NoProfile -ExecutionPolicy Bypass -File C:\Users\pjunm\Pearl-mining\RUN-MINING.ps1'"
exit /b
