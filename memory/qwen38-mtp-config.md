---
name: qwen38-mtp-config
description: Qwen3.8-27B + MTP 實測可用設定與 VRAM 上限（32GB 三卡）
metadata:
  type: project
---

Qwen3.8-27B-UD-Q4_K_XL（17.92GB）在 8+12+12=32GB 三卡上的實測結果（2026-08-15 實測）。

**架構關鍵**：GGUF arch = `qwen35`（不是 qwen38），64 層裡只有 16 層是 full attention，
其餘 48 層是 linear attention → KV cache 只按 16 層算，比一般 27B 便宜很多。
KV 每 token：q4_0 = 18KB、q8_0 = 34KB。

**實測 ctx 上限**（--parallel 1、-sm layer、tensor-split 6,12,13）：
- 256K + q8_0 → 失敗（compute buffer 爆 CUDA0）
- 256K + q4_0 → 失敗（爆 CUDA1/2）
- 192K + q4_0 → 可載入可推理，但 CUDA2 只剩 276MB，太緊
- **160K (163840) + q4_0 → 正式採用**，VRAM 24.6/32GB，穩定

**MTP**：`--spec-type draft-mtp`（權重內建在 GGUF，不需 draft model）。
VRAM 成本跟 ctx 成正比：160K 時 1482 MiB、256K 時 3476 MiB（比官方說的 1GB 多很多）。
實測 draft acceptance **43%**、生成 13-15 t/s、prompt 670 t/s。

**最大的雷 — llama.cpp 版本**：舊的 6/2 build（`~/.unsloth/llama.cpp/`）**stream 模式回空值**，
非 stream 正常。Claude Code 只用 stream → 每次請求空回應 → api_retry 重試 10 次 →
每次重跑整個 prompt。症狀是「跑 20 分鐘沒動作」，log 是
`{"subtype":"api_retry","error_status":null,"error":"unknown"}`，
`/slots` 的 `n_prompt_tokens_processed` 停住但 task 編號暴衝。
**必須用 `~/.unsloth/llama.cpp-b10435/llama-server.exe`**（8/14 build，新版還有原生
`/v1/messages` Anthropic 端點）。換版後寫檔任務 3分17秒完成。

**必踩的雷 — chat template**：官方 template 在第 110 行有
`raise_exception('System message must be at the beginning.')`，Claude Code 會送多個
system/developer message → 400 錯誤整個不能用。解法是把那行換成當 user message 輸出，
存成 `hermes/qwen38_chat_template.jinja`，啟動帶 `--chat-template-file`。

1M ctx 需要 ~48GB（q4_0+MTP）→ 要把 3070 8GB 換成 24GB 卡才夠（3070 一直是 OOM 瓶頸）。

相關：[[llama-cpp-tuning-rules]] [[llm-vram-budget]]

**★ prompt cache 失效有解（2026-08-15 實測成功）**：llama.cpp 已知 bug（issue #20225、#19794），
根因是 checkpoint 搜尋用 SWA 的 pos_min 判斷、但 recurrent 模型 pos_min 永遠等於全長，
加上 `--checkpoint-min-step` 預設 8192 太大導致根本建不出 checkpoint。
**解法**（b10435 已有這些旗標）：
```
--swa-full  --checkpoint-min-step 0  --ctx-checkpoints 32  --cache-ram -1
```
實測：cache 從 0 → **41714/42145 命中**，第三輪耗時 100 秒 → **27.8 秒**。
log 裡 `forcing full prompt re-processing` 那行也消失。已寫進 `_ensure_38.ps1`。

**啟動器 profile**（`_ensure_38.ps1` 收 -Think 跟 -Mode 參數）：
- `1-START-GPU-Server.bat` think=low（預設，一般寫 code）
- `1b-...-THINK.bat` think=high（深度推理）
- `1c-...-FAST.bat` think=off（閒聊最快）
- `1d-...-FIRMWARE.bat` think=high + mode=fw + ctx=140K（STM32/RTOS 用，temp 0.3 讓暫存器名和數字不飄）

⚠ `--reasoning` 合法值只有 `on|off|auto`，寫 `none` 會導致啟動失敗（VRAM 停在 0）。
⚠ `--reasoning auto` 沒有上限會讓模型陷入思考迴圈（proc 停住但 prompt 一直漲），必須設 budget。

**★ think 設定實測（2026-08-18 對照測試）**：出了一題跨檔韌體任務
（H7S3 的 SPSC ringbuf + UART DMA + 測試檔，共 4 檔），本機 vs 雲端各做一次：

| | 完成度 | 首檔產出 |
|---|---|---|
| 雲端 Opus | 4/4 | 226 秒全部完成 |
| 本機 think=low | 2/4 | **1090 秒** |
| 本機 think=off | 2/4 | **285 秒** |

**關掉 thinking 讓首檔產出快 3.8 倍，品質沒變差反而更好**（think=off 版把
`cap>=1` 修正成 `cap>=2`）。`--reasoning-budget` 對這模型**沒有實際約束力**，
budget 2048 照樣繞 18 分鐘，唯一可靠的是 `--reasoning off`。

**發現模型的系統性弱點**：環形緩衝區的 wrap 計算兩版都寫錯（各錯 21/49 種索引組合）
- think=low 版：`(h-t) % cap` → uint32 下溢
- think=off 版：`cap - h + t` → h/t 顛倒，算出的值比 cap 大會越界
兩版都**沒有記憶體屏障**（SPSC 無鎖在 M7 上需要 `__DMB()`）。
→ **本機寫的邊界計算程式碼一定要自己複查**。

**think 該用在哪**：單輪深度問答（「為什麼」「該選哪個」）值得開；
多輪寫程式反效果。更實用的替代是平常 think=off，難題時在 prompt 裡寫
「先分析可能原因再給結論」，不用重啟伺服器。

**開機選單預設已改成 think=off + fw 模式**（temp 0.3 + 140K ctx）。

**★★ Hermes Agent 完勝 Claude Code（2026-08-18 實測，推翻早上的結論）**
同一模型、同一題（SPSC ringbuf + 測試 + 編譯）：

| | Claude Code | Hermes |
|---|---|---|
| prompt | 32K | **16K** |
| 首檔產出 | 285 秒 | **60 秒** |
| 完成度 | 2/4 檔 | **3/3 檔** |
| 測試品質 | wrap 錯 21/49 種 | **55 項只錯 1 項** |

**早上「本地模型不能代工」的結論是錯的** —— 不是模型不行，是 Claude Code 的 36 個
工具定義（Cron/Worktree/DesignSync/TaskCreate 等對本地模型沒用的）把 context 吃光。
Hermes 工具集精簡，同一個模型就能完成任務，還自己寫了含多執行緒壓力測試的 11KB 測試檔。

**Hermes 接本地 llama-server 的兩個坑**（卡很久才解）：
1. **不要用 ollama provider** — 那走 Ollama 雲端（`hermes doctor` 會顯示 `ollama-cloud`）。
   要用 **LM Studio provider**，它打 `127.0.0.1:1234`。
2. **Hermes 會檢查模型有沒有宣告 tools 能力**，llama-server 只回
   `capabilities:["completion"]` → 拒用，但錯誤訊息是誤導性的
   `API call failed after 3 retries: Connection error.`
   → 解法：`hermes_bridge.py` 轉發 1234→8001 並補上
   `capabilities:["completion","tools","chat"]`。已加進開機選單自動啟動
   （用 `_bridge.bat` 包一層，直接 Start-Process python 在排程環境下起不來）。

Hermes 還內建 Telegram/Discord/Slack/WhatsApp/Email/SMS 閘道跟 cron 排程，
遠端遙控能力比 Claude Code 完整得多。
