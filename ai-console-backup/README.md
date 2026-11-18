# AI 控制台＋本地模型 設定備份（去敏版）

這裡是辦公室主機（`本機 AI 控制台`、Strata 本地模型、Hermes、挖礦管理、礦機彙整、手機遙控小幫手、溫濕度閘道）的
**設定、腳本與參數**，用來重灌或換電腦時快速重建。

- **去敏**：金鑰、token、密碼、礦池帳號、通知頻道、LINE 使用者 ID、藍牙感測器 bindkey、MAC、SSH 公鑰、
  ZeroTier 網路 ID、排程工作裡的使用者 SID 都換成 `<REDACTED>`／`<...>` 佔位字串，還原時要自己填回。
  Hermes 的 `.env`、`auth.json`、memories 沒放進來；skills 在本倉庫的 `skills/`。
- **完整私人版**（含真實設定值）在作者的 Google Drive：`G:\我的雲端硬碟\for-other-pc\ai-console-llm\`，
  有 `RESTORE.md`（還原步驟）、`MANIFEST.md`（版本、顯示卡、沒複製的大檔清單）、`_make_backup.py`（重產備份）。
- **大檔案沒放**：模型（約 160 GB）、Strata 引擎、packs、Python 虛擬環境、礦工程式。下載來源與版本寫在 `RESTORE.md`。
- **還原步驟**：看同資料夾的 [`RESTORE.md`](RESTORE.md)，`files\` 對照表裡的路徑就是這裡的 `files/`。

## 怎麼更新

在辦公室主機：

1. `python "G:\我的雲端硬碟\for-other-pc\ai-console-llm\_make_backup.py"`：重產 G: 的完整私人版。
2. `python "G:\我的雲端硬碟\for-other-pc\ai-console-llm\_sanitize_for_github.py" "G:\我的雲端硬碟\for-other-pc\ai-console-llm\files" <暫存資料夾>`：
   產生去敏版，把結果換掉這裡的 `files/`。
3. 推之前，先用私人版收集到的機密值掃一次確認是 0，再讀完整 staged diff。
