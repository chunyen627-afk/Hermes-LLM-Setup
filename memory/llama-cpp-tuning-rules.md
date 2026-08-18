---
name: llama-cpp-tuning-rules
description: "Non-obvious llama.cpp server tuning rules learned from user's tri-GPU rig. Counterintuitive defaults to watch for."
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 2cc11d76-828b-4b47-aeda-881427fe9735
---

Tuning rules for llama.cpp `llama-server.exe` on multi-GPU NVIDIA setups, learned empirically:

1. **`-sm layer` can crush `-sm row` on asymmetric GPUs / slow PCIe.**
   - On user's 3070 + 3060 + 3060 over PCIe Gen3-ish: row split = 13 tok/s, layer split = 72 tok/s (5.5× faster).
   - **Why:** row split syncs every layer across GPUs every token; on asymmetric VRAM/bandwidth, the slow card stalls all the others. Layer split runs each layer fully on one GPU, only crossing PCIe at layer boundaries.
   - **How to apply:** when user picks `-sm row` for a MoE model on multi-GPU, ask first whether GPUs are symmetric and PCIe is Gen4 x16 per slot. If not, benchmark layer split before committing.

2. **`-fa` requires a value in recent llama.cpp builds.**
   - Old `-fa` (bare flag) now errors with "unknown value for --flash-attn: '<next-arg>'".
   - **How to apply:** always write `-fa on` (or `auto` / `off`).

3. **KV cache q8_0 quant (`-ctk q8_0 -ctv q8_0`) is near-free.**
   - Halves KV memory, quality drop is undetectable in chat use.
   - **How to apply:** enable by default when context > 32K or VRAM is tight. Combine with `-fa on`.

4. **Hermes Agent needs context ≥ 64K** and `compression.enabled: false` in config — otherwise startup fails or the compression helper model errors. **How to apply:** when configuring Hermes against any local backend, bake these into the model config block.

5. **Hermes installed from Claude Desktop's terminal goes into UWP sandbox** (`AppData\Local\Packages\Claude_*\LocalCache\Local\hermes`), and BAT files launched from real cmd can't find it. **How to apply:** any "hermes.exe not found" / exit code 3 symptom on Windows → first check if it got installed into a sandboxed AppData; fix is reinstall from a real PowerShell window.

6. **llama.cpp server natively serves Anthropic `/v1/messages`** — no proxy / claude-code-router / litellm needed. Point Claude Code CLI at it with `ANTHROPIC_BASE_URL=http://127.0.0.1:1234` + `ANTHROPIC_AUTH_TOKEN=...` + `ANTHROPIC_MODEL=...`. The `--jinja` flag is mandatory on llama-server or tool calls leak `<unused24>` tokens. **How to apply:** when user wants to run Claude Code against any local OpenAI-compatible server, check first if it speaks Anthropic Messages API natively (llama.cpp ≥ recent builds, LM Studio ≥ 0.4.1, Ollama ≥ 0.14.0 do) — proxies are obsolete for these.

7. **`CLAUDE_CODE_ATTRIBUTION_HEADER=0` must live in `~/.claude/settings.json` env block**, not in shell env vars (Claude Code ignores the env var when routing through `ANTHROPIC_BASE_URL`). Without it the attribution header invalidates KV cache on every request → ~90% slower prefill on local models.

Related: [[local-llm-hermes-rig]]
