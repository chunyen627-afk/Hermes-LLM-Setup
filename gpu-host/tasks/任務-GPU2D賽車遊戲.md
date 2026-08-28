# 任務：STM32H7S78-DK 上的 GPU2D 加速賽車遊戲

在 STM32H7S78-DK 上做一款 2.5D 賽車遊戲，**必須使用 NeoChrom GPU2D**，
不是只用 DMA2D 或 CPU 畫。

---

## 為什麼指定 GPU2D

STM32H7S7L8 上有三個獨立的繪圖單元，能力完全不同：

| 單元 | 能力 | 你做過嗎 |
|---|---|---|
| **NeoChrom GPU2D** | 任意角度旋轉、縮放、**透視正確的紋理映射**（perspective correct texture mapping）、2.5D | ❌ 從沒碰過 |
| DMA2D (Chrom-ART) | 矩形填色、blit、格式轉換、alpha 混色 | ✅ 但只用了 R2M 填色 |
| GFXMMU (Chrom-GRC) | 非矩形螢幕省記憶體 | ❌ 沒用 |

**GPU2D 和 DMA2D 可以同時運作**，各自接受繪圖任務。

賽車遊戲的道路透視正是 GPU2D 的 perspective correct texture mapping 要解決的問題。
如果你用掃描線逐行縮放的老方法（Pole Position 那種），等於白白放著硬體不用 —— 不要那樣做。

---

## 已知的起點（不要重新摸索）

**參考專案**（同一塊板子，已驗證可跑）：
- 俄羅斯方塊：`C:\Users\pjunm\tetris_h7s78`
  （GitHub: https://github.com/chunyen627-afk/stm32h7s78-tetris）
- 計算機：`C:\Users\pjunm\OneDrive\Desktop\stm32h7s78-calc`

**必讀**：`stm32h7s78-calc\docs\stm32h7s78-notes.md`
那份筆記已經解掉這些坑，直接沿用不要重踩：
- LTDC 設定與閃爍問題
- DMA2D 不能用 HAL 的 PollForTransfer（TC 旗標的雷）
- 專案建置流程、外部 Flash 燒錄要指定 external loader
- 中文字型的產生與檢查工具

**已量測的效能基準**（DWT cycle counter @600MHz）：

| 項目 | CPU 繪圖 | DMA2D |
|---|---|---|
| 每格繪製 | 9.87 ms | 8.94 ms |
| 等待垂直消隱 | 5.71 ms | 6.63 ms |
| 合計 | 15.58 ms | 15.57 ms |

那是俄羅斯方塊那種小面積更新的數字。**全畫面 800×480 的賽車完全不同**，
你必須自己重新量。

**資源狀況**：外部 Flash 128 MB（目前只用 60 KB），Framebuffer ×2 已佔 1.5 MB PSRAM。
容量不是限制，**時間才是**。

---

## 第一步：先做時間預算分析，不要急著寫程式

動手前先回答這些，寫進 `docs/budget.md`：

1. 800×480 @ 60 FPS，每幀 16.67 ms，每像素平均能花幾個 CPU cycle？
2. 一幀要畫哪些東西？（天空、道路、路邊物件、HUD、車子）各佔多少面積？
3. 哪些交給 GPU2D、哪些給 DMA2D、哪些 CPU 直接寫比較快？
   （注意：筆記裡量到「小矩形用 CPU 反而快，DMA2D 設定成本大於傳輸」）
4. GPU2D 和 DMA2D 並行時，記憶體頻寬會不會變成瓶頸？

**這一步做完再開始寫程式。** 沒有預算分析就寫，最後跑不到 60 FPS 會很難回頭改架構。

---

## 功能需求

**必須有**：
- 透視道路，會左右彎（曲率隨距離累積）
- 車子可左右操控，會受道路曲率影響
- 路邊物件（樹、告示牌之類）有正確的深度縮放與遮擋順序
- 速度感（道路紋理捲動）
- HUD：速度、時間或圈數
- **穩定 60 FPS，畫面不撕裂**

**加分**：
- 上下坡（地平線起伏）
- 對手車
- 賽道有終點與計時

**不要做**：
- 音效（板子上沒接喇叭）
- 存檔（沒必要）

---

## 驗收標準

依序驗證，每一項都要有證據：

1. **編譯通過** —— 沒有 warning（`-Wall -Wextra`）
2. **時間預算分析** —— `docs/budget.md` 有數字，不是空話
3. **實測 FPS** —— 用 DWT cycle counter 量，寫進筆記。
   低於 60 就要說明瓶頸在哪、試過什麼
4. **無撕裂** —— double buffering + LTDC line interrupt，
   說明你怎麼確認沒撕裂的
5. **可操控** —— 車子會動、會受曲率影響、不會開出道路外
6. **GPU2D 真的有用到** —— 說明哪些繪製走 GPU2D、
   跟純 CPU 版本比較快多少（要有數字）

---

## 工作方式

- **每寫完一個模組就編譯一次**，不要累積到最後才編
- 純邏輯的部分（道路曲率、深度排序、碰撞）寫測試，
  參考計算機專案的 `test/` 和 `scripts/test.sh`
- 畫面正確性可以把 framebuffer 存成 PNG 檢查
  （計算機專案的 `tools/raw2png.py` 可以直接用）
- 卡住超過三次的問題，寫進 `docs/` 說明你試了什麼、為什麼不行，
  不要無聲繞過去
- 做完把這個專案學到的東西存成 skill，之後其他專案可以用

---

## 一個提醒

GPU2D 在這塊板子上有人踩過坑
（ST 社群有 "GPU2D trouble on STM32H7S78-DK" 的討論串）。
如果卡住，先確認：時脈有沒有開、命令佇列的記憶體位置對不對、
cache 一致性有沒有處理（GPU2D 寫的記憶體 CPU 要看得到，反之亦然）。

計算機專案裡 DMA2D 的記憶體屏障寫法可以參考。
