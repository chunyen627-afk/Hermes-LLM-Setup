# Skills

給本地模型用的知識包 —— 把踩過的坑寫成規則，避免每次重新摸索。

## stm32-h7s78-dk

STM32H7S78-DK 開發板的已驗證知識，來自一個完整專案（觸控計算機）的實戰。

**為什麼需要**：本地模型做那個專案時走錯五個方向，每個都燒掉數小時。
最嚴重的是把 `mode=1`（Connect Under Reset）造成的假象誤判成
「iRoT secure boot 擋住映像」—— 那個問題根本不存在。

**內容**：硬體腳位/位址、五個坑的正確做法、燒錄指令、分階段 marker 除錯法。

---

## 安裝到 Hermes

複製整個資料夾到：
```
C:\Users\<你>\AppData\Local\hermes\skills\
```

確認：
```bash
hermes skills list
```

## 安裝到 Claude Code（本地模型用）

放進 plugin 目錄：
```
<你的資料夾>\local-plugin\skills\
```

啟動時帶 `--plugin-dir <你的資料夾>\local-plugin`

---

## 寫新 skill 的重點

**`description` 決定會不會被載入** —— 要寫「使用者會講的詞」，不是摘要。

```yaml
# ✅ 好
description: 在 STM32H7S78-DK 上做專案時用。含腳位、位址、燒錄指令、
  五個踩過的坑。當任務提到 H7S78、H7S7L8、這塊 Discovery 板時載入。

# ❌ 爛
description: STM32 開發相關知識
```

**內容要寫「正確做法」不是「錯誤紀錄」**：
```
❌ 我們曾經用錯 GPIO 位址
✅ GPIOD = 0x58020C00（AHB4），不是 0x48020C00
```
