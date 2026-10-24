@echo off
cd /d "%~dp0"
title Pearl - guarded mining
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0launch.ps1"
