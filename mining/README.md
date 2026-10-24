# 珍珠挖礦專區：Windows / NVIDIA / Kryptex

更新：2026-10-24。這是可攜式部署材料與接手機制，不代表另兩台已安裝或已驗證。
目前主機的正式腳本仍在本機 `Pearl-mining` 資料夾；本專區不會覆寫正在跑的環境。

### 本機最新交接：不依賴 Afterburner

最新本機熱保護改為先降載：76°C 起每30秒降低5W/150MHz（最低100W/300MHz）；
80°C 立即降到100W/300MHz繼續挖，30秒仍未低於78°C或達82°C才停止。
本輪只降不升，重新啟動前需低於76°C。七項模擬測試涵蓋80°C不停機降載、
冷卻逾時、82°C硬停與設定失敗。以下80°C即停的描述及通用範本是較早保守版本；
新機仍先用保守範本，不能直接搬用這台的功率下限。

同日後續已備份 Afterburner Profiles/啟動任務、退出程式並停用其自啟動。
本機正式啟動器由管理員視窗統一套用功率/核心設定，礦工明確使用零核心/記憶體偏移；
原生 NVML 每兩秒控制六個風扇，40°C 約40%、75°C 達100%，下降有遲滯。
逐風扇讀回目標值，控制失敗就停止。獨立的一次性清理程序等待該 guard process 結束，
恢復 NVIDIA 自動風扇。已實測強制結束 guard：礦工清理成功、六個 fan policy 全部恢復自動。
仍保留 80°C 停止，尚未做此風扇方案的 24 小時穩定驗證。

**此 repo 的 portable_guard.py 仍是通用保護範本，不包含本機三卡專用風扇控制器。**
新機代理必須自行盤點 fan count、NVML 支援與管理員權限，再測試控制/還原；
不得因本機成功就宣稱單3070或雙2060已驗證，或直接關掉尚未交接的風扇控制軟體。
下面提到 Afterburner 保留曲線的測試記錄是先前階段的歷史，不是目前依賴。

## 使用者已決定的順序

1. 現有三 GPU 主機持續挖，異常才停止。沒有每日 24 小時停機限制。
2. 可提領餘額先累積等值 10 USDT，第一次直接以 USDT BEP20 轉 MAX。
3. **確認 MAX 實際入帳後**，才部署單張 RTX 3070 與雙張 RTX 2060。
4. 後續以累積 50 USDT 再轉 MAX 為規劃；提領時重新查費率與網路。
5. 不自動轉帳、不自動交易、不自動重試過熱停機、不設定開機自動挖礦。

## 同帳號與機器命名

Kryptex 支援同帳號加入多台電腦，收益集中；官方沒有列出固定台數上限。
KRig 直連格式是 `mining_username.worker_name`。使用 Profile 裡的 Mining Username，**不是 Email、密碼或錢包地址**。
建議現有主機保留 `llmrig`，另兩台使用 `rtx3070`、`dual2060`，避免紀錄混在一起。
同帳號不代表共用超頻設定：每台 GPU UUID、溫度、功率上限都必須重新盤點。

## 已知主機測試紀錄（不是其他機器的參數）

| GPU | 候選功率上限 | 核心鎖定目標 | 短測末溫度 |
|---|---:|---:|---:|
| RTX 3070 | 110W | 900MHz | 73°C |
| 較熱 RTX 3060 | 100W | 600MHz | 76°C |
| 較涼 RTX 3060 | 125W | 1500MHz | 69°C |

實際核心會受功率限制而低於鎖定目標。保留原 Afterburner 風扇曲線與記憶體偏移。
基準約 62 TH/s，均衡候選約 72 TH/s，增加約 16%。均衡測試總長約 8 分鐘，
最終設定約 4 分鐘；後續約一小時紀錄約 71.4 TH/s、19 accepted、0 stale、0 rejected。
這不是 24 小時或一週穩定認證，也不是收益保證。
原全力短測 3070 曾到 91°C；單獨拉高較涼卡也曾使另一張到 78°C，已停止該試驗。

## 新機部署前

- 先確認首次 MAX 入帳已完成，或使用者明確變更部署時機。
- 記錄 GPU 型號/UUID、電源供應器、驅動、原功率上限、Afterburner offsets/風扇曲線。
- 備份本機 AI 設定、啟動器及 Afterburner Profiles；模型保留原處，不刪除。
  「保留模型檔」不等於完整複製備份，文件要分清楚。
- 檢查沒有其他 miner、模型、生圖、影片作業。不得無差別終止全部 python。
- RTX 2060 可能為 6GB/12GB，先確認版本；雙卡逐張驗證，再一起熱測。

## 安裝環境

在本機複製此 repo，進入 `mining`。需要 Windows、NVIDIA 驅動及 Python 3.11。
以下在 PowerShell 執行；有 RTK 可於一般命令前加 `rtk`，沒有則使用原生命令。

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install pywin32
nvidia-smi --query-gpu=index,name,uuid,power.limit,power.min_limit,power.max_limit,temperature.gpu --format=csv
Copy-Item config.example.json config.local.json
```

KRig 1.5.12 官方 Windows ZIP：
https://github.com/kryptex-miners-org/kryptex-miners/releases/download/krig-1-5-12/krig-miner-1.5.12-win-x64.zip

已核對該版本 SHA256：
`c4342a413e5c244d9fcc288b2e515b2fed468b262c7393b723ace30e25f077f3`

用 `Get-FileHash <ZIP> -Algorithm SHA256` 比對後再解壓，把完整執行檔所在目錄的內容放在
`runtime`，確認 `runtime/krig-miner.exe` 存在。不要直接用下載包附帶的帳號範例啟動器。
不要關閉防毒保護來強行安裝。更新其他版本時重新驗證來源、SHA256與參數相容性。

## 每台建立私人設定

編輯 `config.local.json`（已 gitignore）：

- `mining_user`：使用者自己的 Kryptex Mining Username。
- `worker`：該台獨有名稱。
- `gpus`：每個要挖的 GPU 一筆 index、實際 UUID、該卡經確認的 power_w/core_mhz。
  雙 2060 必須兩筆不同 index/UUID。**範本的 100W/750MHz 不是已驗證推薦值**。
- `ai_stop_command`：可選的本機停止指令參數陣列；只填已讀過且可信的腳本。
  使用本專案 AI 控制台者填 `["本機python完整路徑","本機media_control.py完整路徑","--action","Stop"]`。
  未使用控制台可留空，但需自行先停 AI；程式仍檢查連接埠與顯存。
- `ai_ports`：本機 AI 服務連接埠，預設 8001/8188/8189/5000；新機有其他服務需補上。
- `idle_memory_mib`：預設每卡啟動前不超過 2048MiB；Windows 桌面占用不會歸零。
- `validated_for_this_machine`：保留 false，直到盤點、備份、套用保守設定及保護檢查完成，
  才可在有人監看下設 true 進行第一輪測試。這個開關不表示長期測試已通過。

可攜版**不自動修改硬體功率/頻率**；先由當台代理使用管理員權限套用已評估參數，
等待設定生效，保護程式會檢查實際上限。例：

```powershell
# 以下變數由當台盤點後決定，不能照抄其他主機的數字。
nvidia-smi -i $gpuIndex -pl $validatedPowerW
nvidia-smi -i $gpuIndex -lgc "$validatedCoreMHz,$validatedCoreMHz"
```

## 開始、停止與保護

雙擊 `START.bat`；可在桌面建立指向它的捷徑。沒有背景自動重啟或每日時間上限。

啟動順序：單實例鎖 → 本機 AI 停止指令 → 最多約一分鐘等待連接埠關閉與顯存下降，
連續三次通過 → 啟動 KRig。顯存低於門檻是低占用檢查，不證明所有 GPU context 都不存在。
保護程式持續約每 2 秒檢查 GPU；78°C 警告、80°C 停止，讀值異常或設定不符也停止。
15 分鐘沒有新的 accepted share、礦工自行退出皆停止。有效份額是隨機的，此門檻可能誤停，
但不應取消檢查來掩蓋連線問題。熱輪詢不能保證完全沒有瞬間超溫。
關閉視窗或 Ctrl+C 停止；Windows Job Object 關閉時清理所屬 miner。

`logs` 保留礦工紀錄、GPU JSONL及結束原因。長期運行需留意磁碟容量，停止後再封存舊紀錄。
啟動多個不同副本可能繞過單資料夾鎖，因此一台只部署一個正式副本。

## 多機使用與監控安排（2026-10-24 更新）

- office 三 GPU 主機與家裡 RTX 5080 都兼作其他工作，使用者手動開關挖礦；目前不安排持續健康監控、不自動重啟，主動停止不算故障。
- office 本機已整合 AI 控制台與挖礦入口，新增綠／黃／紅／灰燈，分別表示正常、啟動中或狀態待確認、異常、待機或手動停止；更新於下次開啟套用。這是本機客製功能，並非本目錄通用腳本已具備的功能。
- 後續另外兩台預計長期挖礦（原規劃單 RTX 3070、雙 RTX 2060，部署時核對），上線後再由 Codex 協助安排異常通知，現在尚未啟用健康監控排程。
- ZeroTier＋office／DOT 唯讀集中監控仍是候選方案，尚未部署。若選用，須確認集中端離線時如何處理，並區分手動停止、異常退出、失聯與資料過期。
- 保留現有首次提領達標提醒；首次成功轉 MAX 後再安排另兩台。提醒不代替健康監控，也不授權自動交易或提領。
- 各台保留獨立溫控與還原設定，不直接複製三卡主機的功率、時脈、風扇參數。

## 驗收與還原

1. 先逐卡保守短測，再雙卡或全卡 30～60 分鐘熱測，最後 24 小時觀察。
2. 記錄平均算力、最大溫度、實際功耗、accepted/stale/rejected、重啟與停機原因。
3. 工作管理員 3D 使用率不代表 CUDA 算力；以礦工每卡輸出與礦池有效份額交叉確認。
4. 以可用餘額的實際增量評估日收益；礦池 30 分鐘均值、新工作機尚未填滿時間窗、
   總餘額與可提領餘額都不能混為一談。
5. 原 AI 還原：停止 miner，解除本次核心/記憶體鎖（`nvidia-smi -rgc/-rmc`），
   恢復**該台備份**的功率限制與 Afterburner 設定，再開 AI 控制台。
   若原本有自訂鎖頻，必須重新套用其原設定；解除鎖定不是完整還原。

## 轉 MAX 與費用（2026-10-24 查詢，交易前再確認）

USDT BEP20 提領最低 1.5 USDT，費用 0.5 USDT；第一次規劃累積 10 USDT，
後續 50 USDT。Kryptex 餘額以 BTC 保存；換 USDT 的價差與 MAX 賣台幣費另計。
閃電網路是 2% 而非免費，不能直接填 MAX 的 BTC 主網地址。
提領、驗證碼及交易最後確認由使用者完成，不寫入腳本。
本 repo 不含帳戶、Email、登入 Token、錢包、原始私人紀錄或個人路徑。

## 交給另一台 Codex 的提示詞

> 請讀取本 repo 的 mining/README.md 與規則，先確認主機首次轉 MAX 已成功。
> 本機是單張 RTX 3070／雙 RTX 2060（依實機確認），使用同一 Kryptex 帳號、獨立 worker。
> 先盤點硬體與備份原設定，不刪除 AI 模型，不搬用三卡主機參數。
> 安裝官方 KRig 並核對 SHA256，建立不提交 GitHub 的 config.local.json。
> 保留 80°C 停止、78°C 警告、無有效份額/異常停止及關窗清理；持續運行但不自動重啟。
> 逐卡調校並做全機熱測，記錄可還原值及實際入帳，提供桌面單一啟動入口。
> 別索取密碼/OTP，別自行提領或交易。回報環境、參數、驗收範圍、未完成項目。

來源：[同帳號多機](https://www.kryptex.com/en/articles/many-pcs-en) ·
[帳戶直連規則](https://pool.kryptex.com/articles/account-mining-en) ·
[官方費率](https://www.kryptex.com/en/fees) ·
[MAX 公開幣種網路](https://max-api.maicoin.com/api/v3/currencies)
