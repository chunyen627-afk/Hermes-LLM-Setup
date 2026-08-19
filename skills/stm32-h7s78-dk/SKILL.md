---
name: stm32-h7s78-dk
version: 1.0.0
license: MIT
platforms: [windows]
description: 在 STM32H7S78-DK 開發板上做任何專案時用。含已驗證的腳位、位址、燒錄指令、以及五個已經踩過並解決的坑（SWD 連線模式、GPIO 位址、UART4 腳位、時鐘、LVGL v9）。當任務提到 H7S78、H7S7L8、這塊 Discovery 板、或要在上面燒錄/除錯時載入。
---

# STM32H7S78-DK 已驗證知識

**這些都是實際做過一個完整專案（計算機）驗證出來的，直接用，不要重新摸索。**

板子：STM32H7S78-DK（MCU: STM32H7S7L8）

---

## ✅ 已確認的硬體事實

| 項目 | 值 | 備註 |
|---|---|---|
| **LED1** | **PO1** | ST 官方 UM3289 |
| LED2 | PO5 | |
| **ST-Link VCP UART** | **UART4：PD0=RX / PD1=TX, AF8** | ⚠ **不是 USART2/PA2-PA3**（那是別的板子） |
| Baud | 115200 8N1 | |
| **GPIOD 基底位址** | **0x58020C00** | AHB4PERIPH_BASE(0x58020000) + 0x0C00 |
| 內部 flash（Boot） | 0x08000000 | |
| **外部 NOR（Appli）** | **0x70000000** | 用 XSPI |
| NOR loader | `MX66UW1G45G_STM32H7S78-DK.stldr` | ST 官方 |
| IROT_SELECT | 0x6A（OEM iRoT） | ST 文件說無法停用 |

---

## 🔴 五個已踩過的坑（每個都燒掉數小時）

### 1. SWD 連線模式 —— 最會誤導人的一個

**症狀**：讀 RAM marker 永遠是 0，看起來像「code 沒跑」「iRoT 擋住 boot」。

**真相**：`mode=1`（Connect Under Reset）**每次連線都會 halt-on-reset**，
把核心卡在 reset vector。每個 CLI 指令都是獨立連線 → 你 `-g` run 之後，
下一個指令重新連線又 halt → main() 永遠沒機會跑。

```bash
# ❌ 錯：每次連線都把核心 halt 在 reset 點
STM32_Programmer_CLI -c port=SWD mode=1 -r32 0x24000100 1

# ✅ 對：mode=0 不 halt 執行中的核心，而且一次連線做完多個動作
STM32_Programmer_CLI -c port=SWD mode=0 -hardRst -r32 0x24000100 1
```

**原則**：要觀察執行中的狀態，就用 **mode=0 + 單一連線內做完所有動作**。
跨連線讀取會重新 reset/halt 核心。

> 這個坑讓整個專案誤判成「iRoT secure boot 擋住我們的映像」，
> 實際上 chainload 一直是通的。

### 2. GPIO 位址寫錯 → BusFault → LOCKUP

```c
#define GPIO_D_BASE 0x48020C00   // ❌ 不是有效映射位址，一寫就 BusFault
#define GPIO_D_BASE 0x58020C00   // ✅ AHB4 週邊空間
```

**症狀**：CFSR=0x8200、HFSR LOCKUP。
H7RS 系列的 GPIO 在 **AHB4**（0x58020000），不是 H7 傳統的 0x48020000。

### 3. UART4 不是 USART2

從別的板子（Nucleo 等）抄來的 USART2/PA2-PA3 在這塊板子行不通。
**ST-Link VCP 走 UART4**，而且 AF 是 **8**。

需要在 Boot 跟 Appli **兩個 context** 都設好：
- `hal_conf.h` 啟用 `HAL_UART_MODULE_ENABLED`
- `hal_msp.c` 加 `HAL_UART_MspInit`
- `main.c` 加 `huart4` + `MX_UART4_UART_Init` + `__io_putchar`

### 4. reset 後的時鐘是 HSI 16MHz，不是 64MHz

**症狀**：UART 有輸出但終端機讀到亂碼或什麼都沒有。

BRR 要照**當下實際時鐘**算。reset 直後還沒跑 SystemClock_Config 時是 **HSI 16MHz**。
用 64MHz 去算 BRR 會變成約 461k baud，115200 的終端機讀不到。

### 5. AFR 暫存器的位元組位置

```c
// pin N 的 AFR 欄位：AFR[N/8] 的 bits[(N%8)*4 +: 4]
GPIO_D_BASE[8] = (0xFF << 8);   // ❌ 這是設 pin 2
GPIO_D_BASE[8] = (0x08 << 4);   // ✅ pin 1 = AF8
```

---

## 🖥️ LVGL v9 的兩個坑

### 1. 預設 layout 不是 grid

`lv_obj_create` 出來的容器**沒有 layout**，所有子物件會疊在原點
（畫面上只看得到最後一個）。

```c
// 方案 A：明確設 grid
lv_obj_set_grid_dsc_array(cont, col_dsc, row_dsc);

// 方案 B（實測較可靠）：絕對定位
lv_obj_set_pos(btn, x, y);
```

### 2. `lv_label_get_text()` 不能用在 button 上

```c
// ❌ 讀到垃圾記憶體 → 顯示亂碼（例如按鈕全變 'h'）
const char *s = lv_label_get_text(btn);

// ✅ 把值存進 user_data
lv_obj_set_user_data(btn, (void*)(uintptr_t)key_char);
char c = (char)(uintptr_t)lv_obj_get_user_data(e_target);
```

**編譯會過、程式會跑，但行為錯誤** —— 這種 bug 只能靠實際測試抓到。

---

## 🔧 燒錄流程

```bash
P="/c/Program Files/STMicroelectronics/STM32Cube/STM32CubeProgrammer/bin/STM32_Programmer_CLI.exe"

# Boot → 內部 flash
"$P" -c port=SWD mode=0 -w build/boot.bin 0x08000000 -v

# Appli → 外部 NOR（要指定 loader）
"$P" -c port=SWD mode=0 \
  -el "C:/Program Files/STMicroelectronics/STM32Cube/STM32CubeProgrammer/bin/ExternalLoader/MX66UW1G45G_STM32H7S78-DK.stldr" \
  -w build/appli.bin 0x70000000 -v

# 燒完自動 reset（不用按實體按鍵）
"$P" -c port=SWD mode=0 -hardRst
```

⚠ 燒錄後 `-hardRst` 會自動重置，**不需要按 B1/U6 實體按鍵**。

---

## 🩺 除錯方法：分階段 marker

核心卡住時，用 RAM marker 定位跑到哪：

```c
#define MARK(stage, val) (*(volatile uint32_t*)(0x24000100 + (stage)*4) = (val))

int main(void) {
    MARK(0, 0xDEADBEEF);        // 進 main()
    SystemClock_Config();
    MARK(1, 0x11111111);        // 時鐘設定完
    MX_UART4_UART_Init();
    MARK(2, 0x22222222);        // UART 好了
    ...
}
```

讀回來（**注意用 mode=0 + 單一連線**）：
```bash
"$P" -c port=SWD mode=0 -hardRst -r32 0x24000100 4
```

哪個 stage 沒被寫，就是卡在前一步。

### 讀 fault 暫存器

```bash
"$P" -c port=SWD mode=0 -r32 0xE000ED28 4   # CFSR/HFSR/DFSR/AFSR
```
- CFSR bit[13] (0x2000) = BusFault，通常是**存取無效位址**
- HFSR bit[30] = FORCED，表示上升成 HardFault

---

## 📋 開新專案時的檢查清單

1. UART 用 **UART4 PD0/PD1 AF8**，不要抄別板子的 USART2
2. GPIO 位址用 **0x58020000** 系列（AHB4）
3. SWD 一律 **mode=0**，要看執行狀態就在同一連線內做完
4. Boot 燒 0x08000000，Appli 燒 0x70000000（帶 stldr loader）
5. LVGL 明確設 layout 或用絕對定位；取值用 `user_data` 不要用 label getter
6. 早期時鐘是 HSI 16MHz，算 BRR 要用對頻率
