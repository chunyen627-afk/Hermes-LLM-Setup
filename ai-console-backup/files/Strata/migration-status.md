# Strata 與 Hermes 遷移紀錄

更新日期：2026-10-07。聊天後端已切換為 OrcaRouter Qwen3.8-Flash-Next-Uncensored IQ3_XXS。27B 的兩份聊天權重與兩個草稿模型先前已刪除，實際回收 37.02 GiB。使用者後續明確授權「授權現在刪除這 5 個舊聊天模型」，並接受 Hermes 桌面畫面驗收尚未完成；其餘五個舊聊天模型已刪除，另實際回收 79.21 GiB，兩次合計約 116.23 GiB。

## 桌面入口與登入預設

| 入口 | 模型配置 | 媒體配置 |
|---|---|---|
| 模型＋靜態生圖 | GPU 0、1、2；262,144 tokens；專家 RAM 預算 36 GiB（實際約 32.45 GiB）；KV streaming 32K | GPU 1 分享預留顯存；ComfyUI 8188、代理 8003、生圖頁面 5000 |
| 模型＋生影片 | GPU 0、2；32,768 tokens；20 GiB 專家 RAM 快取 | GPU 1 執行 H3；ComfyUI 8189 |

2026-10-08 改為手動啟動：桌面只保留一個「本機 AI 控制台」捷徑，移除本模型的 Startup 捷徑。開機不自動載入，開啟控制台先待機，由使用者按模式按鈕。舊 Qwen38-GPU-Server 排程維持停用。控制台讀取 Strata /metrics.live 顯示目前 TPS（後端近 2 秒窗口）與本輪平均，約每 1.5 秒更新；输入讀取中、待機、停止與資訊無法取得時分別顯示狀態，不保留過期 TPS。服務集中在 Windows Job Object 背景執行，停止全部／關閉控制台會終止其全部子程序；最小化保持執行，不自動打開瀏覽器。沒有為測試而重新開機。

262,144 是實際 GGUF 的原生上下文上限，已載入並測試媒體共存；沒有測滿 256K 的長提示詞。三卡模式主要提高可用容量，不能推論三卡會按比例加速。

兩種模式均啟用本模型自己的原生視覺編碼器，在 CPU 執行，沒有另外載入舊看圖模型。橋接器仍使用 1234 → 8001，保留相容處理、8192 輸出上限與簡單輪替 LOG，沒有自動派工。服務可透過控制台停止，停止後不會自動在背景重開。

## 2026-10-08 真正 MTP 與雙卡配置調校（目前配置）

先前只有 --spec 4，並未提供 --mtp 草稿權重路徑；mtp_max=4 不能當作 MTP 已載入的證据。本次使用上游工具下載官方 MTP 區塊，31 個張量 SHA256 驗證通過，轉成 Q2_0 runtime，保存在 C:\Users\pjunm\Strata-data\mtp\rt。含中文的 draft_vocab 為 106,299 tokens。兩個正式配置均加入 --mtp ../Strata-data/mtp/rt 與 --spec-min-p 0.7；啟動 LOG 確認 draft layer loaded（835 MiB）及 draft head（178.4 MiB）。引擎仍為 0.1.40，主模型、原生視覺與 GPU 功率未變更。

靜態模式維持三卡、262,144 CTX、36 GiB 專家 RAM 預算、32K 常駐 KV。影片模式維持 GPU [0,2]、32K CTX、20 GiB RAM 預算，新增 trim-stage-weights，layer_split 從 auto 的 42/6 層改為 20/28，專家顯存快取約由 4,200 增至 5,500 slots。GPU 1 保留給 H3。

| 測試 | 同日調整前 | 最終正式設定 | 範圍 |
|---|---:|---:|---|
| 靜態模式三類短輸入中位數 | 23.1 TPS | 31.15 TPS（+35%） | 程式碼／中文／英文，各兩次，256 輸出 tokens、thinking off |
| 靜態模式程式碼 | 26.1～31.1 | 36.8～40.7 TPS | 同一提示詞 |
| 靜態模式中文 | 22.9～23.3 | 30.0～32.3 TPS | 同一提示詞 |
| 靜態模式英文 | 20.3～20.4 | 27.4～29.0 TPS | 同一提示詞 |
| 1,106 tokens 程式碼輸入 | 30.2 TPS | 35.3 TPS | 冷讀 14.27→15.16 秒 |
| 16,485 tokens 程式碼輸入 | 27.1 TPS | 34.1 TPS | 冷讀 89.92→95.39 秒 |
| 影片模式三類短輸入中位數 | 7.7 TPS | 17.1 TPS（約 2.22 倍） | 各一次；包含 MTP、層分配與權重釋放的共同效果 |

最終設定的驗證：原生看圖正確讀出 AQUA-731；1024×1024 靜態圖與聊天共存；真正 Hermes 桌面代理完成 terminal 工具呼叫及結果回讀（79.66 秒）。H3 實際生成 512×512、73 幀、3.04 秒影片，790,820 bytes，影片 73 幀／音訊 96 幀完整解碼通過；生成期間獨立聊天回覆 VIDEO_CHAT_OK（6.09 秒）。短回覆只證明共存，不能推論生成媒體時仍有上述 TPS。

原始數據：tps-before-mtp-20261008.json、tps-image-mtp-final-20261008.json、context-tps-before-mtp-20261008.json、context-tps-image-mtp-final-20261008.json、tps-video-before-mtp-20261008.json、tps-video-mtp-balanced-20261008.json。功能紀錄為 *verification-mtp-final-20261008.json；H3 工作紀錄為 ComfyUI-H3/user/default/h3_jobs/mtp-validation-4b3d2e12.json。原配置備份在 C:\Users\pjunm\.cache\hermes-strata-backups\before-mtp-20261008。

以上為生成階段測速，不是整項開發任務速度；冷讀未明顯改善，首次約一萬 tokens 的 Hermes 系統／工具說明仍需等待。未測滿 256K、未達 70 TPS、未做長期穩定性保證。本次原生 UI 工具無法啟動，已驗證真正桌面代理後端，但沒有聲稱完成 Hermes 視窗點擊或成品呈現驗收。開機仍不自動載入，桌面仍只有一個控制台入口。

## 2026-10-07 TPS 調校（歷史配置）

使用者允許縮小上下文，後續目標改為「日常 1～16K 實際內容約 20 TPS，找出最大 CTX」。實測後保留原生上限 262,144；不是滿 256K 內容的速度承諾。

靜態模式採用 GPU [1,0,2]、層切點 14,28、trim-stage-weights、prefill 1024、host-core last、pcie-frac 0、int8 KV 與 kv-resident 32768。各卡只保留自己層的稠密權重，GPU 專家快取約 17,768 MiB／8,563 slots；超出常駐區的上下文快取放在 RAM。沒有換模型量化，也沒有調整顯卡時脈／功率。影片模式的模型配置仍維持原先雙卡／32K，不能套用此次三卡 TPS。

固定三個短輸入、每次 256 輸出 tokens、thinking off：舊配置中位數 11.5 TPS；新 256K streaming 配置為 23.2 TPS（各案例 25.2、23.2、20.5，合併速率 22.81）。32K／64K／128K／一般 256K 等候選結果保存在 tps-*.json；192K 僅建立候選檔，沒有執行，不計入結果。推測門檻 0.7 與較高 PCIe share 沒有測得改善，因此未採用。

控制台整合後最後一次重新啟動的複測：tps-managed-final.json 三個案例為 21.1／19.7／18.0 TPS，中位数 19.7、合併 19.53 TPS。速度依內容和執行狀態有波動，不能把先前 23.2 的中位數當作固定保證，亦不宣稱每一輪都至少 20。final-local-setup-verification.json 確認最後已恢復 Image／256K、五個服務 listener 均屬控制台、單一模型程序、配置參數與受測候選相同，原生視覺仍啟用。

實際程式碼輸入測試 context-tps-ctx256-stream.json：1,106 tokens 輸入，生成 256 tokens，24.7 TPS，冷讀 15.76 秒；16,485 tokens 輸入，生成 256 tokens，27.1 TPS，冷讀 102.90 秒。輸入長度、文字類型、快取與工具／推理流程仍會影響速度，TPS 不等於可見中文字數或整項開發任務的完成時間。上述測試沒有同時執行生圖。

static-coexistence-verification-tps256.json：256K 配置實際生成 1024×1024 圖片並同時回覆 IMAGE_CHAT_OK，27.56 秒完成。此短回覆只有 4 tokens，不能用來聲稱生圖期間也能保持 20 TPS。

正式配置重啟後：hermes-tool-verification-tps256.json 的實際桌面代理完成 terminal 工具來回，81.06 秒；首輪 10,077 tokens 冷讀 66.28 秒、生成 23.6 TPS；後續短輪 28 tokens 為 19.4 TPS。native-vision-verification-tps256.json 正確讀取圖片代碼 AQUA-731 及形狀／顏色，17.27 秒。不是 Hermes 桌面畫面的點擊驗收。

控制台已透過真正 Windows 視窗截圖檢查，實際點擊紅色停止按鈕：原本 18 個所屬程序全部結束，8001／1234／8188／8189／8003／5000 無 listener，GPU 0／1 使用量降至 0 MiB，GPU 2 保留系統桌面約 557 MiB。影片模式亦達到 32K 模型＋H3 已就緒，三個 listener 均屬於控制台；實際點擊視窗關閉按鈕後，該組 27 個程序全部結束、六個服務 port 全關閉。media-control-verification.json 記錄實際 UI 驗證；control-lifecycle-verification.json 另驗證 Windows job 的巢狀子程序繼承與關閉即終止。停止不刪除模型、圖片、影片、對話或專案檔案。

啟動器現在分開判斷媒體模式與 CTX，依所選 JSON 回報真實 context，保留 8K 桌面輸出上限與原生視覺。修正載入階段尚未開 port 時重複啟動的問題，兩個 RUN 入口實測拒絕重複啟動，僅有一個 Strata 引擎。重新載入模型前會卸載閒置靜態生圖權重，避免重複扣除已占用及預留顯存。tuned-profile-verification.json 確認實際桌面代理建構與四種媒體／CTX 組合正確。標準 pytest runner 因現有環境缺 pytest 無法啟動，未聲稱完整 pytest 通過。

## 先前完成的驗證（調校前）

- 兩個主模型檔合計 85,202,668,032 bytes，自己的 F16 視覺編碼器 907,543,296 bytes；三檔 SHA256 均符合來源。
- Strata 0.1.40、固定版本 llama.cpp、獨立環境；原生 GGUF 專家映射與 BF16 相容轉換已完成，沒有重複建立大型 experts.bin。
- 真實 API：算術、中文多輪、實際工具來回、串流、取消及恢復，8 項通過。
- 兩卡／三卡短提示詞比較：解碼中位數約 10.1／9.9 tokens/s；沒有明顯的三卡解碼加速。
- 原生視覺 API 正確辨識色塊與圖片代碼 AQUA-731；Hermes 桌面版實際代理正確讀出另一張圖的 STONE-864，使用主模型原生圖片輸入。
- 靜態圖配置：3 GPU／256K 完成 1024×1024、10 步圖片，約 21 秒；生圖期間取得獨立聊天回覆。
- 模式切換：停止核對身分且閒置的服務，重新載入 2 GPU／32K 與原生視覺；H3 專用 GPU 1，沒有重複啟動。
- Hermes 桌面代理：讀 H3 技能、執行本機工具、寫新請求、將簡單中文動作整理成英文、提交並等待完成、交付真實 MP4 路徑。新影片為 512×512、73 幀／3.04 秒、8 步、211,657 bytes；影片與音訊完整解碼通過。
- 第二支 H3 與獨立聊天共存：聊天回覆 VIDEO_CHAT_OK；另一支新影片 219,010 bytes，影片與音訊完整解碼通過。
- DOCX 更新後 15 頁、9 張操作截圖，逐頁視覺檢查完成；另有桌面 Hermes-H3 自動生影片操作.md 及 Hermes 的 h3-local-video 技能。

## 速度與桌面驗收範圍

Hermes 包含約一萬 tokens 的系統與工具說明，首次或換對話時读取較慢。影片代理完整流程約 687.92 秒，包含工具說明、多輪推論及佇列等待，不是單純 H3 生成時間。共存壓力測試 RAM 曾約 59 GB／64 GB。獨立聊天等待約 175.59 秒，當時前面有其他推論請求；這些測試不代表日常使用已流暢。

已關閉額外占用佇列的自動標題生成。實測證明上述設定能完成工作，不等同長期穩定或更大影片／滿 256K 輸入保證。

已測真實桌面代理建立流程，沒有冒稱已測桌面畫面。當前工具無法操作 Hermes 原生視窗，已請使用者自行重開 Hermes，在新對話中測試簡單圖片轉影片，確認完成訊息與影片路徑真的顯示。桌面視窗、附件按鈕與成品呈現仍待驗收。清理預設仍要求桌面驗收；此次使用 cleanup-authorization-20261007.json 記錄使用者對指定五個檔案的後續明確授權，驗證檔案清單完全相符後才刪除，沒有建立虛假的桌面驗收通過紀錄。

推理設定建議：小工具開發先用 Low；難題改 Medium；日常聊天與媒體派工可關 Thinking。Strata 支援 none、low、medium、high；high／xhigh／max 映射為 xhigh，minimal 映射為關閉，ultra 不在目前後端支援範圍。這是依本機設定與來源核對的使用建議，沒有聲稱做過程式品質比較。

2026-10-07 晚間，使用者在真正 Hermes 桌面對話開發小遊戲的觀測：冷讀 10,110 tokens 約 95.74 秒、生成 290 tokens 約 22.36 秒；同對話後續快取 10,105／10,242 tokens，完整 API 約 18.9 秒。生成速度依請求約 11.2～14.7 tokens/s，含推理與程式工具參數，不等同可見中文字速度。三卡短取樣平均 GPU 0／1／2 約 39.5／33.2／41.0%；實際分層為物理 GPU 1 的 0～3、GPU 0 的 4～30、GPU 2 的 31～47。GPU 1 的低時脈當下回報 Idle，沒有熱降頻或功率限制事件，不能僅因低時脈判定故障。硬體為 DDR4-3200、Samsung 970 EVO Plus NVMe；專家 RAM 快取 32 GiB，仍有檔案回退讀取。兩個被截斷請求分別記錄讀取約 28,238.0／66,574.7 MB 模型資料，須區分讀取總量與實際瞬時 SSD 吞吐。

此次已證實原桌面 profile 的 2,048 tokens 上限不適合完整程式工具呼叫：一輪約 144.5 秒只有推理便截斷，另一輪約 187.7 秒 write_file JSON 截斷並觸發重試。已將新 Strata 模型兩種模式的上限改為 8,192；舊 27B 相容條件維持 2,048。8 個既有測試案例的斷言直接執行通過（現有環境沒有 pytest，未冒稱 pytest 執行成功），真正桌面 _make_agent 建構驗證也確認 max_tokens=8192、Low 設定保留。desktop-profile-budget-tests.json 與 desktop-output-budget-verification.json 記錄驗證。沒有重啟使用者正在進行的對話，現有桌面程序需重開才能載入修正；未聲稱已提高 TPS 或遊戲已完成。Thinking 的 Low 只提供推理指引，沒有硬性推理 token 限制；簡單遊戲可關閉 Thinking 比較端到端耗時。

可後續比較的引擎選項：保留原生 256K 上下文，測試 --kv-resident 32768 與較大 prefill chunk 以減少顯存占用／冷讀耗時；須驗證三卡、靜態圖共存與 RAM 餘量。尚未啟用、未做 A/B，不能把其他硬體的官方增速直接當成本機成效。沒有加開推論、變更時脈、取消本輪或留下無期限監測；monitor_desktop_game.py 每次最多 45 秒，採樣資料在 desktop-game-performance-20261007.jsonl。

## 清理與保留

model-cleanup-plan.json 記錄原本 9 個舊聊天／草稿模型、約 116.23 GiB，現已全部移除。model-cleanup-27b-result.json 記錄先前四個 27B 檔案；model-cleanup-result.json 記錄此次五個檔案：Qwen 35B 的 IQ4_NL_XL、IQ4_XS、越獄 Q4_K_M，DeepSeek V4 Lite Q4_K_M，以及 Qwen Coder 30B UD-Q4_K_XL。此次實際回收 79.21 GiB；cleanup-assets-verification.json 核對 24 個保留資產的容量、實體檔案 ID 與修改時間均未改變，現用新模型仍載入且原生視覺啟用。27B 原有支援資料夾保留，因為橋接器仍使用其中的程式。

保留：新 Strata 模型、所有 mmproj／projector、Gemma 多模態資產、Qwen-VL 看圖模型、H3 模型與成品、靜態圖模型與成品、FunctionGemma 專案、使用者文件及雲端／網路檔案。刪除前核對絕對路徑、容量、實體檔案 ID 及執行中程序參照；僅刪指定權重，沒有遞迴刪資料夾。

參考：[Strata Orca 相容說明](https://github.com/Niko1221/Strata/blob/main/docs/ORCA.md)、[Strata 推理設定](https://github.com/Niko1221/Strata/blob/main/docs/DETAILS.md)、[模型原生視覺說明](https://huggingface.co/orcarouter/Qwen3.8-Flash-Next-Uncensored-GGUF#vision)。
