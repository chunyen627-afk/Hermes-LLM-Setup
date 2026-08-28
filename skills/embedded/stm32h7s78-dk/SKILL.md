---
name: stm32h7s78-dk
description: "STM32H7S78-DK firmware: XIP build, LTDC, DMA2D, GPU2D, flashing."
version: 1.0.0
author: pjunm
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [stm32, embedded, firmware, ltdc, dma2d, gpu2d, xip, display]
    related_skills: [embedded-ui-verification]
---

# STM32H7S78-DK 開發實戰

在這塊板子上做過的專案已經解掉下面這些坑。**動手前先看完，不要重踩。**

---

## 板子規格

| 項目 | 規格 |
|---|---|
| MCU | STM32H7S7L8H6H，Cortex-M7 @ 600 MHz |
| **內部 Flash** | **只有 64 KB** ← 決定整個架構 |
| 外部 Flash | 128 MB Octo-SPI NOR (MX66UW1G45G)，映射 `0x70000000` |
| 外部 RAM | 32 MB PSRAM (APS256XX)，映射 `0x90000000` |
| LCD | 5 吋 800×480 RGB565，RK050HR18 面板 |
| 觸控 | GT911 電容五點觸控，I2C1，位址 `0xBA` |
| 除錯器 | 板載 ST-Link V3 (`VID_0483 / PID_3754`) |

**內部 Flash 只有 64 KB 是最特別的地方。** 一個 800×480 RGB565 framebuffer
就要 750 KB，程式碼放不進內部 Flash。所以**必須用 XIP 架構**：
內部 Flash 放 bootloader，程式本體燒進外部 Flash 直接執行。

---

## 本機工具鏈路徑（已驗證可用）

```
GCC   = C:/ST/STM32CubeIDE_2.0.0/STM32CubeIDE/plugins/com.st.stm32cube.ide.mcu.externaltools.gnu-tools-for-stm32.13.3.rel1.win32_1.0.100.202509120712/tools/bin/arm-none-eabi-gcc.exe
QEMU  = C:/Program Files/qemu/qemu-system-arm.exe   (mps2-an500 = Cortex-M7)
IDE   = C:/ST/STM32CubeIDE_2.0.0/STM32CubeIDE/stm32cubeidec.exe
CLI   = C:/ST/STM32CubeIDE_2.0.0/STM32CubeIDE/plugins/com.st.stm32cube.ide.mcu.externaltools.cubeprogrammer.win32_2.2.300.202508131133/tools/bin/STM32_Programmer_CLI.exe
EL    = <CLI 同目錄>/ExternalLoader/MX66UW1G45G_STM32H7S78-DK.stldr
PIL   = 已裝（python -c "import PIL"）
```

**ST 相關東西都放在 `C:\ST\STM32CubeIDE_2.0.0`**（使用者慣例）。STM32CubeH7RS
韌體包 clone 到 `C:/ST/STM32CubeIDE_2.0.0/STM32CubeH7RS`，Calc/Tetris 專案在
`.../STM32CubeH7RS/Projects/STM32H7S78-DK/Templates/<Name>`。

terminal 工具走 **git-bash**，不是 PowerShell。用 POSIX 語法；原生 Windows
程式（python、gcc、IDE）的參數路徑要用 `cygpath -w` 轉成 Windows 格式，
否則 MSYS 路徑 `/c/Users/...` 會被當成 `C:\c\Users\...` 而找不到檔。

**已驗證可用的範本專案**：

```
C:/ST/STM32CubeIDE_2.0.0/STM32CubeH7RS/Projects/STM32H7S78-DK/Templates/Tetris
C:/ST/STM32CubeIDE_2.0.0/STM32CubeH7RS/Projects/STM32H7S78-DK/Templates/Calc
```

---

## 一、專案建置

**必須用 `Template_XIP` 當基底** —— ST 韌體包的
`Projects/STM32H7S78-DK/Templates/Template_XIP` 已經處理好 bootloader、
XIP 鏈結腳本、XSPI 記憶體映射。不要從零搭。

**專案不能隨便搬位置**
症狀：`No rule to make target 'C:/Middlewares/ST/...'`
原因：`.cproject` 用相對路徑 `../../../../../../..` 指向韌體包，
搬家後往上爬的層數就錯了。
解法：專案要放在 `cube/Projects/STM32H7S78-DK/Templates/<你的專案>` 這一層。

**submodule 一定要抓**
症狀：缺 `stm32_boot_xip.c`、`stm32h7rsxx_hal_ltdc.c`
原因：`STM32CubeH7RS` 把 HAL/CMSIS/BSP/GT911 都做成 submodule，
`git clone --depth 1` 不會帶下來。

```bash
git submodule update --init --depth 1 --recursive
```

**元件設定檔要自己補** —— Template_XIP 不含 `gt911_conf.h`、
`mx66uw1g45g_conf.h`、`aps256xx_conf.h`。

**路徑格式的雷** —— 給 Eclipse/CubeIDE 的路徑必須用 Windows 反斜線，
否則它把 `C:` 當成 URI scheme，報
`No file system is defined for scheme: C`。

**燒錄兩段式**

```
bootloader → 內部 Flash（只需第一次）
應用程式   → 外部 Flash，必須指定 external loader
```

**可直接複製的燒錄指令**（實測會動的版本，git-bash）：

```bash
CLI="C:/ST/STM32CubeIDE_2.0.0/STM32CubeIDE/plugins/com.st.stm32cube.ide.mcu.externaltools.cubeprogrammer.win32_2.2.300.202508131133/tools/bin/STM32_Programmer_CLI.exe"
EL="C:/ST/STM32CubeIDE_2.0.0/STM32CubeIDE/plugins/com.st.stm32cube.ide.mcu.externaltools.cubeprogrammer.win32_2.2.300.202508131133/tools/bin/ExternalLoader/MX66UW1G45G_STM32H7S78-DK.stldr"
ELF="<專案>/STM32CubeIDE/Appli/Debug/Template_XIP_Appli.elf"

"$CLI" -c port=SWD mode=UR -el "$EL" -w "$ELF" -v
```

⚠ **三個一定要注意的點**（2026-08-28 實測踩到）：

1. **燒 `.elf` 不要燒 `.bin`** —— ELF 自帶位址資訊，不用手寫 `0x70000000`。
   用 `.bin` 就必須自己給位址，寫錯或漏了都不會報錯，只是沒燒進去。

2. **`-el` 不能漏** —— 漏了會看到 `Flash size : 64 KBytes (default)`，
   那是**內部** Flash 的大小。外部 Flash 的寫入等於沒發生，
   但 CLI **不會報錯**，你會以為成功了。
   看到 64 KBytes 就知道外部載入器沒生效。

3. **`mode=UR`（Under Reset）燒錄用這個** —— 跟「讀記憶體用 mode=0」不衝突：
   燒錄要 halt 住 CPU 才能寫 flash，讀變數則不能 halt（否則永遠讀到初始值）。
   讀取用：`-c port=SWD mode=0 -hardRst -r32 <addr> <bytes>`
   （`-r32` 的 count 是**位元組數**，讀 4 個 32-bit 變數要給 16）

**燒完怎麼確認真的進去了**：

```bash
# 讀外部 flash 第一個字，應該是你的初始 SP（通常 0x20001000 之類）
"$CLI" -c port=SWD mode=0 -r32 0x70000000 4
```

值不對就是沒燒進去，不要往下做。

---

## 二、LTDC 顯示（這裡的坑最多）

**`BSP_LCD_Init` 預設是 ARGB8888 不是 RGB565**
症狀：畫面左右重複兩次、下半部彩色雜訊、顏色全錯
解法：明確指定像素格式。

**換 framebuffer 千萬不要用 `BSP_LCD_SetLayerAddress`**
症狀：畫面持續閃爍、出現橫向黑線
原因：它會連帶重設一堆 LTDC 暫存器
解法：**只寫 `CFBAR` 一個暫存器**。它是 shadow register，
寫入不會立即生效，會在下一次垂直消隱時才套用 —— 這正是我們要的。

**雙緩衝的索引很容易寫反**
症狀：畫面閃爍，但暫停時不閃
原因：交換後把繪圖目標指到「正在顯示」的那塊，雙緩衝等於失效
解法：用紙筆把狀態機跑幾格，確認「顯示的」和「繪製的」永遠相反。

**PSRAM 要設成 write-through**，而且**開機是隨機內容**（要先清）。

**直立顯示要自己做旋轉** —— 面板是實體 800×480 橫向，
要 480×800 直立就得在繪圖層轉。

---

## 三、DMA2D（Chrom-ART）

**根本問題是 CPU 讀取時的快取一致性**
症狀：畫面出現大範圍白色雜點（偶發、一整片同時出現）
原因：DMA2D 繞過 CPU 直接寫 PSRAM，但 **CPU 讀取仍會從 D-Cache 取值**
解法：讀取被 DMA2D 寫過的區域之前，先失效該範圍的快取。

**不要用 HAL 的 `PollForTransfer`**
它的等待迴圈包在 `if (CR & DMA2D_CR_START)` 裡面。小矩形傳輸太快，
進迴圈前 START 已經被硬體清 0，於是完全不等待就返回。
而 `HAL_DMA2D_Start` 從不清除 TC 旗標。
**正確做法**：直接輪詢 `while (DMA2D->CR & DMA2D_CR_START) {}` ——
START 由硬體在傳輸結束時清 0，比旗標可靠。

```c
MODIFY_REG(DMA2D->CR, DMA2D_CR_MODE, DMA2D_R2M);
MODIFY_REG(DMA2D->OPFCCR, DMA2D_OPFCCR_CM, DMA2D_OUTPUT_RGB565);
DMA2D->IFCR = DMA2D_IFCR_CTCIF | DMA2D_IFCR_CTEIF | DMA2D_IFCR_CCEIF;
/* ... 設定完啟動 ... */
while (DMA2D->CR & DMA2D_CR_START) { }
__DSB();   /* 確保 DMA2D 的寫入對後續 CPU 存取可見 */
```

**混用硬體與 CPU 繪圖必須同步**
症狀：某區域的小圖案不完整而且會閃，其他區域正常
原因：填色是非同步的（發出傳輸後函式就返回），
隨後用 CPU 寫同一區域就會打架
解法：繪圖層設一個同步點，CPU 寫 framebuffer 前先等硬體停下來。

**小矩形用 CPU 反而快** —— DMA2D 每次都要設四個暫存器再啟動，
設定成本大於傳輸成本。實測門檻約 1024 像素。
真正要發揮 DMA2D 應該「一次傳輸畫一大塊」而不是「每個小方塊一次」。

---

## 四、GPU2D（NeoChrom）—— 還沒用過但這顆有

`GPU2D_BASE` 存在，HAL 有 `stm32h7rsxx_hal_gpu2d.c`。

那是 **NeoChrom GPU**，能力遠超 DMA2D：

| 單元 | 能力 |
|---|---|
| **GPU2D** | 任意角度旋轉、縮放、**透視正確的紋理映射**、抗鋸齒、2.5D |
| DMA2D | 只能矩形搬運與混色 |
| GFXMMU | 非矩形螢幕省記憶體（最多 20%）|

**GPU2D 和 DMA2D 可以同時運作**，各自接受繪圖任務。

**做賽車這類需要旋轉縮放透視的遊戲時應該用 GPU2D**，
不要用掃描線逐行縮放的老方法 —— 那等於放著硬體不用。

⚠ ST 社群有 "GPU2D trouble on STM32H7S78-DK" 的討論串，代表有人踩過坑。
卡住時先確認：時脈開了沒、命令佇列的記憶體位置對不對、
cache 一致性有沒有處理（跟 DMA2D 同一類問題）。

---

## 五、效能量測

用 DWT cycle counter 在板子上實測（600 MHz）：

```c
CoreDebug->DEMCR |= CoreDebug_DEMCR_TRCENA_Msk;
DWT->CYCCNT = 0;
DWT->CTRL |= DWT_CTRL_CYCCNTENA_Msk;
uint32_t t0 = DWT->CYCCNT;
/* ... 要量的程式 ... */
uint32_t cycles = DWT->CYCCNT - t0;
```

**不用接 UART 就能讀出數值**：

```bash
arm-none-eabi-nm app.elf | grep <變數名>              # 查位址
STM32_Programmer_CLI -c port=SWD mode=HOTPLUG -r32 <位址> 1
```

**已量到的基準**（俄羅斯方塊那種小面積更新）：

| 項目 | CPU 繪圖 | DMA2D |
|---|---|---|
| 每格繪製 | 9.87 ms | 8.94 ms |
| 等待垂直消隱 | 5.71 ms | 6.63 ms |
| 幀率 | 64 fps | 64 fps |

**兩者幀率相同，因為瓶頸是面板的 60 Hz，不是繪製速度。**
全畫面 800×480 的遊戲完全不同，要自己重新量。

---

## 六、資源用量

| 項目 | 大小 |
|---|---|
| 遊戲本體（邏輯+繪圖+UI+輸入+字型）| 16.4 KB Flash / 296 B RAM |
| 中文字型（74 字，可共用）| 5.6 KB |
| HAL + BSP 基礎建設（共用）| 約 145 KB |
| Framebuffer ×2（共用）| 1.5 MB PSRAM |

外部 Flash 有 128 MB，純程式類 2D 遊戲每款約 10~20 KB。
**容量不是限制，時間才是。**

---

## 七、測試策略

**不需要板子就能測** —— QEMU `mps2-an500` 可以跑核心邏輯，
`./scripts/test.sh` 一鍵執行。

**畫面正確性要用看的** —— 把 framebuffer 存成 PNG 檢查版面。
純數學測試看不出「上下顛倒」這類錯誤。
詳見 `embedded-ui-verification` skill。
