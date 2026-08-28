# Skills 備份

自己寫的 Hermes skill 放這裡。**官方內建的不要放** —— 重裝 Hermes 會自己帶回來，
備份它們只會在還原時衝突。

本機位置：`%LOCALAPPDATA%\hermes\skills\`

## 用法

```powershell
.\_sync.ps1            # 備份：本機 -> 這裡（改過 skill 後跑）
.\_sync.ps1 -Restore   # 還原：這裡 -> 本機（重灌後跑）
.\_sync.ps1 -List      # 看目前備份了哪些
```

還原後**要重開 Hermes** 才會載入。

## 加入新的 skill

`_sync.ps1` 只同步「這個資料夾裡已經有的」。要納入新的：

1. 手動複製一份過來，保持相同的分類路徑
   （例如 `embedded/我的新skill/`）
2. 之後 `.\_sync.ps1` 就會自動跟著更新

這樣設計是為了不把官方那 80 幾個一起掃進來。

## 目前備份的

| Skill | 內容 |
|---|---|
| `embedded/stm32h7s78-dk` | STM32H7S78-DK 的架構與踩過的雷（LTDC、XIP、SWD mode=0）|
| `embedded/embedded-ui-verification` | 嵌入式 UI 自我驗證：抓 framebuffer、轉 PNG、模擬觸控 |
| `devops/android-headless-build-verify` | 無頭 Android 建置與模擬器驗證（彈珠台專案產出）|
