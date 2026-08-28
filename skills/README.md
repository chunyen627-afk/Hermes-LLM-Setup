# Skills 備份

自己寫的 Hermes skill 放這裡。**官方內建的不要放** —— 重裝 Hermes 會自己帶回來，
備份它們只會在還原時衝突。

本機位置：`%LOCALAPPDATA%\hermes\skills\`

## 用法

```powershell
.\_sync.ps1            # 備份：本機 -> 這裡
.\_sync.ps1 -Auto      # 備份 + 自動收錄新 skill + commit/push
.\_sync.ps1 -Restore   # 還原：這裡 -> 本機（重灌後跑）
.\_sync.ps1 -List      # 看目前備份了哪些
```

**已設每天 03:00 自動執行 `-Auto`**（Windows 工作排程器，名稱「Hermes Skill 備份」），
所以平常不用手動跑。要停就到工作排程器停用那個任務。

還原後**要重開 Hermes** 才會載入。

## 加入新的 skill

`-Auto` 會自己找出「本機有、這裡沒有」的自訂 skill 並收錄進來。

判斷標準是**內容有沒有提到這台機器專屬的東西**
（`STM32H7S78`、`pjunm`、`Qwen3.8`、`andriod`、`tensor-split`、`hermes_bridge`）——
官方那 80 幾個不會提到這些，所以不會被誤收。

如果新 skill 剛好沒提到這些關鍵字，`-Auto` 抓不到，手動複製進來一次即可，
之後就會持續同步。

## 目前備份的

| Skill | 內容 |
|---|---|
| `embedded/stm32h7s78-dk` | STM32H7S78-DK 的架構與踩過的雷（LTDC、XIP、SWD mode=0）|
| `embedded/embedded-ui-verification` | 嵌入式 UI 自我驗證：抓 framebuffer、轉 PNG、模擬觸控 |
| `devops/android-headless-build-verify` | 無頭 Android 建置與模擬器驗證（彈珠台專案產出）|
