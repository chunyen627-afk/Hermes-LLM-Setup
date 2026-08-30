@echo off
REM Send a new task to the 27B. Write the prompt into task.txt first.
REM Uses --query-file so Chinese, newlines and quotes survive the shell.
REM ASCII comments on purpose: cmd reads .bat in the OEM codepage.
chcp 65001 >nul
title New task for 27B
cd /d "%~dp0"

set PY=C:\Users\pjunm\AppData\Local\Programs\Python\Python311\python.exe
if not exist "%PY%" set PY=python
set H=%LOCALAPPDATA%\hermes\hermes-agent\venv\Scripts\hermes.exe
set TASK=%~dp0task.txt

if not exist "%TASK%" (
    echo task.txt not found.
    echo Create it here, write the prompt, then run this again:
    echo   %TASK%
    echo.
    pause
    exit /b 1
)

echo Task:
echo ----------------------------------------
type "%TASK%"
echo ----------------------------------------
echo.
set /p GO=Send this task? (Y/N)
if /i not "%GO%"=="Y" (
    echo Cancelled.
    pause
    exit /b 0
)

echo.
echo Running with --max-turns 500. You can minimize this window, do not close it.
echo.
"%H%" chat --query-file "%TASK%" --max-turns 500

echo.
echo Task ended.
pause
