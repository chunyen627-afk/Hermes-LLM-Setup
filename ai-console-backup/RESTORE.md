# AI 控制台＋本地模型 還原說明

這份備份只有**設定、腳本、參數**（約 8 MB），讓重灌或換電腦後能照著重建。
模型、引擎、預處理檔、Python 套件、礦工程式這些大檔案**沒有複製**，下面寫了去哪裡下載；
沒複製的大檔案清單、目前的版本和顯示卡資訊都記在 `MANIFEST.md`。

- 更新備份（私人版）：在原電腦執行 `python "G:\我的雲端硬碟\for-other-pc\ai-console-llm\_make_backup.py"`（約 1 分鐘，會整個重產 `files\`）
- 這是公開倉庫的**去敏版**：設定裡的金鑰、token、帳號都換成 `<REDACTED>` 等佔位字串，要自己填回（完整私人版在作者的 G: 雲端硬碟）。
- 所有腳本都寫死 `C:\Users\pjunm\...`。新電腦用同一個使用者名稱最省事；不同名稱就要把這些路徑全部取代。

## 備份內容對照

| `files\` 底下 | 還原到 | 是什麼 |
|---|---|---|
| `Strata\` | `C:\Users\pjunm\Strata\` | AI 控制台 `media_control.py`、啟動檔 `RUN-*`／`START-*`／`PREPARE-*.bat`、模型參數檔 `strata-*.json`、`start_media.ps1`、ZeroTier API |
| `Strata\packs\` | `C:\Users\pjunm\Strata\packs\` | 預處理產生的小檔（`native_experts.txt` 等）；大的 pack 檔要重跑 PREPARE 產生 |
| `Strata\engine*.BUILD.json` | — | 兩版引擎的版本記錄（engine＝0.1.40、engine-v0.1.45＝0.1.45） |
| `hermes\` | `%LOCALAPPDATA%\hermes\` | `config.yaml`、`SOUL.md`、`.env`、memories、skills（登入資訊 auth.json 沒備份，要重新登入） |
| `Qwen3.8-27B-desktop\` | `C:\Users\pjunm\OneDrive\Desktop\Qwen3.8-27B\` | 舊的 llama.cpp 啟動腳本＋`hermes_bridge.py`（127.0.0.1:1234 轉接，Hermes 連的就是它）、`START-ALL.bat` |
| `Pearl-mining\` | `C:\Users\pjunm\Pearl-mining\` | 辦公室挖礦管理 `guarded_miner.py`、風扇／溫度控制、`panel_bridge.py`（控制台挖礦分頁） |
| `dev\miner-status-ios\pc-service\` ＋ `pc-service\aggregator.json` | `C:\Users\pjunm\dev\miner-status-ios\pc-service\` | 礦機彙整服務（:18786）；重建礦機群看裡面的 `FLEET-REBUILD.md` |
| `dev\desk-remote\pc-helper\` ＋ `DeskRemote\config.json` | `C:\Users\pjunm\dev\desk-remote\pc-helper\`、`%LOCALAPPDATA%\DeskRemote\` | 手機遙控小幫手 |
| `dev\thermo-gateway\` ＋ `thermo-gateway\thermo-gateway.json` | `C:\Users\pjunm\dev\thermo-gateway\` | 溫濕度閘道 |
| `dev\qtc-fpga\host\` | `C:\Users\pjunm\dev\qtc-fpga\host\` | VCU118 FPGA 礦機主機端（控制台 FPGA 分頁） |
| `ComfyUI-H3\` | `C:\Users\pjunm\ComfyUI-H3\` | ComfyUI 設定（圖片／影片生成） |
| `python\*-requirements.txt` | — | 三個 Python 環境的套件清單 |
| `scheduled-tasks\*.xml` | 工作排程器 | 開機／登入自動啟動的 8 個排程工作 |

## 重建順序

1. **基本軟體**：NVIDIA 驅動（原機 610.88）、Git、Google Drive、ZeroTier（網路 <ZT_NETWORK_ID>）、
   Python 3.11.9 裝到 `%LOCALAPPDATA%\Programs\Python\Python311`，然後
   `python -m pip install -r files\python\python311-requirements.txt`。
2. **Strata＋AI 控制台**
   ```bat
   git clone https://github.com/Niko1221/Strata.git C:\Users\pjunm\Strata
   cd C:\Users\pjunm\Strata
   git checkout 82f46a8
   ```
   把 `files\Strata\` 全部複製進去（不會蓋到上游檔案，都是我們自己加的）。
   建環境：`python -m venv .venv`，再 `.venv\Scripts\pip install -r <備份>\files\python\strata-venv-requirements.txt`
   （CUDA 13 的 nvidia 套件也在裡面；參數檔的 `lib_dirs` 指到它）。
   引擎從 Strata 的 GitHub Releases 下載：**0.1.40 解壓到 `engine\`**（影片模式和視覺用）、**0.1.45 解壓到 `engine-v0.1.45\`**（圖片模式主模型用）。
3. **模型**（放 `C:\Users\pjunm\Strata-data\`，總共約 160 GB）
   - 主模型 Swift：Hugging Face `ukisai/Swift-1.5-Qwen3.8-Flash-Next-GSQ-RCO-GGUF` 的 IQ3_XXS 兩個分檔，
     放 `Strata-data\models\swift-abliterated-iq3_xxs\IQ3_XXS\`；視覺投影 `mmproj-Qwen3.8-Flash-Next-BF16.gguf` 放上一層。
   - 備用 Orca：`orcarouter/Qwen3.8-Flash-Next-Uncensored-GGUF`，用 `prepare_orca_model.py`、`fetch_orca_vision.py` 下載＋SHA256 驗證。
   - MTP 草稿權重（`Strata-data\mtp\`、`...\swift-abliterated-iq3_xxs\strata\rt\`）：步驟見 `Strata\migration-status.md`，裝好用 `verify_mtp_setup.py` 檢查。
   - 下載完跑 `PREPARE-VISION.bat`（Orca 用 `PREPARE-ORCA.bat`）產生 `packs\`。
4. **Hermes**：裝 Hermes Agent（原機 v0.20.4），把 `files\hermes\` 複製到 `%LOCALAPPDATA%\hermes\`，重新登入。
   Hermes 連 `http://127.0.0.1:1234/v1`（模型名 `swift-1.5-qwen3.8-flash-next-abliterated-iq3_xxs`、context 262144）。
5. **桌面舊資料夾**：`files\Qwen3.8-27B-desktop\` 複製到 `OneDrive\Desktop\Qwen3.8-27B\`（`start_media.ps1` 還會用到裡面的 `hermes_bridge.py`）。
6. **ComfyUI**：`git clone https://github.com/Comfy-Org/ComfyUI.git C:\Users\pjunm\ComfyUI-H3`、`git checkout d49e888`、
   `.venv` 照 `comfyui-venv-requirements.txt` 裝，複製 `user\default\comfy.settings.json`（原機沒有 extra_model_paths.yaml，模型放 ComfyUI 自己的 `models\`）。
7. **挖礦**：複製 `Pearl-mining\`；SRBMiner 3.8.8 與 krig 1.5.12 照 `dev\miner-status-ios\pc-service\FLEET-REBUILD.md` 下載（有網址和 SHA256），
   Defender 排除要自己加。
8. **服務**：複製 `dev\...` 三個服務資料夾和它們的設定檔（見上表）。
9. **排程工作**（管理員，用 `elevate`）：每個 XML 執行
   `schtasks /create /tn "<名稱>" /xml "<備份>\files\scheduled-tasks\<名稱>.xml"`。
   XML 裡記的是原機的使用者；換了帳號名稱要先改 `<UserId>`。
   `DeskRemoteElevated` 還需要 `C:\ProgramData\DeskRemote\bin\desk_remote_elevated.ps1`（小幫手安裝腳本會建）。
10. **桌面捷徑「本機 AI 控制台」**：目標 `C:\Users\pjunm\Strata\.venv\Scripts\pythonw.exe "C:\Users\pjunm\Strata\media_control.py"`。
    排程工作 AIConsole 則是用 Python311 的 pythonw 開。

## 主要參數（Strata，127.0.0.1:8001）

| 模式 | 參數檔 | 引擎 | GPU（順序） | 層切分 | Context | 常駐 experts | 預留顯存 | 其他 |
|---|---|---|---|---|---|---|---|---|
| 影片（預設） | `strata-swift-iq3_xxs-video.json` | 0.1.40 | 0,2 | 20 | 32K | 20 GiB | 1000 MiB | prefill 512、spec 4（min-p 0.7）＋MTP、KV int8、temp 0.6／top-p 0.95、不思考 |
| 圖片 | `strata-swift-iq3_xxs-image.json` | 0.1.45 | 1,2,0 | 14,34 | 256K | 36 GiB | 5000→1000 MiB | prefill 1024、KV 常駐 32K、pcie-frac 0、STRATA_* 五個環境變數 |

- 視覺（兩種模式相同）：`engine\strata-vision.exe`，CPU 8 執行緒，最多 300 tokens。
- 原機顯卡：0＝RTX 3070 8 GB、1＝RTX 3060 12 GB、2＝RTX 3060 12 GB。換顯卡或順序要改參數檔的 `gpu`、`layer_split`、`resident-budget-gib`。
- 其他調校過的版本（`strata-tune-*.json`、`strata-orca-*.json`）和測速結果（`tps-*.json`）也在 `files\Strata\`，可以拿來比對。
