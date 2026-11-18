@echo off
chcp 65001 >nul
title Qwen3.8 GPU Server - Menu
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0_bootmenu.ps1"
