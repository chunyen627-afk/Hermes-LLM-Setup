@echo off
setlocal
title Image Proxy (8003)
cd /d "C:\Users\pjunm\OneDrive\Desktop\hermes"
echo Image Proxy: close this window to stop the image API.
"C:\Users\pjunm\AppData\Local\Programs\Python\Python311\python.exe" "C:\Users\pjunm\OneDrive\Desktop\hermes\comfy_proxy.py"
echo.
echo Image Proxy stopped.
pause
