@echo off
title Restore GPU settings for AI
echo Close the Pearl mining window before continuing.
pause
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "if (Get-Process krig-miner -ErrorAction SilentlyContinue) { Write-Host 'Miner still running. Stop it first.'; exit 1 }; $p = Start-Process powershell.exe -Verb RunAs -WindowStyle Hidden -ArgumentList '-NoProfile -ExecutionPolicy Bypass -File C:\Users\pjunm\Pearl-mining\RESTORE-AI-POWER.ps1' -Wait -PassThru; exit $p.ExitCode"
if errorlevel 1 goto end
echo GPU clock locks released and original power limits restored.
echo Open your existing Local AI Console desktop shortcut.
:end
pause
