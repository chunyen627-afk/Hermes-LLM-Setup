@echo off
title Strata - download and prepare Orca IQ3_XXS
cd /d "%~dp0"
"C:\Users\pjunm\ComfyUI-H3\.venv\Scripts\python.exe" -u "%~dp0prepare_orca_model.py"
set "RESULT=%ERRORLEVEL%"
echo.
echo Preparation exit code: %RESULT%
echo Existing Hermes and models have not been changed.
pause
exit /b %RESULT%
