@echo off
chcp 65001 >nul
setlocal
set "PATH=C:\Users\pjunm\AppData\Local\Programs\Python\Python311;C:\Users\pjunm\AppData\Local\Programs\Python\Python311\Scripts;%PATH%"
title Claude Code - Qwen3.8-27B (MTP, 160K ctx)
cd /d "%~dp0"

REM === Start Qwen3.8-27B llama-server on :8001 ===
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0_ensure_38.ps1"
if errorlevel 1 ( pause & exit /b 1 )

set "ANTHROPIC_BASE_URL=http://127.0.0.1:8001"
set "ANTHROPIC_AUTH_TOKEN=lmstudio"
set "ANTHROPIC_MODEL=qwen38_mtp"
set "ANTHROPIC_SMALL_FAST_MODEL=qwen38_mtp"

REM --plugin-dir      : 本地專屬 skills（雲端 Claude 看不到）
REM --strict-mcp-config: 只載 mcp-local.json，忽略全域的 headroom/serena（省 prompt）
echo Launching Claude Code (Qwen3.8-27B MTP, :8001)...
claude --dangerously-skip-permissions ^
  --plugin-dir "%~dp0local-plugin" ^
  --mcp-config "%~dp0mcp-local.json" ^
  --strict-mcp-config

pause
endlocal