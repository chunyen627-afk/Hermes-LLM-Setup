---
name: llm-vram-budget
description: 32GB VRAM 4 模型的 ctx 預算配置，避免 llama-server 啟動時 OOM segfault
metadata: 
  node_type: memory
  type: project
  originSessionId: 86205309-645c-4dc6-8b4a-9d930c8e333c
---

使用者機器：RTX 3070 8GB + 2× RTX 3060 12GB = 32GB 總 VRAM（tensor-split 分配）

**4 個模型的安全 ctx + cache 配置**：

| 模型 | 權重 | ctx | KV cache | tensor-split | 估 VRAM |
|---|---|---|---|---|---|
| Qwen3.6-27B Dense | 17 GB | **256K** | q4_0 | 8,12,12 | ~25 GB ✅ |
| Qwen3-32B Dense | 19 GB | **64K** | q4_0 | 5,11,11 | ~25 GB ✅ |
| Qwen3-Coder-30B MoE | 17 GB | **96K** | q4_0 | 5,11,11 | ~22 GB ✅ |
| Qwen3.6-35B + mmproj | 20+1 GB | **96K** | q4_0 | 5,11,11 | ~28 GB ✅ |

**Why**：
- 27B 權重小，可以撐 256K + q4_0 cache 還在 25GB
- 32B Dense 權重大 5GB、每 1K ctx 的 cache 大 33% → 128K + q8_0 直接撞 32GB 上限 → server load 時 silent segfault（不 crash log、直接 process 不見）
- 35B + mmproj 同樣道理，加上 mmproj 又佔 1GB → 必須降 ctx
- Coder-30B 雖然是 MoE 但 KV cache 算法仍按完整 layer → 96K 為安全上限

**How to apply**：
- **永遠用 q4_0 cache**（不用 q8_0）→ 同 ctx 省一半 VRAM
- **永遠不要硬塞 256K 給 32B / Coder / 35B** → 會 segfault
- 設定散在 4 個 `_ensure_*.ps1`、改的時候 4 個一起改
- 切換模型時等 `Kill llama-server → port 8001 真正關閉` 才能起新的（race condition 會 OOM）

**特殊**：35B Uncensored 是 thinking 模型，看圖時 max_tokens 必須 ≥ 4096（< 1024 會被 `<think>` 吃光，content 變空白）

相關：[[mobile-remote-gui]]、[[ps1-encoding-traps]]
