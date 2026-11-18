# Qwen3.8-27B + MTP 本地環境

雙擊 `ClaudeCode-Qwen3.8.bat` 就會自動啟動 llama-server 並開 Claude Code。

## 🚀 開機選單（登入 60 秒後自動跳出）

```
   [1] 韌體設計 (預設)   think=high, temp 0.3, 140K  ← STM32/RTOS
   [2] 一般寫 code       think=low,  temp 0.7, 140K
   [3] 最快速模式         think=off,  temp 0.7, 140K
   [4] 長 context        think=low,  temp 0.7, 160K  ← VRAM 較緊
   [5] 不啟動
```

**10 秒倒數，不按就自動選 [1] 韌體設計**。載入完視窗 8 秒後自關，伺服器繼續背景跑。

| 需求 | 做法 |
|---|---|
| 隨時叫出選單 | 雙擊 `0-MENU.bat` |
| 直接開特定模式 | `1` / `1b` / `1c` / `1d` 的 bat |
| 停止伺服器 | `2-STOP-GPU-Server.bat` |
| 改選單內容 | 編輯 `_bootmenu.ps1` 開頭的 `$menu` 陣列 |
| 取消開機啟動 | `schtasks /Delete /TN "Qwen38-GPU-Server" /F` |

⚠ 排程**不能**設 `-WindowStyle Hidden`，否則選單看不到。

## 檔案
| 檔案 | 用途 |
|---|---|
| `ClaudeCode-Qwen3.8.bat` | 主啟動器（雙擊這個） |
| `_ensure_38.ps1` | 啟動 llama-server，已在跑就跳過 |
| `qwen38_chat_template.jinja` | **修過的** chat template（必要，見下） |
| `docs/CLAUDE.md` | 通用規則（複製到工作資料夾用） |
| `skills/` | 專門規則，已裝到 `~/.claude/skills/` |

模型檔：`C:\Users\pjunm\.cache\huggingface\hub\Qwen3.8-27B-UD-Q4_K_XL.gguf`（17.9GB）

## 實測設定（2026-08-15）

硬體：RTX 3070 8GB + RTX 3060 12GB ×2 = 32GB

| ctx | KV 量化 | 結果 |
|---|---|---|
| 256K | q8_0 | ❌ compute buffer 爆 CUDA0 |
| 256K | q4_0 | ❌ 爆 CUDA1/CUDA2 |
| 192K | q4_0 | ⚠ 可跑但 CUDA2 只剩 276MB，太緊 |
| **160K** | **q4_0** | ✅ **採用**，VRAM 24.6/32GB |

效能：生成 13-15 t/s、prompt 處理 ~600 t/s、MTP draft acceptance **43%**

## MTP 是什麼
Multi-Token Prediction：模型一次預測多個 token，主模型平行驗證，decode 速度約 1.5-2×。
權重內建在 GGUF，不需另外的 draft model，用 `--spec-type draft-mtp` 開啟。
VRAM 成本跟 ctx 成正比（160K 時約 1.5GB）。

## 🔴 llama.cpp 版本：必須用 b10435 或更新（最重要）

**舊的 6/2 build（`~/.unsloth/llama.cpp/`）stream 模式對這個模型是壞的**：
```
非 stream 請求 → 正常回應
stream 請求   → 完全空的
```
Claude Code 只用 stream，所以每個請求都收到空回應 → 觸發重試 → 重試 10 次 →
每次都重跑整個 prompt。症狀是「跑很久都沒動作」，log 裡是：
```json
{"subtype":"api_retry","error_status":null,"error":"unknown"}
```
task 編號會暴衝（實測 1560 → 2471，900 多次請求全是白工），
而 `/slots` 看到的 `n_prompt_tokens_processed` 停住不動。

**解法**：用 `~/.unsloth/llama.cpp-b10435/llama-server.exe`（已下載，`_ensure_38.ps1` 指這個）。
新版還多了原生 `/v1/messages`（Anthropic Messages API）端點。

⚠ 不要改回舊路徑。要升級的話去 https://github.com/ggml-org/llama.cpp/releases 抓
`llama-*-bin-win-cuda-12.4-x64.zip`（你的 CUDA 是 12.6，用 12.4 那版）。

## ⚠️ chat template 必須用修過的版本
官方 template 第 110 行有：
```jinja
{{- raise_exception('System message must be at the beginning.') }}
```
Claude Code 會送多個 system/developer message → **400 錯誤整個不能用**。
`qwen38_chat_template.jinja` 已把那行改成當 user message 輸出。
啟動時用 `--chat-template-file` 帶入，不要拿掉。


## ⚠️ 已知限制：prompt cache 對這架構失效

llama.cpp log 會出現：
```
forcing full prompt re-processing due to lack of cache data
(likely due to SWA or hybrid/recurrent memory)
```

因為 64 層裡 48 層是 linear/recurrent attention，llama.cpp 目前**無法快取這類層的狀態**，
所以多輪對話每一輪都要把整個 prompt 重算一次。

實際影響：
- 單輪問答（直接打 API）→ 沒問題，很快
- **Claude Code 這種多輪 agent 迴圈 → 每輪都要重算**。62K prompt × 600 t/s ≈ 100 秒/輪，
  一個要 5-6 輪的任務（讀檔→思考→寫檔→驗證）就要 10 分鐘以上

**怎麼確認**：跑的時候查 slot 狀態，`n_prompt_tokens_cache` 會是 0：
```bash
curl -s http://127.0.0.1:8001/slots
```
實測 44785 tokens 的 prompt、cache 命中 0、每輪重算約 75 秒。

緩解方式（bat 已套用 1、2）：
1. **把 prompt 壓小** — 拆 Skills，常駐從 9.6K 降到 1.7K
2. **`--strict-mcp-config`** — 只載 `mcp-local.json`（留 ddg-search 聯網），
   忽略全域的 headroom(failed) / serena(pending)。工具從 36 降到 31
3. **複雜任務一次講完整**，少讓它來回。多一輪就多 75 秒
4. 等 llama.cpp 支援 hybrid 架構的 cache（追 PR #13194 討論串）

⚠ **Skill 的取捨**：載入 skill 會讓「那一輪之後」的 prompt 變大（實測載入 2 個 skill 後
從 6K 漲到 43K），而且因為 cache 失效，每輪都要重算。所以 skill 內容要精簡，
description 也要準（載錯 skill 等於白白多背幾萬 token）。

⚠ 剩下 30 個是 Claude Code 內建工具（Cron / Worktree / Task 等對本地模型沒用），
目前無法個別關閉，這是 prompt 的主要來源。

## 架構特點
GGUF arch 是 `qwen35`（不是 qwen38）。64 層裡只有 16 層是 full attention，
其餘 48 層是 linear attention → **KV cache 只按 16 層算**，長 context 比一般 27B 便宜很多。

KV 每 token：q4_0 = 18KB、q8_0 = 34KB。

## 要推到 1M ctx 需要多少 VRAM
| 設定 | 需求 |
|---|---|
| q4_0 + MTP | ~48GB |
| q4_0 不開 MTP | ~38GB |
| q8_0 + MTP | ~64GB |

最划算：把 3070 8GB 換成 24GB 卡（3090/4090）→ 24+12+12 = 48GB。
3070 一直是瓶頸，兩次 OOM 都爆在它身上。
⚠ 模型原生訓練 262K，超過要開 YaRN 外推，品質會掉。

## Skills 機制（省 context）
規則拆成獨立檔案，啟動時只載入各 skill 的一行 description（~60 tokens），
模型判斷需要才把全文拉進來。

| Skill | 何時載入 | 全文 |
|---|---|---|
| game-assets | 做遊戲、要音效/sprite | ~1000 tokens |
| pptx-deck | 做 PPT/簡報/週報 | ~900 tokens |
| web-design | 做網頁/UI/表單 | ~465 tokens |

常駐 context：CLAUDE.md 1546 + 三個 description 189 = **1735 tokens**
（原本全部塞 CLAUDE.md 是 9657 tokens，省 83%）

### Skill 存放位置（跟雲端 Claude 完全隔離）
本地模型的 skill 放在 **`Qwen3.8-27B/local-plugin/skills/`**，
以 Claude Code **plugin** 形式載入（bat 裡的 `--plugin-dir` 參數）。

**為什麼這樣設計**：`~/.claude/skills` 是全域的，雲端 Claude 也會讀到。
用 plugin 隔離後：
- 這些 skill 只有雙擊 `ClaudeCode-Qwen3.8.bat` 時才存在
- 平常用雲端 Claude Code **完全看不到**，兩邊互不干擾
- 載入後名稱是 `qwen38-local:game-assets` 這種形式

**加新 skill**：在 `local-plugin/skills/<名字>/SKILL.md` 建檔，frontmatter 要有
`name` 跟 `description`。**description 決定命中率**，要寫使用者會講的詞。

CLAUDE.md 已加規則叫模型「學到可重用的知識就自己存成 skill」，所以它會自己長。
偶爾檢查它寫的 description 夠不夠具體（太籠統會永遠叫不出來）。

⚠ 手動測試 plugin 有沒有載入：
```bash
claude --plugin-dir "C:\Users\pjunm\OneDrive\Desktop\Qwen3.8-27B\local-plugin" -p "有哪些 skill"
```


## 🌐 給別台電腦用（ZeroTier 遠端 GPU）

架構：**這台只跑模型，別台裝 Claude Code 自己工作**，檔案生在別台硬碟。

### 這台（GPU 機）
- 本機 ZeroTier IP：**10.35.219.64**
- llama-server 監聽 `0.0.0.0:8001`
- **已設開機自動啟動**：排程工作 `Qwen38-GPU-Server`（登入後延遲 90 秒觸發）
  - 查狀態：`schtasks /Query /TN "Qwen38-GPU-Server"`
  - 手動觸發：`schtasks /Run /TN "Qwen38-GPU-Server"`
  - 取消自動啟動：`schtasks /Delete /TN "Qwen38-GPU-Server" /F`
- 手動控制：`1-START-GPU-Server.bat` / `2-STOP-GPU-Server.bat`

### 別台
1. 裝 Node.js + `npm i -g @anthropic-ai/claude-code`
   - 若 PowerShell 擋 npm.ps1（SecurityError）→ 用 `cmd /c "npm i -g @anthropic-ai/claude-code"`
     或 `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`
2. 加入同一個 ZeroTier 網路
3. 複製 `for-other-pc/` **整包**過去（約 55MB，含素材庫），雙擊 `ClaudeCode-RemoteGPU.bat`

**整包內容**（缺一不可）：
```
ClaudeCode-RemoteGPU.bat   啟動器
local-plugin/skills/       3 個 skill
shared_assets/             54MB Kenney 素材庫（5001 檔）← 做遊戲/音效必要
CLAUDE.md                  規則檔（bat 會自動複製到工作目錄）
mcp-local.json             精簡 MCP
settings-local.json        低 effort + 160K context 宣告
work/                      預設工作目錄（bat 自動建）
```

**bat 會自動做的事**：
- 檢查 GPU 伺服器連得到才啟動
- 工作目錄沒有 `CLAUDE.md` → 從整包複製一份
- 工作目錄沒有 `shared_assets` → 建 Junction 指向整包（不佔額外空間）
- **拖資料夾到 bat 上** = 在那個資料夾工作（不拖就用 `work/`）

⚠ skill 裡的素材路徑已改成相對的 `shared_assets/`，別台使用者名稱不是 pjunm 也沒問題。

IP 變了就改 bat 第 8 行的 `SERVER=`。

### ⚠️ 防火牆（還沒做，需要管理員）
目前 llama-server 的規則是 **Remote: Any**（家用 WiFi 也連得到），而 llama-server 沒有驗證。
建議收緊成只允許 ZeroTier 網段——用**管理員** PowerShell：
```powershell
New-NetFirewallRule -DisplayName "Qwen3.8 llama-server (ZeroTier only)" -Direction Inbound -Protocol TCP -LocalPort 8001 -RemoteAddress 10.35.219.0/24 -Action Allow -Profile Any
Get-NetFirewallRule -DisplayName "llama-server.exe" | Disable-NetFirewallRule
```

### CLI 版本
`--plugin-dir` 從 2.1.114 就有，2.1.168+ 都能用。
新版 CLI 會提示「qwen38_mtp is not a model this version recognizes」——那不是錯誤，
已在 `settings-local.json` 設 `CLAUDE_CODE_MAX_CONTEXT_TOKENS=163840` 告訴它真實 context。

## 換模型
改 `_ensure_38.ps1` 裡的 `$model` 路徑跟 `--alias`，bat 的 `ANTHROPIC_MODEL` 要跟 alias 一致。
