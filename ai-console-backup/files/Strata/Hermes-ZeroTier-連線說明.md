# 另一台 Hermes 使用這台模型

主機：10.35.219.64；客戶端：10.35.219.158；ZeroTier 網路：<ZT_NETWORK_ID>。

## 客戶端 Hermes 桌面版

在 Custom Endpoints 新增 OpenAI 相容端點，先備份原有設定：

| 欄位 | 值 |
|---|---|
| 名稱 | 家中 Strata（ZeroTier） |
| Base URL | `http://10.35.219.64:1235/v1` |
| 模型 ID | `orcarouter-qwen3.8-flash-next-uncensored-iq3_xxs` |
| API Key | 可留空；介面必填時填 `zerotier-local` |
| 輸出上限 | 8192 tokens |
| 推理 | 日常使用 none／關閉；開發可選 low |

驗證採 ZeroTier 網路授權及 API 的來源 IP 白名單，沒有另設 Bearer 密鑰；`zerotier-local` 僅為介面佔位值，不是密碼。API 只綁定 ZeroTier 位址，只允許 10.35.219.158 及主機自測位址 10.35.219.64。主機已有 Python 入站允許規則，本次未停用或擴大防火牆。

先探索模型、啟用該端點，再開新對話。若 auxiliary compression／vision 有獨立設定，也應核對其模型與 API 指向本端點，勿留成客戶端 localhost 或未設定的雲端服務。請勿貼出 .env、auth.json 或其他憑證。

主機靜態模式為 262144 CTX；影片模式為 32768 CTX。遠端 Hermes 的上下文設定不能超過主機當前模式。切換主機模式會短暫中斷連線，必要時重新探索模型或調整客戶端設定。

## 使用方式與範圍

主機須開著「本機 AI 控制台」，選擇任一模型模式並等待就緒。ZeroTier API 隨模式啟動；停止全部或關閉控制台也會停止遠端入口。開機不自動載入。若啟動時 ZeroTier 不在線，先接通 ZeroTier，再停止並重新選擇模式。

模型推論使用主機 GPU；Hermes 的檔案、終端機與開發工具在客戶端執行。兩端同時提問共用目前的一個推論工作槽，可能排隊。本次只提供模型 API，未開放 ComfyUI、生圖／影片派工或主機檔案存取。上傳圖片可使用模型原生視覺，但只傳客戶端路徑不會自動讓主機取得圖片。

## 驗收

主機測試紀錄：`zerotier-api-verification-20261010.json`。主機對自身 ZeroTier IP 的測試不能取代另一台電腦的網路驗收。

客戶端應依序測試 GET `/v1/models`、一般聊天、串流、Hermes 實際工具呼叫及結果回讀。工具測試可在客戶端暫存資料夾新建唯一名稱文字檔，再讀回內容；不要修改既有專案。完成後回報實際模型 ID、各項結果、錯誤訊息及已遮蔽憑證的設定摘要。

## 主機維護

入口程式 `zerotier_api.py`、設定 `zerotier-api.json`、輪替紀錄 `zerotier-api.log`，由 `start_media.ps1` 啟動並繼承控制台的 Windows Job Object。只轉送模型探索、狀態、props 與 chat/completions，管理路徑會被拒絕。原本 localhost:1234 使用方式不變。

啟動器修改前備份：`C:\Users\pjunm\.cache\hermes-strata-backups\before-zerotier-20261010\start_media.ps1`。客戶端 ZeroTier IP 改變時須更新白名單並重啟服務。
