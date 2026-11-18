# 礦機群重建手冊（2026-10-29 狀態，11/02 SRBMiner 升 3.8.3、11/08 升 3.8.5、11/09 升 3.8.6、11/12 升 3.8.7、11/15 升 3.8.8）

私人資料夾，不進公開倉庫（含帳號、IP、路徑）。每台怎麼裝回來、設定在哪、哪些坑踩過。

## 總覽

| 機器 | 位置／IP | 礦工 | 保護程式 | 算力（冷氣下） | 電費 |
| --- | --- | --- | --- | --- | --- |
| office（3070＋3060×2） | 這台 10.35.219.64 | SRBMiner 3.8.8 | `Pearl-mining\guarded_miner.py`（AI 控制台啟動） | ~150 TH/s | 不計 |
| RTX 5080 | DESKTOP-2E0865I 10.35.219.158，SSH `user`（管理員） | SRBMiner 3.8.8 | Codex 的 `session.py`＋`portable_guard.py` | ~205 TH/s | 計 |
| 辦公室 2（3070） | DESKTOP-GO9EDH0 10.35.219.79，SSH `pjunm`（管理員） | SRBMiner 3.8.8 | `laptop_miner.ps1` | ~69 TH/s | 不計 |
| 筆電（3070 Laptop） | DESKTOP-RF73D8R 10.35.219.180，SSH `pjunman` | SRBMiner 3.8.8 | `laptop_miner.ps1` | ~43 TH/s | 計（依電價自動開關） |
| VCU118 FPGA | 接在 office | 自製 bitstream＋relay | `qtc-fpga\host\mining_ctl.py` | 120 MH/s（QTC） | 不計 |

全部同一個 Kryptex 帳號（見 `aggregator.json` 的 `earnings.account`），worker：office＝`llmrig`、5080＝`rtx5080`、辦公室 2＝`rtx3070`、筆電＝`laptop3070`、FPGA＝`vcu118`。礦池節點 prl-hk／prl-sg／prl（:8048 TLS），啟動時量延遲排序。

## SRBMiner（四台顯卡共用）

- 版本 **3.8.8**（2026-11-15 起；NVIDIA 30／40／50 pearlhash 效率改善、省電調校修正、**移除 `--gpu-extra-config`**（四台腳本都沒用到，升前已查）），`https://github.com/doktor83/SRBMiner-Multi/releases/download/3.8.8/SRBMiner-Multi-3-8-8-win64.zip`，SHA256 `818D16784C759DB0AB647BA612A3B85D5137FCAC1A79D715FAC2F4248D47D879`（＝GitHub digest；MD5 `f69c8f7d6b4346f5a1fac450cace4f37`＝官方公布；四台解壓前各自核對過）。路徑 `srbminer-3.8.8\SRBMiner-Multi-3-8-8`（5080 `C:\Users\user\srbminer\SRBMiner-Multi-3-8-8`），3.8.7 留著備退，改前備份 `*.before-srb388.*`（office `guarded_miner.before-srb388.py`；5080 `Pearl-mining\portable_guard.before-srb388.py`、`Pearl-monitor\backup\monitor.before-srb388.py`，monitor.py 加認 3-8-8 後重開 `Pearl-ReadOnly-Monitor`；辦公室 2／筆電 `laptop-config.before-srb388.json`）。四台用控制 API stop→start 重啟（筆電因此進手動優先，到下次電價變動才回自動）。升完 3 分鐘：office 152、5080 174–188、辦公室 2 58（照設定從 1200 MHz 爬升）、筆電 35（從 900 MHz 爬升），份額全部接受、0 拒絕。
- 前一版 **3.8.7**（2026-11-12 起；NVIDIA 30／40／50 pearlhash 小幅效率改善、省電調校修正、新增 `--gpu-power-tuner-value`），`https://github.com/doktor83/SRBMiner-Multi/releases/download/3.8.7/SRBMiner-Multi-3-8-7-win64.zip`，SHA256 `CBFC6FA4DAF22641E0F845922F8FBCCFF7FF1DC144AF7316061BCA06B76CFB1F`（＝GitHub digest；MD5 `ff5f70ca61872ab350af3b49ceb028cb`＝官方公布）。路徑 `srbminer-3.8.7\SRBMiner-Multi-3-8-7`（5080 `SRBMiner-Multi-3-8-7`），3.8.6 留著備退，改前備份 `*.before-srb387.*`（office `guarded_miner.before-srb387.py`；5080 `Pearl-mining\portable_guard.before-srb387.py`、`Pearl-monitor\backup\monitor.before-srb387.py`，monitor.py 加認 3-8-7 後重開 `Pearl-ReadOnly-Monitor`；辦公室 2／筆電 `laptop-config.before-srb387.json`）。四台都是改檔抓回 office 用 Edit 改再 scp 回去。升完：office 133（上班低功耗）、5080 197、辦公室 2 從 1200 MHz 爬升中，份額 0 拒絕；筆電叫醒裝好就關機，下次夜挖才是第一次跑 3.8.7。
- 前一版 **3.8.6**（2026-11-09 起；改善 NVIDIA 30／40／50 系列 pearlhash 效率），`https://github.com/doktor83/SRBMiner-Multi/releases/download/3.8.6/SRBMiner-Multi-3-8-6-win64.zip`，SHA256 `D44AA90BF857E526805C95E153B8C089C2188DAE5BA0C4FAD83D516168FB424B`（＝GitHub digest；MD5 `7fa435946e7d9b3229307ee61a97290d`＝官方公布）。路徑 `srbminer-3.8.6\SRBMiner-Multi-3-8-6`（5080 `SRBMiner-Multi-3-8-6`），3.8.5 留著備退，改前備份 `*.before-srb386.*`（5080 的 guard 備份在 `Pearl-mining\portable_guard.before-srb386.py`）。每天 09:04 Claude 排程 `srbminer-update-check` 自動查新版（只回報不升級）。**5080 重開時若桌面程式（影片、串流、瀏覽器）佔顯存 >6 GB 會卡開挖門檻**（`idle_memory_mib` 6144；在跑時不會被擋，只擋啟動），11/09 升級時就卡住，要先關程式。
- 前一版 **3.8.5**（2026-11-08 起；Kryptex 硬體頁標 3.8.3「此矿工软件已过期」；3.8.4／3.8.5 只改省電調校 `--gpu-power-tuner`（3.8.4 加 `--gpu-mclock 405`），pearlhash 算力沒改），`https://github.com/doktor83/SRBMiner-Multi/releases/download/3.8.5/SRBMiner-Multi-3-8-5-win64.zip`，SHA256 `10395CE7190AF40D1A5AC524962F03E0A74AF173A0C715645BEA254AF6545982`（＝GitHub digest；MD5 `1f1509095ad8500d81ce05994b629123`＝官方公布）。路徑全部改成 `srbminer-3.8.5\SRBMiner-Multi-3-8-5`（5080 `SRBMiner-Multi-3-8-5`），3.8.3 資料夾留著備退，改前備份 `*.before-srb385.*`；5080 的 monitor.py 改完要重開排程 `Pearl-ReadOnly-Monitor`。**筆電的挖礦資料夾在 `C:\Users\pjunm\Pearl-mining`（SSH 帳號是 pjunman，但資料夾在 pjunm 底下）**：11/08 誤解壓到 pjunman 底下，不在 Defender 排除內，exe 幾秒內就被隔離。
- 前一版 **3.8.3**（2026-11-02 起；Kryptex 頁面標 3.8.1「已過期」；3.8.3 修 pearlhash 算力／功耗忽高忽低、NVIDIA quantus 加速），`https://github.com/doktor83/SRBMiner-Multi/releases/download/3.8.3/SRBMiner-Multi-3-8-3-win64.zip`，SHA256 `92AF8D2DB5BC96DF62825487E7F384B8AB04E5DB26A634E29C2CD86DCBB87D0E`（與 GitHub 公布的 digest 一致）。3.8.1（SHA256 `16BD455557BD3698E004D1F6B5D19290B9B59E9F9C8EF9245ACAFDAEB2413E79`）留在旁邊的資料夾備退。**3.4.9 等舊版份額全被 Kryptex 判 invalid**。
- 每台的路徑：office `Pearl-mining\srbminer-3.8.3\SRBMiner-Multi-3-8-3\`（`guarded_miner.py` 的 `SRB_EXE`）；辦公室 2／筆電同一相對路徑，由 `laptop-config.json` 的 `srb_exe` 指定（腳本預設仍是 3.8.1）；5080 `C:\Users\user\srbminer\SRBMiner-Multi-3-8-3\`（`portable_guard.py` 的 `SRB_EXE`；`Pearl-monitor\monitor.py` 兩版都認）。新版解壓在既有的排除資料夾裡，不用再加 Defender 排除。
- 升級步驟（11/02 做法）：office 下載並核對雜湊→scp 到各台、各台再核一次→解壓→改路徑（改前備份 `*.before-srb383.*`）→透過彙整服務 `/control/<機器>/stop` 再 `start`（保護程式只在啟動時讀路徑，礦工自己重啟不會換版）。筆電關機時用 `/control/laptop3070/wake` 叫醒、裝好再關機，下次自動開挖就是新版。退回 3.8.1：路徑改回（或還原 `.before-srb383` 備份），再停開一次。
- 抽成 2%（krig 0%）。exe 沒有數位簽章。
- **每台都要先由使用者手動加 Defender 排除**（我不能改安全設定），否則解壓就被刪。office／辦公室 2／筆電排除 `C:\Users\pjunm\Pearl-mining`；5080 只排除 `C:\Users\user\srbminer`（它的挖礦資料夾有 Codex 隔離的惡意程式 `quarantine-20261025-133402`，不能整個排除）。
- 參數：`--algorithm pearlhash --pool a:8048,b:8048,c:8048 --wallet 帳號.worker×3 --password x,x,x --tls true,true,true --disable-cpu --disable-gpu-amd --disable-gpu-intel --api-enable --api-port 21550 --log-file …`。數字從 `http://127.0.0.1:21550` 的 `algorithms[0].hashrate.gpu.total`（H/s）與 `shares.accepted/rejected` 讀。啟動後約 60 秒算力才出來。
- 換回 krig：office／5080 刪 `miner.json`；辦公室 2／筆電把 config 的 `"miner"` 拿掉。exe 不在時也會自動退回 krig。

## office（這台）

- 檔案在 `C:\Users\pjunm\Pearl-mining`，本資料夾 `office-pc\` 有 10/29 的副本。
- `miner.json` `{"miner": "srbminer"}`；`srbminer-3.8.1\SRBMiner-Multi-3-8-1\SRBMiner-MULTI.exe`。
- 時脈／功率上限要**三處一起改**：`SET-LOW-CLOCKS.ps1`（開挖時套用，經 PearlPanelBridge 排程以管理員跑）、`thermal_control.py` 的 `BASE`、`guarded_miner.py` 的 `caps`（不一致會被當「設定被改」停機）。改完要重開挖（`OfficeControl` stop/start 或 AI 控制台）。
- 現值：3070 110 W／1500 MHz／顯存 5001；中間 3060 100 W／1350／5001；外側 3060 125 W／1800／預設顯存（5001 在 krig 會掉 15%）。停挖 `RESTORE-AI-POWER.ps1` 還原功率、時脈、顯存。
- 溫度：76°C 每 30 秒降 5 W／150 MHz；≤70°C 滿 3 分鐘每分鐘回升一級到 BASE；80°C 緊急降到 300 MHz，30 秒內降不到 78°C 就停；82°C 停。
- 上班時段低功耗（11/03 起，使用者嫌吵）：`thermal_control.py` 的 `QUIET`＝(100,1250)、(100,1100)、(100,1600)，**上班日 08:00–18:00** 由保護程式自動切換（不用重開），其他時間回 `BASE`。上班日依人事總處「政府行政機關辦公日曆表」（data.gov.tw 資料集 14718，每年一個 CSV，放 `Pearl-mining\calendar6.csv`、`2027.csv`）：平日放假照原設定、週末補班會降功耗；沒有該年檔案就退回週一到週五。**每年 6 月左右人事總處會公布下一年，要下載 UTF-8 版存成 `calendar\<西元年>.csv`、重開挖礦才會讀到**（2028 年要在 2027 下半年補）。100 W 是驅動最低值，所以靠鎖核心時脈壓功耗。`SET-LOW-CLOCKS.ps1` 跟 `guarded_miner.py` 的 caps 仍是 BASE（QUIET 都在它之下）。實測：風扇 76／79／79%、61–63°C、229 W、135.5 TH/s（原本 88–93%、68–71°C、304 W、154.4 TH/s，算力 −12%）。風扇曲線沒改（40°C 40% 到 75°C 100%）；11/03 試過「守 72°C 的低風扇控制」，使用者嫌吵、已退回，那版在 `test_fan_control.quiet-reverted.py`／`fan_control.py` 歷史中。改前備份 `*.before-quiet-power.*`。
- 狀態：guarded_miner 把 SRBMiner 的 th_s／accepted／rejected／data_at／last_accepted_at 寫進 `panel-mining-state.json`，彙整服務讀不到 krig `Total:` 行時改讀這些。
- 測試：`test_thermal.py`（15，含上班時段切換與日曆）、`test_health_light.py`、`test_panel_integration.py`、`test_state_io.py`。
- 實體：最左 3070、中間 MSI 3060（被夾住，最熱）、最右 3060。直立壁掛機架，熱氣往上；小風扇從上往下吹反而更熱。

## RTX 5080

- 挖礦資料夾 `C:\Users\user\Documents\Codex\2026-10-24\https-github-com-chunyen627-afk-hermes\outputs\Pearl-mining`（venv 在 `.venv`），監控在旁邊的 `Pearl-monitor`（排程工作 `Pearl-ReadOnly-Monitor`，埠 18785 只准 office）。本資料夾 `rtx5080\` 有 10/29 的 `portable_guard.py`、`session.py`、`monitor.py`、`config.local.json`、`miner.json` 副本。
- `miner.json` 在挖礦資料夾；SRBMiner 在 `C:\Users\user\srbminer\SRBMiner-Multi-3-8-1\`。
- `portable_guard.py` 的 `KrigStyleLog` 把 SRBMiner 數字轉寫成 krig 格式行（`Total:`、`share accepted:`、`connected:`）到 `logs\mining-<stamp>.log`，所以 monitor、guard 的進度檢查、節點看守都照舊；SRBMiner 自己的 log 是 `logs\srbminer-<stamp>.log`。`monitor.py` 的 `miner_processes` 認 krig 與 SRBMiner 兩種 exe。
- `config.local.json`：power_w 250、core_mhz 2700、downclock 65、stop_c 75。`session.py`：≥65°C 每 15 秒降 150 MHz；≤60°C 滿 1 分鐘每分鐘升 50 到 core_mhz；75°C 停。冷氣下平衡在 2452 MHz／62°C／223 W。啟動要 GPU <60°C。
- 開停：彙整服務 `rtx_control_commands`（開＝WMI 跑 venv python session.py；停＝寫 `STOP_REQUEST`）。
- 備份都在 RTX 的 `backup\`（`portable_guard.before-srbminer.py`、`session.before-recover.py`、`config.local.before-*.json`）與 `Pearl-monitor\backup\monitor.before-srbminer.py`。測試 `test_guard.py`（11）、`Pearl-monitor\test_monitor.py`。
- 別碰那個資料夾裡 Codex 的惡意程式清除／Windows Update 修復腳本與報告。
- **邊用邊挖（2026-10-31，使用者：可以降速，不要停）**：guard 每 30 秒把 SRBMiner 的 Windows 顯卡排程優先權設成 IDLE（`yield_gpu()`；啟動當下設的會被蓋回 NORMAL，所以要反覆檢查）。有遊戲或 Moonlight 串流要用顯卡時礦工自動讓出，沒人用就全速。實測（Vulkan 基準程式當遊戲，單獨 207 MH/s）：

  | 礦工設定 | 基準程式 | 礦工 |
  | --- | --- | --- |
  | NORMAL、BELOW_NORMAL、`--gpu-intensity` 8–24 | 92–98 MH/s（對半分） | 96–106 TH/s |
  | IDLE | 223 MH/s | 6–78 TH/s，基準一停回到 ~202，同一程序不停 |

  `YieldWatch`：算力低於本輪最佳一半且顯卡使用率 ≥50% 時，那段時間不算進份額／回報逾時（打一整晚遊戲也不會被當成礦工壞掉而停）。`read_gpus()` 失敗重試 4 次（每次隔 5 秒）才停（10/31 系統記憶體不足時 nvidia-smi 回 255 兩次而停挖）。彙整服務 `giving_way`（aggregator.json，rtx5080：normal_th 200、min_gpu_w 100）：跑超過 3 分鐘、算力低於一半、顯卡 ≥100 W → 綠燈「讓路中」，不通知。關掉：config.local.json 加 `"yield_gpu": false`。測試 `test_guard.py` 19 項。備份 `backup\portable_guard.before-giveway.py`、`test_guard.before-giveway.py`。

## 辦公室 2 與筆電（同一支 `laptop_miner.ps1`）

- 原始檔 `laptop\laptop_miner.ps1`（PowerShell 5.1，沒有 Python）；部署到各台 `C:\Users\pjunm\Pearl-mining\`，改完 scp 覆蓋，下次啟動生效。
- 各台設定 `laptop-config.json`（原始檔：`office2\laptop-config.json`、`laptop\laptop-config.json`）可覆寫：`miner`、`status_file`、`memory_mhz`、`start/max/min_ceiling_mhz`、`slow_at_c`、`stop_at_c`、`cool_at_c`、`cpu_max_percent`、`fan_min_percent`（NVML 控風扇）。
- 辦公室 2：顯存 5001、start 1200／max 1500／min 800、76°C 降／82°C 停、風扇最低 50%、狀態檔 `miner-status.json`（SRBMiner 1500 MHz＝69 TH/s／121 W／59°C）。筆電：顯存 6001、start 900／max 1100、74°C 降／80°C 停／88°C 關機、拔電停。
- 啟動：`Invoke-CimMethod Win32_Process Create` 跑 `cmd.exe /c powershell -File laptop_miner.ps1 >> guard-console.log 2>&1`（**附加寫入**，保留上一輪停止原因）。停止：建 `STOP` 檔。礦工結束或 3 分鐘沒動靜 → 保護程式自己重開礦工（一小時超過 5 次才停；11/01 辦公室 2 SRBMiner「Internal error 0x2007」自我重啟被當成結束而停機）。保護程式本身停了，由狀態服務的 `Keeper` 重新開始（見下）。備份 `laptop_miner.before-keep.ps1`（兩台機器上與本資料夾）。
- 踩過的坑：PowerShell 變數不分大小寫（`$Miner` 會蓋掉 `$miner` 行程）；狀態檔 `Move-Item` 撞上彙整服務讀檔會丟例外，已改重試；顯卡偶爾掉進 P5（顯存 810），60 秒後自動重啟礦工；ZeroTier 是公用網路，防火牆規則要 `-Profile Any`。
- 辦公室 2 驅動要 ≥610（516.94 時 krig 報 rc=-30）；SSH 開通腳本 `office2\ENABLE-SSH-FOR-OFFICE.ps1`。

## 彙整服務（`office_aggregator.py`）

- 設定 `aggregator.json`：`office`、`rtx`、`laptop`、`office2`、`vcu118` 各一段；`control.machines` 決定 App 能開停哪幾台；`temperature_limits`（office 82、rtx5080 75、laptop3070 80、office2 82、vcu118 85）；`advice.machines` 的 hashrate_th／gpu_w 用實測值（筆電 43.5／61、5080 208／222，整機 380 W）。備份 `aggregator.json.bak-before-*`。
- 開停、緊急殺礦工、節點看守都認 `krig-miner` 與 `SRBMiner-MULTI`。測試 `python test_office_aggregator.py`（141 項，不要加 `-I`）。
- **自動重開挖 `Keeper`（2026-11-01，使用者：每一台能自動重開挖最好）**：`keep_mining.machines`＝office、office2、vcu118。每 30 秒看 feed：連得到、已停止、停止原因不是「使用者要求」（手機／控制台／STOP 檔／`mining.stopped`）→ 等 60／300／900／1800 秒（連續短命越等越久，挖滿 30 分鐘重置）後送跟手機一樣的「開始」。兩分鐘內的顯卡讀數要 <60°C；沒有新讀數就靠各台保護程式自己的開機溫度檢查。連續 3 次撐不過 30 分鐘發一則警告。5080 有自己的看守（`PearlAutoStart`）、筆電歸電價自動化（`AutoMiner`），不在名單。紀錄在 `aggregator.log` 的 `keep-mining:` 行。關掉：拿掉 `keep_mining`。備份 `office_aggregator.before-keeper.py`、`aggregator.json.bak-before-keeper`。
- 重啟：殺命令列含 `office_aggregator.py` 的 pythonw，再用 `Invoke-CimMethod Win32_Process Create` 跑 `pythonw.exe -I <完整路徑>\office_aggregator.py`。

## 斷網：等網路回來，不停機（2026-10-29）

每台各自連礦池（公司一組網路：office、辦公室 2、FPGA；家裡一組：5080、筆電），office 斷線不會讓家裡停挖。舊版保護程式會把斷網當成礦工壞掉而停機（office 15 分鐘、5080 約 2 分鐘的回報停滯、辦公室 2／筆電 90 分鐘），停了不會自己重開。

- 現在：份額或回報逾時那一刻，先用 TCP 連三個 Kryptex 節點（:8048）。都連不上＝斷網，不停機、不重啟，每分鐘重測；連得上時給礦工一個全新的逾時窗口。網路通卻沒份額才照舊停機。SRBMiner 斷線時不會退出、算力 0、顯卡閒置，回來後自己重連。
- 程式：office `guarded_miner.py` 與 5080 `portable_guard.py` 的 `NetworkWait` 類別（各有單元測試：`test_network_wait.py`、`test_guard.py` 新增 3 項）；`laptop_miner.ps1` 的 `Test-PoolReachable` 與 `$netDown`。
- 測試用設定（平常不要留著）：`laptop-config.json` 的 `pool_nodes`、`share_restart_minutes`；5080 `config.local.json` 的 `pool_nodes`。實測方法：在該機器跑一個 127.0.0.1:8048 → prl-hk:8048 的轉接器（TLS 可直接穿過），`pool_nodes` 設 `["127.0.0.1"]`，殺掉轉接器＝斷網。
- 實測結果：辦公室 2 斷 9 分鐘（舊版 6 分鐘就停）、5080 斷 4.5 分鐘（舊版 2 分鐘就停），都沒停機；接回後同一個礦工程序 1 分鐘內出新份額。office 只有單元測試（程式與 5080 同一套）。
- 筆電沒做「office 斷線時自己照尖離峰開關」：換 SRBMiner 後尖峰也賺（每小時收益約 NT$1.4、電費約 NT$0.4），office 斷線時繼續挖反而好。
- 備份：各機 `*.before-netwait.*`（5080 在 `backup\`）。

## 新機器加入的步驟

1. 使用者裝好 Windows＋NVIDIA 驅動，跑 SSH 開通腳本（只准 office、只收金鑰）。
2. 使用者加 Defender 排除挖礦資料夾。
3. 我傳 krig（備援）與 SRBMiner、`laptop_miner.ps1`、`laptop-config.json`（新 worker 名稱、保守時脈），試挖看溫度，顯存先試 5001。
4. `aggregator.json` 加一段（照 `office2`），加進 `control.machines`、`earnings.workers`、`electricity_billed`、`temperature_limits`、`node_watch.machines`，`main()` 的 SSH 機器清單也要加 section 名稱，重啟彙整服務。
