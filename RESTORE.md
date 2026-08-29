# 重灌後的完整還原

這份是給「拿到這個倉庫、要把環境重建起來的人」看的。
照順序做，每一步都有驗證方式。

需要的時間：約 2-3 小時，其中大半在等下載。

---

## 這套環境在做什麼

一台有三張顯示卡的 Windows 電腦，跑一個本地大型語言模型（Qwen3.8-27B），
讓 Hermes（一個 AI agent 桌面程式）用它來做事 —— 寫程式、燒韌體、驗證結果。

```
Hermes 桌面版  ──►  橋接器 :1234  ──►  llama-server :8001  ──►  3 張 GPU
（下指令的人）      （相容層）          （模型本體）

手機/家人  ──►  Flask GUI :5000  ──►  同一個橋接器
```

**為什麼要橋接器**：Hermes 以為自己在跟 LM Studio 講話，但實際是 llama.cpp。
橋接器補上 Hermes 需要、llama.cpp 沒有的東西（capabilities 欄位、串流 usage 統計）。

---

## 步驟 0：先確認硬體

| 項目 | 這套環境的假設 |
|---|---|
| GPU | RTX 3070 8GB + RTX 3060 12GB × 2（共 32GB）|
| RAM | 32GB |
| 磁碟 | 至少 400GB 可用 |
| OS | Windows 11 |

**顯卡不同的話**，`gpu-host/_ensure_38.ps1` 裡的 `--tensor-split 8,12,10`
一定要改成你的配置（數字是各卡分到的層數比例，跟 VRAM 成正比）。
改法見 `docs/VRAM估算.md`。

---

## 步驟 1：llama.cpp

下載 Windows CUDA 版：https://github.com/ggml-org/llama.cpp/releases

解壓到 `C:\Users\<你>\.unsloth\llama.cpp-<版本>\`
（路徑可以自己選，但要記得，步驟 3 會用到）

**驗證**：
```powershell
& "C:\...\llama.cpp-xxx\llama-server.exe" --version
```

---

## 步驟 2：模型檔

從 HuggingFace 下載 GGUF 格式的模型，放進
`C:\Users\<你>\.cache\huggingface\hub\`

這套環境用的是 `Qwen3.8-27B-Uncensored-HauhauCS-Aggressive-Q4_K_P.gguf`（約 16GB）。
換別的模型也可以，但 `_ensure_38.ps1` 裡的檔名要跟著改。

⚠ **視覺編碼器要另外下載**：`mmproj-Qwen3.8-27B-Uncensored-HauhauCS-Aggressive-BF16.gguf`
（約 0.87GB，跟主模型放同一個目錄）。沒有它模型就看不到圖 —— 見「步驟 3.5：視覺」。

---

## 步驟 3：GPU 主機腳本

把 `gpu-host/` 整個複製到桌面（或任何固定位置）。

**必改兩個路徑**（`_ensure_38.ps1` 開頭）：
```powershell
$llama = 'C:\...\llama.cpp-xxx\llama-server.exe'    # 步驟 1 的位置
$model = 'C:\...\hub\你的模型.gguf'                  # 步驟 2 的位置
```

**驗證**：雙擊 `1-START-GPU-Server.bat`，等它印出
`[OK] Ready (NN s) - qwen38_mtp`，然後：
```powershell
Invoke-RestMethod http://127.0.0.1:8001/v1/models
```
應該回傳模型清單。

---

## 步驟 3.5：視覺（讓模型自己看圖）

這個模型**原生就是多模態**，chat template 裡早就有 `<|vision_start|>`。
但沒掛 mmproj 的話 `/props` 會回報 `vision: false`，看圖只能外送雲端。

**不需要換非 GGUF 版本** —— mmproj 就是原生 ViT + projector 本體，
而且是 BF16 未量化，精度比 bnb-4bit 那類整包量化的還高。

需要**三個條件同時成立**，缺一就等於沒開：

**1. 啟動參數**（`_ensure_38.ps1` 已內建，只要 mmproj 檔案在就會自動掛）
```
--mmproj <路徑>  --image-min-tokens 1024
```
`--image-min-tokens 1024` 不能省。llama.cpp 啟動時會警告 Qwen-VL 低於 1024
image token 在 grounding 任務會失準 —— 實測預設只給 868，模型會把渲染雜點
誤判成訊號跳變；提到 1277 之後它會自己說「那只是游標標記，不是訊號」。

**2. `--tensor-split 6,13,13`**（不是舊的 `8,12,10`）

mmproj 是**最後才配置**的。舊比例會讓 device0（3070 8GB）剛好塞不下，
報 `cudaMalloc failed: out of memory`，錯誤訊息裡的 931127936 bytes
就是 mmproj 的大小。改成 6,13,13 之後 **200K ctx + 視覺同時成立**
（實測 28.3/32.7 GB），完全不用犧牲 context。

**3. Hermes 的視覺路由**（最容易漏掉的一步）

只改 llama-server 沒有用，Hermes 的 `vision_analyze` 工具**另有路由設定**，
預設指向 Vertex/Gemini。不改的話照樣走雲端、照樣燒免費額度（20 次/天/模型）。

```powershell
& $hermes config set auxiliary.vision.provider lmstudio
& $hermes config set auxiliary.vision.model qwen38_mtp
```

改完**桌面版要重開**才吃到（CLI 每次新進程會自動重讀）。

**4. 橋接器的 `STRIP_IMAGES = False`，改完要重啟橋接器**（最容易漏）

`hermes_bridge.py` 有個 `_strip_images()` 會把圖片整個抽掉、換成 Gemini 的
文字描述 —— 那是 mmproj 之前的權宜之計。前三項都做了但沒重啟橋接器的話，
記憶體裡跑的還是舊碼，mmproj 等於白掛，圖根本到不了模型眼前。

**怎麼分辨圖有沒有真的到模型眼前**：拿一張你知道答案的圖，問「視覺才答得出來」
的問題（背景是深色還是白色？某一欄有沒有畫出東西？）。走 Gemini 那次答
「white canvas」但圖其實是深色底，還描述了不存在的波形；本地視覺答
「深藍灰/炭黑色」並正確指出只有三個欄位有波形。回覆以 `Analysis:` 開頭
也是走 Gemini 的特徵。

**驗證**：
```powershell
(Invoke-RestMethod http://127.0.0.1:8001/props).modalities
# 要看到 vision=True
```

**壓縮（compression）建議留在雲端** —— 它在 ctx 快滿時觸發，那時本地模型
正扛著滿滿的 context，再要它做摘要會拖慢；而且壓縮品質決定壓完後還記不記得
在做什麼。觸發頻率低，不太吃額度。

⚠ **視覺不能單獨當驗證標準**：實測 27B 和 Gemini 兩個不同架構的模型，
在同一張波形圖的同一個渲染雜點上犯了**一樣的錯**。圖要跟數字交叉比對，
矛盾時以數字為準。

---

## 步驟 4：Hermes 桌面版

從官方下載安裝：https://hermes-assets.nousresearch.com/Hermes-Setup.exe

⚠ **不要用 git 版或 CLI 版** —— 桌面版才是穩定的（見 `docs/踩過的坑.md`）。

裝完先**不要**開，先做步驟 5。

---

## 步驟 5：Hermes 設定

複製 `hermes-config/config.yaml` 到 `%LOCALAPPDATA%\hermes\config.yaml`。

⚠ **官方安裝檔預設會用 Google Vertex，一定要改掉**，否則會出現
`HTTP 400 Malformed publisher model`。這份 config 已經設好指向本地模型。

**你要自己填的東西**（倉庫裡是空的，因為不能上傳金鑰）：

| 設定 | 怎麼填 |
|---|---|
| `auxiliary.compression.api_key` | 你自己的 Gemini API key |
| `auxiliary.vision.api_key` | 同上 |
| `vertex.project_id` | 若走 Vertex，填你的 GCP 專案 ID |

取得 Gemini key：https://aistudio.google.com/apikey

**壓縮和看圖為什麼要用雲端**：本地模型讀十幾萬 token 再摘要會超時
（實測 120 秒還沒吐第一個字），用 Gemini 只要 40 秒。詳見 `docs/` 底下的說明。

**驗證**：
```powershell
& "$env:LOCALAPPDATA\hermes\hermes-agent\venv\Scripts\hermes.exe" config show
```

---

## 步驟 6：橋接器

`gpu-host/_bridge.bat` 啟動，它會在 :1234 監聽。

需要 Python 3.11+。如果要用「手機傳圖給模型看」的功能，設環境變數：
```powershell
[Environment]::SetEnvironmentVariable('GEMINI_API_KEY', '你的key', 'User')
```
（不設也能跑，只是沒有視覺能力）

**驗證**：
```powershell
Invoke-RestMethod http://127.0.0.1:1234/v1/models
```

---

## 步驟 6.5：clangd（強烈建議）

Serena（程式碼符號索引）靠 LSP 運作，**C/C++ 需要 clangd**。
沒裝的話模型只能一次讀整個 `.c` 檔，context 消耗差好幾倍：

| | 讀整檔 | 符號查詢 |
|---|---|---|
| 一個 800 行的 gfx.c | 5,700 字元 | **400 字元** |

下載：https://github.com/clangd/clangd/releases
（挑 `clangd-windows-*.zip`，約 26MB）

解壓到 `C:\Users\<你>\dev\`，把 `clangd_*\bin` 加進使用者 PATH：

```powershell
$bin = 'C:\Users\<你>\dev\clangd_22.1.6\bin'
[Environment]::SetEnvironmentVariable('Path',
  [Environment]::GetEnvironmentVariable('Path','User') + ";$bin", 'User')
```

**驗證**：`clangd --version`

⚠ **裝完要重開 Hermes** —— Serena 在啟動時才偵測 language server，
跑到一半裝沒有用。

其他語言同理（Kotlin 要 kotlin-language-server、Python 要 pyright），
Serena 沒有自帶，都得自己裝進 PATH。

---

## 步驟 6.8：規則（SOUL.md）

Hermes 的行為規則放在 `%LOCALAPPDATA%\hermes\SOUL.md`。
那份**不管在哪個專案都會載入**（程式碼註解：SOUL.md from HERMES_HOME is
independent and always included when present），所以規則放這一份就好，
不用每個專案放 `.hermes.md` 再各自維護。

複製 `hermes-config/SOUL.md` 過去即可。

規則的來源是 `remote-station/gui/app.py` 的 `WIN_RULES`，流程是：

```
改 WIN_RULES  ->  rules/_extract_rules.py  ->  rules/_deploy_rules.py
                  （產出人看的完整版）        （寫進 SOUL.md）
```

`_deploy_rules.py` 用 `<!-- WIN_RULES:start -->` 標記，重跑會整段換掉不累積。

⚠ **改了規則要重開 Hermes** —— SOUL.md 是啟動時載入的。

Hermes 的規則載入優先序：`.hermes.md` > `AGENTS.md` > `CLAUDE.md`（都只看
專案目錄，沒有 git root 時甚至不往上找），SOUL.md 則是獨立且永遠載入。

---

## 步驟 7：還原 skill

```powershell
cd <倉庫>\skills
.\_sync.ps1 -Restore
```

之後**要重開 Hermes** 才會載入。

`skills/README.md` 有說明每個 skill 在做什麼。

---

## 步驟 8：還原記憶（選用）

`memory/` 底下是給 Claude Code 用的長期記憶，複製到
`~\.claude\projects\<專案資料夾>\memory\`。

裡面記著這台機器踩過的坑、實測數據、設定理由。
**如果你是新接手的人，這些值得先讀一遍** —— 很多是花好幾小時才查出來的。

---

## 步驟 9：手機/家人介面（選用）

`remote-station/gui/` 是一個 Flask 網頁介面，讓不會用終端機的人也能跟模型對話。

```powershell
cd remote-station\gui
pip install flask requests trafilatura
python app.py
```

預設 :5000。同網段的手機可以直接連。

⚠ `app.py` 裡的 `WIN_RULES` 是**給模型的行為規則**（怎麼省 context、
什麼時候寫交接文件）。改了之後要跑 `_extract_rules.py` 同步到 CLAUDE.md。

---

## 推理強度：用 low，不要用 high

`_ensure_38.ps1` 的 `-Think` 參數預設 `low`，**不要改成 high**：

| | 每輪耗時 |
|---|---|
| `low` | **2.5 秒** |
| `high` | **80 秒** |

實測一個多輪硬體除錯任務，high 模式下**單輪 740 秒**（12 分鐘），
而且那次產出的 30 個 token 全花在 thinking，`content` 是空的。
長任務用 high 根本跑不完。

韌體工作需要的是**低溫度**（`-Mode fw`，temp 0.3，暫存器名和數字不飄），
不是深度思考。

`1b-START-GPU-Server-THINK.bat` 是刻意保留的 high 模式，
只適合「一個短的、真的很難的推理問題」，不要用在長任務。

⚠ **兩邊都要設，不然會打架**。`_ensure_38.ps1` 的 `-Think low` 只是
llama-server 的預設值，Hermes 每次請求會帶自己的 `reasoning_effort` 覆蓋它。
Hermes 出廠是 `medium`，所以只改 server 端等於沒改：

```powershell
& $hermes config set agent.reasoning_effort low
```

設完就不用每次在 GUI 手動切了（`config.yaml` 第 26 行 `agent.reasoning_effort`）。

---

## 全部裝完後的自我檢查

```powershell
# 1. 模型活著
Invoke-RestMethod http://127.0.0.1:8001/v1/models

# 2. 橋接器活著
Invoke-RestMethod http://127.0.0.1:1234/v1/models

# 3. slot 配置正確
(Invoke-RestMethod http://127.0.0.1:8001/slots)[0].n_ctx

# 4. Hermes 設定沒問題
& "$env:LOCALAPPDATA\hermes\hermes-agent\venv\Scripts\hermes.exe" config show

# 5. 視覺開著（要看到 vision=True，不是 False）
(Invoke-RestMethod http://127.0.0.1:8001/props).modalities

# 6. 視覺走本地不是雲端（要是 lmstudio / qwen38_mtp）
$h = "$env:LOCALAPPDATA\hermes\hermes-agent\venv\Scripts\hermes.exe"
& $h config get auxiliary.vision.provider
& $h config get auxiliary.vision.model

# 7. 思考等級是 low（不用每次手動切）
& $h config get agent.reasoning_effort
```

然後開 Hermes，隨便問一句話。橋接器視窗會印
`[   1] 本機推論 OK  N.Ns` —— **有印就代表走本機不是雲端**。

---

## 遇到問題

`docs/踩過的坑.md` 記錄了所有踩過的雷，包含：

- 模型載入成功不代表能跑（tensor-split 太緊會 hang 而非崩潰）
- Hermes 的 provider 會凍結在 session 裡，改 config 對舊對話無效
- PowerShell 腳本必須 UTF-8 **含 BOM**，否則中文註解會解析錯誤
- llama.cpp 的 `--context-shift` 預設關閉，ctx 滿了會硬失敗

---

## 這套環境的設計取捨

| 決定 | 為什麼 |
|---|---|
| 用 llama.cpp 不用 vLLM | vLLM 假設每張卡一樣大，這裡是 8+12+12 不對稱 |
| 壓縮/看圖走雲端 | 本地做會超時，而且會跟主任務搶 GPU |
| 只跑一台，不用 ZeroTier 分散 | 兩台共用 slot 會互搶，實測慢 13 倍 |
| skill 只備份自訂的 | 官方那 80 幾個重裝會自己回來，一起備份會衝突 |
