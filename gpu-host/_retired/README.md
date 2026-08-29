# 已退役的東西

## _switch_mode.ps1 / _switch_mode.bat / 「切換 slot 模式」捷徑

2026-08-29 退役。原本用來在「獨佔 1 slot 200K」和「共享 2 slot 各 120K」
之間切換。

**現在固定 2 slot，不再需要切換。**

原因：視覺請求（vision_analyze）是另一個獨立請求，1 slot 時它得排隊等
主任務跑完 —— 實測 900 秒都等不到，必定 timeout。2 slot 下 21.9 秒完成。
只要還想讓模型自己看圖，就不能用 1 slot。

留著捷徑反而危險：誤點「獨佔」會讓視覺整個壞掉，而且症狀（timeout）
不會直接指向 slot 設定，很難聯想。

真的需要單 slot 超長 ctx 時，直接下指令：

```powershell
.\_ensure_38.ps1 -Ctx 204800 -Slots 1
```

`_ensure_38.ps1` 會自動同步 Hermes 的 context_length，不需要 switch_mode
額外做這件事。

## 開機啟動-27B-server.lnk（啟動資料夾）

2026-08-29 退役。它指向 `hermes\_舊版本_不要用\開機啟動-llama-server-27B.bat`，
而那個 bat 呼叫的 `hermes\_ensure_27b.ps1` **早就不存在了** —— 每次開機只會
跳錯誤，然後卡在 `pause` 等人按鍵。

而且就算它能跑，也會跟排程「Qwen38-GPU-Server」搶同一個 port 8001，
啟動的還是沒有 mmproj、沒有 2 slot 的舊設定。

開機啟動現在只由排程「Qwen38-GPU-Server」負責，它跑 `_bootmenu.ps1`。
