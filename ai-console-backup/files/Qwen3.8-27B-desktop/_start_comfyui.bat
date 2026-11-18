@echo off
setlocal
title ComfyUI - Image Generation (8188)
cd /d "C:\Users\pjunm\OneDrive\Desktop\hermes\ComfyUI"
set XFORMERS_DISABLED=1
set XFORMERS_MORE_DETAILS=0
echo ComfyUI: close this window to stop image generation and release its GPU memory.
"C:\Users\pjunm\AppData\Local\Programs\Python\Python311\python.exe" "C:\Users\pjunm\OneDrive\Desktop\hermes\ComfyUI\main.py" --port 8188 --use-pytorch-cross-attention
echo.
echo ComfyUI stopped.
pause
