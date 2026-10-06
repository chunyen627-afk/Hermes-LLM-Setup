---
name: h3-local-video
description: 本機 H3 圖生影片。使用者附加圖片或給本機路徑，加一句簡單中文動作時，自動整理英文提示詞、提交、等待並交付 MP4。27B 看圖模型不用載入。
platforms: [windows]
---

# Hermes／27B 自動 H3 圖生影片

使用者只需啟動桌面「27B＋影片」，在 Hermes 傳圖片或貼完整本機路徑，說「讓它輕輕左右搖動，5 秒」。實際執行到交付 MP4；不要求完整提示詞，不再確認。不重啟 27B，不用雲端 video_gen，不開背景服務，不刪模型。

1. 圖片：取本次附件的 @image:、image_url: C:/... 或 path: C:/... 真實路徑。即使訊息說「看不到圖片」，H3 仍能用該檔案。不要 vision_analyze，不猜內容。多張無法判定才問用哪張；缺圖／檔案不存在才請附加。
2. 提示詞：把簡短中文動作整理成英文。可稱 the subject in the reference image，補保持外觀、連續鏡頭、無字幕；不添加未要求的動作或鏡頭。音訊可用 quiet natural ambience，指定對白保留原語言。
3. 請求：terminal 執行 date +%Y%m%d-%H%M%S 取得唯一時間，再用 write_file 寫 UTF-8 JSON 到 C:/Users/pjunm/ComfyUI-H3/user/default/h3_jobs/request-<時間>.json。內容包含 request_id（hermes-<時間>）、image_path、prompt_en、seconds（預設5）、steps（預設12），可選整數 seed。新工作用新 ID，重試沿用同一 ID。禁止照抄既有測試 ID。
4. 提交：terminal 執行下列命令，替換真實請求路徑：

```bash
"C:/Users/pjunm/ComfyUI-H3/.venv/Scripts/python.exe" "C:/Users/pjunm/AppData/Local/hermes/skills/media/h3-local-video/scripts/h3_video.py" submit --request "C:/Users/pjunm/ComfyUI-H3/user/default/h3_jobs/request-<時間>.json"
```

Hermes Windows terminal 用 Git Bash，正斜線路徑，不加 PowerShell 的 &。本機沒有 rtk，專用命令直接執行。terminal timeout=60、background=false。8189連線失敗才請使用者先開「27B＋影片」。

5. 等候：submitted 只代表已提交。告知已開始，反覆 terminal 執行下列命令直到 completed，每次自身結束：

```bash
"C:/Users/pjunm/ComfyUI-H3/.venv/Scripts/python.exe" "C:/Users/pjunm/AppData/Local/hermes/skills/media/h3-local-video/scripts/h3_video.py" wait --job "實際job_id" --timeout 40
```

running/queued/submitted 繼續等，每約一分鐘簡短報進度，不要求使用者自己查。busy 等待其他工作，不清空佇列。error 回報具體錯誤。unknown/submission_unknown 先 status --job "實際job_id" 查詢，不自動重送。對話關閉不停止已提交工作。

6. 交付：只有 completed 才說完成。工具已完整解碼影片與音訊、核對幀數尺寸。提供實際 output_path 及可點擊 view_url；可用 ![生成影片](C:/.../實際.mp4)。桌面未內嵌也能開啟下載。不能只交提示詞、捏造檔名。

工具保持原圖比例、補邊不拉伸、長邊512／32倍數。5秒對齊為124幀／5.17秒；秒數支援3～10、步數8～20。先前5秒12步約3～4分鐘。輸出 C:/Users/pjunm/ComfyUI-H3/output/video/，紀錄 user/default/h3_jobs/。27B用兩張GPU、H3用一張。完成後使用者可關閉可見H3視窗釋放GPU。
