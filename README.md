# Hermes + 本地 LLM 完整環境

用 **Hermes Agent** 驅動本地 LLM（目前 Qwen3.8-27B），一台 GPU 主機跑模型，
其他電腦透過區網連進來用。模型可隨時替換。

> **這份 repo 的目的**：新的 GPU 主機或新的用戶端，照著做就能建起來。

---

## 為什麼用 Hermes 而不是 Claude Code

同一個模型、同一道題（SPSC 無鎖環形緩衝區 + 測試 + 編譯驗證）實測：

| | Claude Code | **Hermes** |
|---|---|---|
| 系統 prompt | 32K tokens | **16K** |
| 首檔產出 | 285 秒 | **60 秒** |
| 完成度 | 2/4 檔 | **3/3 檔** |
| 測試結果 | wrap 計算 49 種錯 21 種 | **3098 項 0 失敗** |
| 記憶體屏障 | 0 處 | 18 處 atomic |

差別不在模型，在 **Claude Code 帶了 36 個工具定義**（Cron、Worktree、DesignSync、
TaskCreate…對本地模型完全沒用），把 context 吃掉一半，每輪重算成本翻倍。

---

## 架構

```
┌──────────────────────┐    ZeroTier / 區網    ┌────────────────────┐
│  GPU 主機            │◄──────────────────────│  用戶端電腦         │
│                      │                       │                    │
│  llama-server :8001  │                       │  hermes_bridge     │
│  (Qwen3.8-27B)       │                       │      :1234         │
│  + hermes_bridge     │                       │        ↓           │
│      :1234           │                       │  Hermes 桌面版      │
└──────────────────────┘                       └────────────────────┘
```

**橋接器不可省略** —— Hermes 會檢查模型有沒有宣告 `tools` 能力，
llama-server 只回 `capabilities:["completion"]` 就會被拒用。詳見
[docs/踩過的坑.md](docs/踩過的坑.md)。

---

## 快速開始

### A. GPU 主機

**1. 裝 llama.cpp**（必須 b10435 或更新）

從 [llama.cpp releases](https://github.com/ggml-org/llama.cpp/releases) 抓
`llama-*-bin-win-cuda-12.4-x64.zip`（CUDA 12.x）解壓到
`C:\Users\<你>\.unsloth\llama.cpp-b10435\`

⚠ **舊版有致命 bug**：stream 模式回空值，agent 框架只用 stream，
症狀是「跑很久沒動作」，log 裡是 `api_retry` 配 `error_status: null`。

**2. 下載模型**
```bash
hf download unsloth/Qwen3.8-27B-GGUF Qwen3.8-27B-UD-Q4_K_XL.gguf --local-dir <模型資料夾>
```

**3. 複製 `gpu-host/` 到桌面資料夾**，改 `_ensure_38.ps1` 裡兩個路徑：
```powershell
$llama = 'C:\...\llama.cpp-b10435\llama-server.exe'
$model = 'C:\...\Qwen3.8-27B-UD-Q4_K_XL.gguf'
```
還有 `tensor-split` 要照你的顯卡調（見 [docs/VRAM估算.md](docs/VRAM估算.md)）。

**4. 雙擊 `0-MENU.bat`** — 10 秒倒數選單，不選就用預設。

**5.（選用）開機自動啟動**
```powershell
$ps1 = 'C:\...\_bootmenu.ps1'
$act = New-ScheduledTaskAction -Execute 'powershell.exe' `
       -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$ps1`""
$trg = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERNAME"
$trg.Delay = 'PT60S'
Register-ScheduledTask -TaskName 'LLM-GPU-Server' -Action $act -Trigger $trg -Force
```
⚠ **不能加 `-WindowStyle Hidden`**，否則選單看不到。

### B. 用戶端電腦

1. 裝 [Hermes Agent](https://hermes-agent.ai)
2. 加入同一個 ZeroTier 網路（或同區網）
3. 複製 `client/` 過去
4. 跑橋接器（**視窗不要關**）：
   ```bash
   python hermes_bridge.py <GPU主機IP>
   ```
5. 設定 Hermes → 見 [docs/用戶端設定.md](docs/用戶端設定.md)
   ⚠ 要跑長任務（幾小時以上）務必先看 [docs/長時間任務設定.md](docs/長時間任務設定.md) —— `max_turns` 預設值會讓通宵任務半夜停掉
6. 驗證：`hermes -z "說 ok" --yolo`

---

## 目錄說明

| 目錄 | 內容 |
|---|---|
| `gpu-host/` | GPU 主機的啟動器、選單、橋接器 |
| `client/` | 用戶端要複製過去的檔案 |
| `docs/` | 詳細設定、VRAM 估算、效能調校、推理強度、長時間任務、踩過的坑 |
| `memory/` | Claude 的長期記憶（實測數據、教訓） |

---

## 實測數據

硬體：RTX 3070 8G + RTX 3060 12G × 2 = **32GB**

| ctx | KV 量化 | 結果 |
|---|---|---|
| 256K | q8_0 / q4_0 | ❌ OOM |
| 192K | q4_0 | ⚠️ 可跑但邊界緊 |
| **176K** | **q4_0** | ✅ **採用**，壓測到 131K 穩定 |

- 生成速度 **23.7 tok/s**（MTP 開啟；關掉只有 10.6）
- prompt 處理 **488 tok/s** ← 這才是主要瓶頸，比生成慢 13 倍
- MTP draft acceptance **43%**
- prompt cache 命中率 **95-97%**

### 推理強度（最容易搞錯的一項）

同一句「說 ok」：**`low` 2.5 秒 vs `high` 80 秒**（差 32 倍）。

| 任務類型 | 建議 |
|---|---|
| **多輪除錯**（改→測→再改） | **`low`** ← 本 repo 預設 |
| 一次寫完整個模組 | `high`（品質確實較好，但慢 4 倍） |
| 閒聊查資料 | `off` |

⚠ 三個容易踩的點：
- Hermes GUI 的推理強度選單**其實不生效**，真正決定的是伺服器啟動參數
- GUI 選「最小」或「最高」會 **HTTP 500**（這模型只吃 low/medium/high/xhigh）
- `--reasoning-budget` **沒有硬約束力**，2048 照樣可以繞 18 分鐘

完整實測 → [docs/推理強度設定.md](docs/推理強度設定.md)

---

## 換模型

改 `gpu-host/_ensure_38.ps1`：
```powershell
$model = 'C:\...\新模型.gguf'
'--alias', '新別名',
```

同步改用戶端 `config.yaml`：
```yaml
model:
  default: 新別名
  context_length: <跟伺服器一致>
```

**注意**：
- 不同架構的 KV cache 成本差很多 → 用 [docs/VRAM估算.md](docs/VRAM估算.md) 重算 ctx
- 新模型若 chat template 沒問題，可拿掉 `--chat-template-file`
- MTP 是 Qwen3.8 特有，其他模型要移除 `--spec-type draft-mtp`

---

## 疑難排解

| 症狀 | 原因 | 解法 |
|---|---|---|
| `Connection error` 但網路正常 | 模型沒宣告 tools 能力 | 跑橋接器 |
| 跑很久沒動作、log 有 `api_retry` | llama.cpp 太舊 | 換 b10435+ |
| 400 錯誤 `System message must be at the beginning` | chat template 有 `raise_exception` | 用本 repo 的 template |
| 對話突然爆掉 | `context_length` 兩邊不一致 | 用 `3-CHECK-CTX.bat` 查伺服器實際值 |
| 顯示 Sonnet/Claude 而非本地模型 | 環境變數沒設成功 | 檢查 bat 的變數展開 |
| 通宵任務半夜就停了 | `max_turns` 太小（預設 60-90） | 改成 500，見 [長時間任務設定](docs/長時間任務設定.md) |
| 速度突然掉到 0.3 tok/s | 推理強度設太高 | 改 `low`，見 [推理強度設定](docs/推理強度設定.md) |
| 想看遠端那台在做什麼 | — | GPU 主機跑 `4-WATCH.bat` |

完整清單 → [docs/踩過的坑.md](docs/踩過的坑.md)
效能相關 → [docs/效能調校實測.md](docs/效能調校實測.md)

---

## 授權

個人環境配置，無使用限制。
