@echo off
setlocal
chcp 65001 >nul
set PYTHONUTF8=1
set XFORMERS_DISABLED=1
title Static Images - GPU 1 - close to release memory
cd /d "C:\Users\pjunm\OneDrive\Desktop\hermes\ComfyUI"
powershell -NoProfile -Command "if (Get-NetTCPConnection -LocalPort 8188 -State Listen -ErrorAction SilentlyContinue) { Write-Host 'Static images are already running. No duplicate was started.'; exit 1 }"
if errorlevel 1 exit /b 1
"C:\Users\pjunm\AppData\Local\Programs\Python\Python311\python.exe" -u "C:\Users\pjunm\OneDrive\Desktop\hermes\ComfyUI\main.py" --listen 127.0.0.1 --port 8188 --cuda-device 1 --lowvram --reserve-vram 1 --use-pytorch-cross-attention
echo Static image service stopped.
pause
