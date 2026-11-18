@echo off
setlocal
chcp 65001 >nul
set PYTHONUTF8=1
set XFORMERS_DISABLED=1
title H3 Video - GPU 1 - close to release memory
cd /d "C:\Users\pjunm\ComfyUI-H3"
powershell -NoProfile -Command "if (Get-NetTCPConnection -LocalPort 8189 -State Listen -ErrorAction SilentlyContinue) { Write-Host 'H3 is already running. No duplicate was started.'; exit 1 }"
if errorlevel 1 exit /b 1
"C:\Users\pjunm\ComfyUI-H3\.venv\Scripts\python.exe" -u "C:\Users\pjunm\ComfyUI-H3\main.py" --listen 127.0.0.1 --port 8189 --cuda-device 1 --enable-dynamic-vram --reserve-vram 1 --use-pytorch-cross-attention --auto-launch
echo H3 stopped.
pause
