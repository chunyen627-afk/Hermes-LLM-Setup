@echo off
chcp 65001 >nul
title Claude Code - Qwen3.8-27B (remote GPU)

set "SERVER=10.35.219.64"

echo.
echo Checking %SERVER%:8001 ...
curl -s -m 10 -o nul "http://%SERVER%:8001/v1/models"
if errorlevel 1 goto NOSERVER
echo   [OK] server reachable

set "ANTHROPIC_BASE_URL=http://%SERVER%:8001"
set "ANTHROPIC_AUTH_TOKEN=lmstudio"
set "ANTHROPIC_MODEL=qwen38_mtp"
set "ANTHROPIC_SMALL_FAST_MODEL=qwen38_mtp"

if not exist "%~dp0work" mkdir "%~dp0work"
if "%~1"=="" (cd /d "%~dp0work") else (cd /d "%~1")

if not exist "CLAUDE.md" copy /Y "%~dp0CLAUDE.md" "CLAUDE.md" >nul 2>&1
if not exist "shared_assets" mklink /J "shared_assets" "%~dp0shared_assets" >nul 2>&1

echo.
echo   Remote GPU : %SERVER%:8001
echo   Working dir: %CD%
echo   (tip: drag a folder onto this .bat to work there)
echo.

claude --dangerously-skip-permissions --plugin-dir "%~dp0local-plugin" --mcp-config "%~dp0mcp-local.json" --strict-mcp-config --settings "%~dp0settings-local.json"
goto END

:NOSERVER
echo.
echo   [ERROR] Cannot reach %SERVER%:8001
echo     1. GPU PC: run 0-MENU.bat
echo     2. Both PCs on the same ZeroTier network?
echo     3. Try: ping %SERVER%
echo.

:END
pause
