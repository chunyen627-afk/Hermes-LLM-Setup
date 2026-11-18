# 辦公室 2 挖礦（office2，RTX 3070 桌機）

私人資料夾，不進公開倉庫（`laptop-config.json` 含帳號）。

## 機器

- DESKTOP-GO9EDH0：RTX 3070 8 GB、i3-10100F、16 GB、Windows 10 19044
- ZeroTier 10.35.219.79；Windows 帳號 `pjunm`
- SSH：`ssh -i ~/.ssh/tn12_home pjunm@10.35.219.79`（管理員、只收金鑰、只准 office），用 `ENABLE-SSH-FOR-OFFICE.ps1` 開通（防火牆 `-Profile Any`、另開 office 可 ping）
- 2026-10-29 驅動 516.94 → 610.88（跟 office 同版；516.94 下 krig 報 `resident_b_workspace_alloc failed rc=-30`）。換驅動後原本 177.6 W 的功率上限回到預設 240 W

## 挖礦

- 跟筆電同一支保護程式 `laptop_miner.ps1`（原始檔在 `../laptop/`），調校值放在本資料夾的 `laptop-config.json`：worker `rtx3070`、狀態檔 `miner-status.json`
- 遠端檔案在 `C:\Users\pjunm\Pearl-mining\`（`krig-1.5.12\`、`logs\`、`STOP`、`guard-console.log`、`driver\610.88.exe`）
- 桌面捷徑沒有建立；開始／停止用手機 App 或彙整服務（aggregator.json 的 `office2`）。**重開機後不會自己挖**

## 調校（2026-10-29）

| 設定 | 算力 | 功耗 | 溫度 |
| --- | --- | --- | --- |
| 1100 MHz、風扇自動 | — | 約 150 W | 15 秒內 39→80°C（風扇還沒轉） |
| 1100 MHz、程式控風扇 90–100% | — | 143–155 W | 25 秒內 54→80°C |
| 800 MHz、顯存 6801 | 32 TH/s | 115–126 W | 64–69°C |
| 900 MHz、顯存 6801 | 36.4 TH/s | 128–140 W | 69–74°C |
| 900 MHz、顯存 5001 | 36.3 TH/s | 105–113 W | 58–60°C |
| **1150 上限（實際 1140）、顯存 5001（採用）** | **約 42.5 TH/s** | **122–126 W** | **62–64°C** |
| 1200 MHz、顯存 5001 | 約 47 TH/s | 135–142 W | 1 分鐘內到 76°C |

- 顯存 5001 跟 6801 算力一樣，少約 20 W、低約 12°C（2026-10-29）；810 會掉到約 9 TH/s。顯存檔位只有 7001／6801／5001／810／405
- 1140→1200 MHz 功耗跳 ~13 W、溫度很快衝到 76°C，所以上限定 1150 不讓它來回跳；要再高要先處理散熱（Kryptex 公布 73 TH/s @ ~170 W，這張 138 W 就 76°C，像是散熱膏／散熱片接觸不良）
- 曾一次掉進 P5（顯存 810、算力 1/4）且重鎖顯存無效，重啟礦工才恢復；保護程式已加「顯存低於設定一半 60 秒 → 重啟礦工，10 分鐘內再發生就停」
- SRBMiner 3.4.9 實測份額全被 Kryptex 判 invalid（舊版演算法），不能用
- **SRBMiner 3.8.1（2026-10-29 起採用，驗證中）**：1150/5001＝52.2 TH/s @73 W、1300/5001＝59.1 TH/s @84 W，份額有接受、0 拒絕；抽成 2%（krig 0%）。設定 `"miner": "srbminer"`（`srb_exe` 預設 `srbminer-3.8.1\SRBMiner-Multi-3-8-1\SRBMiner-MULTI.exe`），數字從它的本機 API（127.0.0.1:21550）讀，礦工主控台輸出存 `logs\mining-*-console.txt`。上限放回 1500，保護程式依溫度自己爬。需要 Defender 排除 `C:\Users\pjunm\Pearl-mining`（已加）
- 同卡比 QTC：SRBMiner 228 MH/s @74 W ≈ NT$38/天 vs PRL 1300 MHz ≈ NT$46/天（10/29 行情），維持 PRL
- 溫度條件比照 office：76°C 起降頻、82°C 停止、76°C 以上不開始；保護程式控風扇（最低 50%，40°C→40%、75°C→100%），停止後還給驅動
- 彙整服務看守：≥84°C 遠端殺礦工、≥90°C 遠端關機；溫度保護線 82
