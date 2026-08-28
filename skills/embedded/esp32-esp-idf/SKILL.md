---
name: esp32-esp-idf
description: "ESP32 系列 + ESP-IDF：建置、燒錄、序列埠監看、連線失敗排查。Windows 環境。"
tags: [esp32, esp-idf, embedded, firmware, esptool, serial]
related_skills: [embedded-ui-verification, stm32h7s78-dk]
---

# ESP32 + ESP-IDF（Windows）

⚠ **本機尚未安裝 ESP-IDF**（2026-08-28 確認）。
標「未實測」的部分第一次做完要用 `skill_manage(action='patch')` 補正。

---

## 一、環境

| | |
|---|---|
| 現行穩定版 | **ESP-IDF v6.1**（2026-08 查證）|
| 安裝方式 | v6.0+ 推薦用 **EIM**（ESP-IDF Installation Manager），有 GUI 和 CLI |
| 本機狀態 | ✗ 未安裝，`idf.py` 不在 PATH |

**Windows 上每開一個新終端機都要先 source 環境**，否則 `idf.py` 找不到：

```bash
# 安裝目錄下的 export 腳本（路徑未實測，裝完補上）
%IDF_PATH%\export.bat        # cmd
. $IDF_PATH/export.sh        # git-bash
```

**「PATH 沒設定」是最常見的錯誤** —— 執行 `idf.py` 前一定要先 source。

---

## 二、基本流程

```bash
idf.py create-project <name>      # 建專案
idf.py set-target esp32           # 指定晶片（esp32 / esp32s3 / esp32c3 / esp32c6...）
idf.py menuconfig                 # 設定（會開 TUI，agent 要小心會卡住終端機）
idf.py build                      # 建置
idf.py -p COM3 flash              # 燒錄（Windows 用 COMx，不是 /dev/ttyUSB0）
idf.py -p COM3 monitor            # 看序列埠輸出（Ctrl+] 離開）
idf.py -p COM3 flash monitor      # 燒完直接看，最常用
```

**`flash` 會自動先 build**，不用分兩步。

環境變數可省略 `-p`：`ESPPORT=COM3`、`ESPBAUD=921600`
（命令列給的參數會覆寫環境變數）

---

## 三、連線失敗（`Failed to connect`）排查順序

這是最常見的問題。**照順序查，不要跳**：

1. **序列埠被佔用** —— 最常見。另一個視窗還開著 monitor 就會鎖住埠。
   先關掉所有 serial terminal。
2. **選錯埠** —— Windows 用裝置管理員確認 COM 號碼
3. **供電不足** —— ESP 需要 **70mA 持續、200-300mA 峰值**。
   FTDI 小板和 Arduino 的 3.3V 輸出**帶不動**，會在燒錄中途隨機失敗
4. **boot 腳位（GPIO0）** —— 沒有自動下載電路的板子要手動按住 BOOT 再 reset
5. **降速測試** —— `esptool -b 9600`，能通就是訊號品質問題

### 錯誤訊息對照

| 錯誤 | 原因 | 處理 |
|---|---|---|
| `No serial data received` | RX/TX 沒接或硬體壞 | 檢查接線與 reset 電路 |
| `Wrong boot mode detected (0xXX)` | 沒進下載模式 | 檢查自動 reset 電路，或手動按 BOOT |
| `Invalid head of packet (0xXX)` | 訊號雜訊、麵包板接觸不良、掉電 | 換好一點的 USB 線、拔離麵包板、加大電源 |
| `A serial exception error occurred` | pySerial 層失敗 | 通常是上面幾項的連鎖反應 |

**GPIO6-11 是 flash 專用**，接東西上去會燒不進去
（QIO 模式連 7-10 都不能碰，DIO 模式是 7-8）。

---

## 四、agent 要注意的事

- **`idf.py monitor` 會佔住終端機**（要 Ctrl+] 才離開）。
  批次執行時改用 `--print_filter` 或直接讀序列埠，不要開互動式 monitor
- **`menuconfig` 是 TUI，會卡死自動化流程**。
  設定改用 `sdkconfig.defaults` 檔或 `idf.py -D CONFIG_XXX=y`
- 建置產物在 `build/`，`.bin` 和 `.elf` 都在那

---

## 五、怎麼證明「它真的在跑」

跟其他嵌入式平台同樣的原則 —— 要機器可讀的證據：

- **序列埠輸出**：`ESP_LOGI()` 印計數器/狀態，主機端讀（最直接）
- **`idf.py monitor` 有 backtrace 解碼**：當機時會自動把位址翻成函式名
- **JTAG + OpenOCD**：可以讀記憶體變數（類似 STM32 的 SWD），未實測
- 有螢幕的話：dump framebuffer → PNG → 自己看（見 [[embedded-ui-verification]]）

---

## 六、踩過的坑

（還沒有。第一次做完務必寫進來 —— 這節才是這份 skill 的價值所在。）

---

來源：
- https://docs.espressif.com/projects/esp-idf/en/stable/esp32/get-started/
- https://docs.espressif.com/projects/esptool/en/latest/esp32/troubleshooting.html
（2026-08-28 查證，版本會變，實測不符以實測為準）
