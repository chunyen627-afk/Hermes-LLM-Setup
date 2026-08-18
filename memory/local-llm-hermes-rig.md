---
name: local-llm-hermes-rig
description: "User runs Qwen3.6 35B-A3B MoE locally via llama.cpp + Hermes Agent. Tri-GPU rig with specific optimization quirks (sm layer, not row)."
metadata: 
  node_type: memory
  type: project
  originSessionId: 2cc11d76-828b-4b47-aeda-881427fe9735
---

User runs local LLM stack with these decisions baked in:

- **Stack:** llama.cpp `llama-server.exe` (CUDA 12.4 build, unsloth's prebuild at `C:\Users\pjunm\.unsloth\llama.cpp\build\bin\Release\`) serves ALL frontends. Single port 1234, model swapped on-demand via a helper script.
- **Two models, picked by task:**
  - `HauhauCS/Qwen3.6-35B-A3B-Uncensored-HauhauCS-Aggressive` Q4_K_M (19.7 GB) + mmproj vision → for Hermes / Aider / LM Studio. 96K ctx, `-ts 5,12,12`. ~72 tok/s.
  - `unsloth/Qwen3-Coder-30B-A3B-Instruct-GGUF` UD-Q4_K_XL (17.7 GB), text-only → for Claude Code. 192K ctx, `-ts 6,11,11`. ~45 tok/s.
- **Four frontends, each in `C:\Users\pjunm\OneDrive\Desktop\hermes\` as BATs:**
  - **Hermes** — chat + vision, friendly to small models. Backend: llama.cpp :1234.
  - **Claude Code** — agent loop + auto file-write. Backend: **Unsloth Studio :8888** (NOT llama.cpp — llama.cpp lacks the Qwen3 Coder XML tool-call parser, so the model emits `<function=Write>...` text and Claude Code never sees a `tool_use` block). Pinned to **v2.1.153** because v2.1.154+ injects `system` role into `messages[]` which Unsloth's strict schema rejects with HTTP 422. Studio launched with `--disable-tools` so the model uses Claude Code's `Write`/`Edit`/`Bash` instead of Studio's built-in `render_html`/etc.
  - **Aider** — `/add` files + diff-based edits, most tolerant of small models. Backend: llama.cpp :1234. BAT shim → `Aider-Launch.ps1` (because cmd + parens cwd + prompt_toolkit = triple landmine)
  - **LM Studio GUI** — for plain chat; `切到LM-釋放VRAM.bat` kills llama-server first to free VRAM
- **Rig:** RTX 3070 8 GB + RTX 3060 12 GB × 2 = 32 GB VRAM, i9-10900, 64 GB RAM, PCIe Gen3-ish (asymmetric).
- **Central helper:** `_ensure_server.ps1` auto-detects (has mmproj → vision model; no mmproj → Coder), kills/restarts server only if alias or ctx differs. All BATs call it.
- **Sandbox trap (hit twice):** `uv tool install` and Hermes installer both pollute `Packages\Claude_*` virtual AppData when run from Claude Desktop. Always reinstall from a real Windows PowerShell window. Test from user's double-click env with `Test-Path` to confirm.

**Setup guide:** `本地LLM＋Hermes架設指南.md` in the hermes folder — full rebuild checklist for all 4 tools.

**How to apply:** when user asks about local LLM on this machine, default to this stack. Coding agent loop → Coder + Claude Code; coding diff → Aider + uncensored; chat/vision → Hermes + uncensored or LM Studio. Don't suggest claude-code-router/litellm (llama.cpp serves Anthropic Messages API natively). Don't suggest vLLM/ExLlamaV2 (Windows pain).

Related: [[llama-cpp-tuning-rules]]
