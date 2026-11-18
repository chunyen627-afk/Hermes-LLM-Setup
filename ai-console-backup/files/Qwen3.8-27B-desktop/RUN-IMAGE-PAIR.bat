@echo off
setlocal
title 27B + Static Images (8188)
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0_start_image_coexist.ps1"
