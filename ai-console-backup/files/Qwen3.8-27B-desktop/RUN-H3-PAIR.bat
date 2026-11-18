@echo off
setlocal
title 27B + Video - H3 (8189)
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0_start_h3.ps1" -Coexist
