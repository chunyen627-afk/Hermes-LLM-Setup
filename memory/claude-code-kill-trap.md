---
name: claude-code-kill-trap
description: 絕對禁止 taskkill /F /IM claude.exe — 會殺到使用者桌面板 Claude / Desktop Claude session
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 86205309-645c-4dc6-8b4a-9d930c8e333c
---

**絕對禁止用 `taskkill /F /IM claude.exe` 或 `taskkill /F /IM node.exe`**。

**Why**：使用者有桌面板 Claude（Claude Desktop App）+ Claude Code CLI 同時跑，process name 都叫 claude.exe / node.exe。`/IM` 殺所有同名 process → **連這個對話本身的 Claude 都會被殺**，使用者血淚教訓。

**How to apply**：
- 永遠用 **PID 精準殺**：`subprocess.Popen` 後存 `proc.pid`，要砍時 `taskkill /F /T /PID <pid>`
- `/T` = 含子孫 process（claude.cmd spawn node.exe 一起清掉）
- Flask `_run_claude_msg` 用 `CHAT_PROCS[sid] = proc` 追蹤每個對話的 PID
- 心跳 timeout / 使用者按⏹中斷 → 只殺該 sid 的 proc.pid
- 如果一定要殺全部 → 改殺 `llama-server.exe`（不是 claude.exe）來釋 VRAM

相關：[[mobile-remote-gui]]、[[ps1-encoding-traps]]
