---
name: xilinx-vcu118
description: "Xilinx VCU118 (Virtex UltraScale+ XCVU9P) + Vivado 合成、時序收斂、燒錄、上板驗證。要跑 synth_design、看 WNS/TNS、查資源用量（LUT/FF/DSP/BRAM）、寫時脈約束 xdc、處理跨時脈域假違例、或把 RTL 從模擬推進到硬體時，開工前必讀。含實測可用的 synth_check.tcl。"
tags: [fpga, xilinx, vcu118, vivado, ultrascale, verilog, vhdl, hls]
related_skills: [hardware-design-tradeoffs, rtl-sim-verification, embedded-ui-verification, stm32h7s78-dk]
---

# Xilinx VCU118 + Vivado

環境已實測可用（2026-08-31）。合成與時序流程見第六節，
腳本在 `references/` 底下可以直接跑。

仍標「未確認」的地方（ILA/VIO、HLS）第一次做時要查清楚並用
`skill_manage(action='patch')` 補進來。

---

## 一、這塊板子是什麼

| | |
|---|---|
| 板子 | Xilinx VCU118 Evaluation Kit |
| FPGA | **Virtex UltraScale+ XCVU9P**（`xcvu9p-flga2104-2L-e`）|
| 邏輯單元 | 約 2.5M |
| DSP | 6,840 |
| 記憶體 | 板載 **dual 80-bit DDR4**（兩組 80-bit 介面；MIG 產生的 AXI 資料寬度未確認 —— 推測 256-bit，要跑一次 MIG IP 產生器才知道）|
| 介面 | PCIe Gen3 x16、QSFP28 ×4、USB-UART、FMC+ |
| 燒錄 | 板載 Digilent USB-JTAG |

**這是資料中心等級的大晶片**，不是入門板。實務影響：

- **綜合 + implement 一次要 30 分鐘到數小時**，不是幾秒
- 記憶體吃很兇（Vivado 建議 32GB+ RAM；本機 32GB 是**剛好夠**）
- device 支援檔就要幾十 GB

---

## 二、環境（本機現況：2026-08-31 實測）

```
Vivado      C:\Xilinx\Vivado\2024.2   ML Enterprise（XCVU9P 可用）
執行檔      C:\Xilinx\Vivado\2024.2\bin\vivado.bat
RAM         32GB（VU9P implement 會吃滿，注意）
```

**實測確認的值 —— 腳本直接抄這些，不要自己猜：**

| | |
|---|---|
| Part | `xcvu9p-flga2104-2L-e` |
| Board part | `xilinx.com:vcu118:part0:2.0` |
| 可用 xcvu9p parts | 70 個（總 parts 1153）|

⚠ **板子是原廠 Rev 2.0**，board file 裝了 2.0 / 2.3 / 2.4 三個版本，
**一定要指定 2.0**，不指定會拿到錯的接腳定義。

### 怎麼確認授權還在（跑之前先驗，省得白等）

```tcl
# 存成 v.tcl，跑 vivado.bat -mode batch -nolog -nojournal -source v.tcl
puts "VU9P: [llength [get_parts -quiet xcvu9p*]]"
puts "BOARD: [get_board_parts -quiet *vcu118*]"
```

兩行都要有輸出。**如果 `VU9P: 0`**，表示變回 Standard 版或授權掉了 ——
Standard 是 device limited，只給 4 顆 Alveo，跑不了 VCU118。
這時不要硬跑，先處理授權（`Help` → `Manage License`，
或設 `XILINXD_LICENSE_FILE`）。

---

## 三、關鍵：分層驗證，不要每次都跑完整流程

這塊板子的**慢迴圈特別慢**，所以分層特別重要：

| 層 | 工具 | 一次多久 | 抓得到什麼 |
|---|---|---|---|
| **1. 行為模擬** | `xsim` / Verilator / iverilog | 秒 | 邏輯錯誤（**大部分 bug 在這裡**）|
| **2. 綜合** | `synth_design` | 分鐘 | 不可合成的寫法、資源爆掉 |
| **3. Implement** | `place & route` | **數十分鐘~小時** | 時序收斂、繞線壅塞 |
| **4. 上板** | JTAG + ILA | 分鐘（但要先跑完 1-3）| 真實時序、外部介面 |

**never 跳過第 1 層。** 一個在模擬層 3 秒就能抓到的 bug，
到 implement 才發現要重跑 40 分鐘。

---

## 四、Vivado 用 Tcl 批次模式，不要開 GUI

agent 用不到 GUI，而且 GUI 會卡住終端機。

```bash
vivado -mode batch -source build.tcl -nojournal -nolog
```

`-nojournal -nolog` 避免工作目錄被 `vivado*.jou` / `vivado*.log` 塞滿。

合成用的腳本已經寫好且實測過：`references/synth_check.tcl`，見第六節。

---

## 五、怎麼證明「它真的在跑」（沒有 GUI 的情況下）

跟 STM32 那邊同樣的思路 —— 要有機器可讀的證據，不能只靠肉眼看板子：

- **綜合/implement 報告**：`report_utilization`、`report_timing_summary`
  → WNS（Worst Negative Slack）≥ 0 才算時序收斂
- **ILA（Integrated Logic Analyzer）**：抓真實訊號波形，可存成 CSV
- **UART 回傳**：讓設計印出計數器/狀態，主機端讀
- **VIO（Virtual IO）**：透過 JTAG 讀寫暫存器，等同 STM32 那邊的 SWD 讀記憶體

**未確認**：本機的 ILA/VIO 存取方式（要用 `xsct` 還是 hw_server），
第一次做的時候查清楚並補進來。

---

## 六、合成 + 時序收斂（主要流程）

**這一節的東西是 agent 自己跑的**，不是規劃者代跑。
規劃者只驗收 `synth_summary.json` 的數字。

腳本在 `references/synth_check.tcl`，用法：

```bash
cd <專案根>
/c/Xilinx/Vivado/2024.2/bin/vivado.bat -mode batch -nolog -nojournal \
  -source <skill>/references/synth_check.tcl -tclargs <頂層模組> <週期ns>
```

它做三件事：讀 RTL → `synth_design` → 出報告，結果寫進 `vivado_out/`：

| 檔案 | 看什麼 |
|---|---|
| `synth_summary.json` | **機器可讀的結論**，先看這個 |
| `utilization.rpt` | LUT/FF/DSP/BRAM 用量 |
| `timing.rpt` | WNS/TNS 明細 |
| `synth.log` | 出事時才翻 |

### 判讀標準（不要只看「有沒有跑完」）

`synth_summary.json` 裡：

| 欄位 | 及格 | 意義 |
|---|---|---|
| `status` | `ok` | 合成本身有沒有成功 |
| `wns_ns` | **≥ 0** | 負的就是**沒收時序**，不能算過 |
| `latch_count` | **0** | 有 latch 幾乎都是 always 區塊漏 else / 漏 default |
| `dsp` / `lut` / `bram` | 量級合理 | 爆掉表示展開太多，要加 pipeline 或改用 BRAM |

⚠ **合成階段的 WNS 是樂觀值**（還沒繞線）。
合成 WNS 就已經是負的 → 這份 RTL 一定收不了時序，先改再說；
合成 WNS 正的 → 只代表「還有機會」，真正的門檻在 implement。

### 兩個時脈域要各自約束

`matmul_axi` 這種設計有 `aclk`（計算/AXI）和 `xspi_clk`（主機介面）兩個
**互不相關**的時脈。約束要分開寫，而且要宣告它們非同步，
否則 Vivado 會去分析跨域路徑，報出一堆假的違例：

```tcl
create_clock -name aclk     -period 10.000 [get_ports aclk]      ;# 100 MHz
create_clock -name xspi_clk -period 20.000 [get_ports xspi_clk]  ;# 50 MHz
set_clock_groups -asynchronous \
  -group [get_clocks aclk] -group [get_clocks xspi_clk]
```

**沒宣告 asynchronous 是最常見的假違例來源** —— 看到跨域路徑違例
先檢查這行有沒有寫，不要急著改 RTL。

**matmul_axi 的定案值（2026-08-31 使用者決定）**：
`aclk` 100 MHz、`xspi_clk` 50 MHz。約束檔已寫好在 `constraints/timing.xdc`，
跑合成時直接指定第四個參數：

```bash
... -tclargs matmul_top 10.0 rtl constraints/timing.xdc
```

⚠ **兩個時脈頻率不同時一定要給真的 xdc** —— 自動產生的那份會把
每個時脈都設成同一個週期，等於在拿錯的目標做時序分析。

### 時序收不了怎麼辦（照這個順序，不要跳）

1. **先確認違例是真的** —— 看 `timing.rpt` 的違例路徑起點終點。
   跨時脈域的？補 `set_clock_groups`。到不存在的接腳？約束寫錯了。
2. **看關鍵路徑經過什麼** —— 通常是一長串組合邏輯。
   `f32_mul` → `f32_add` → 累加器 這種鏈很容易超。
3. **加 pipeline 暫存器** —— 最有效。但**會改變時序行為**，
   加完一定要重跑模擬 gate（見 [[rtl-sim-verification]]），
   不能只看合成過了就算。
4. **放寬目標頻率** —— 100 MHz 收不了就先試 75 MHz。
   能動比較重要，加速比之後再談。
5. 最後才考慮 `-directive Performance_*` 這類選項。

### 派工給 agent 做合成時，驗收標準這樣寫

不要只說「跑合成」——會拿到「跑完了」這種沒有資訊量的回報。
要求它把 `synth_summary.json` 的內容貼回來，並且逐項對照：

```
驗收標準（三項都要，缺一不可）：
1. vivado_out/synth_summary.json 存在且 status == "ok"
2. wns_ns >= 0（負的就是沒收時序，不准說「大致上過了」）
3. latch_count == 0
把整份 JSON 貼回來，不要只說「合成成功」。
```

⚠ **合成過了不等於邏輯還是對的**。加 pipeline 或改寫法之後
一定要重跑模擬 gate（`simcheck.py`），兩邊都綠才算數。
只看合成報告會漏掉功能被改壞 —— 見 [[rtl-sim-verification]]。

### 一次跑多久

VU9P 是資料中心等級大晶片，**合成分鐘級、implement 數十分鐘到數小時**。

- 背景跑，不要前景等（見 [[remote-testing]] 的做法）
- **改一行就重跑完整流程是浪費**。先用 iverilog 模擬 gate 確認邏輯對，
  再進合成；合成過了再進 implement

---

## 七、踩過的坑

### Standard 版看起來像裝好了，其實沒有 VU9P（2026-08-31）

安裝程式裡 `Virtex UltraScale+` 顯示**灰色打勾 + Download Size 0.0 Byte**，
看起來像「已安裝」—— 實際意思是**「這個 edition 能給的 VU+ 元件已經全給了」**，
就是 4 顆 Alveo，沒有 XCVU9P。

Standard 是 *device limited* 版。**VCU118 一定要 Enterprise。**
判斷方法只有一個：`get_parts xcvu9p*` 有沒有回東西（Enterprise 回 70 個）。

### board file 要自己抓，`xhub::install` 會騙你（2026-08-31）

`xhub::install` 回 `INSTALL_OK` 但其實什麼都沒下載，
因為 `xhub::refresh_catalog` 連不到 AMD 伺服器。**別信那個 OK。**

自己抓：
```bash
curl -sL -o b.zip https://github.com/Xilinx/XilinxBoardStore/archive/refs/heads/2024.2.zip
unzip -o b.zip "XilinxBoardStore-2024.2/boards/Xilinx/vcu118/2.0/*" -d .
# 複製到 C:\Xilinx\Vivado\2024.2\data\xhub\boards\XilinxBoardStore\boards\Xilinx\vcu118\2.0\
```
⚠ `master` 分支**沒有** VCU118，board files 是按 Vivado 版本分支的。

### 不要用 `synth_design -rtl` 去抓 port（2026-08-31）

寫 `synth_check.tcl` 時原本想先跑 `synth_design -rtl` elaborate 一次來抓
clock port 名稱，結果：

- RTL 本身有問題時**這一步就會失敗**，還沒進到真正的合成
- 錯誤訊息會被 `.Xil/Vivado-*/realtime\<top>.tcl: no such file` 這種
  暫存檔雜訊蓋掉，看起來像 Vivado 壞了，其實是 RTL 的問題

改成**直接讀頂層原始碼用 regex 抓 `input ... clk`**，不 elaborate。

### 合成很慢，log 停住不代表死了（2026-08-31）

VU9P 光載入 part 就要一段時間，log 會停在
`Loading part xcvu9p-flga2104-2L-e` 好幾分鐘不動。**那是正常的**，
不要以為當掉了就去砍。要等就背景等，用檔案存在與否判斷：

```bash
until [ -f vivado_out/synth_summary.json ]; do sleep 8; done
```

### 半成品 RTL 合不出來，錯誤訊息不會告訴你「因為沒寫完」

實測 `matmul_axi` 的 `xspi_slave.v`（被截斷的半成品）跑合成，
只得到 `Elaboration failed`。真正的原因是 11 個 implicit wire
和一堆宣告了卻沒驅動的 output port。

**合成前先過 iverilog** —— 它的 warning 直接指出哪一條 wire 沒被賦值，
比 Vivado 的 elaboration 錯誤好讀太多。見 [[verify-rtl-drives-not-declares]]。


---

## 八、HLS（如果要用）

Vitis HLS 把 C/C++ 合成成 RTL。

- **C simulation** 很快（秒），先用它驗功能
- **C/RTL co-simulation** 慢很多，但能抓到 pragma 用錯
- 綜合報告要看 **II（Initiation Interval）** 和 latency，不是只看「有沒有過」

**未確認**：本機的 HLS 版本與 `vitis_hls` 路徑。
