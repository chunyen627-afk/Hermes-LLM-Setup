---
name: xilinx-vcu118
description: "Xilinx VCU118 (Virtex UltraScale+ XCVU9P) + Vivado 合成、時序收斂、block design（IP integrator、MIG DDR4、module reference、board automation、wrapper）、implement、產 bitstream、燒錄、上板驗證。要跑 synth_design、看 WNS/TNS、查資源用量（LUT/FF/DSP/BRAM）、寫時脈約束 xdc、處理跨時脈域假違例、或把 RTL 從模擬推進到硬體時，開工前必讀。含實測可用的 synth_check.tcl。"
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

## 七之二、block design → bitstream（2026-09-03 實戰，一晚踩完的坑）

這一節是 matmul_axi 從「RTL 合成過」走到「產出 .bit」的實際流程。
順序不要跳，每一步都有踩過的坑。

### 0. 執行環境：所有 Vivado 指令都經過帶 settings64 的 .bat

```bat
call "C:\Xilinx\Vivado4.2\settings64.bat"
cd /d <專案根>
vivado -mode batch -nojournal -nolog -source %1
```
Vivado **不繼承呼叫端 shell 的 PATH**。沒跑 settings64 時 `launch_simulation`
只會回一句 `Spawn failed: Broken pipe`，真正的訊息（`xvlog 不是內部或外部命令`）
要手動跑 `compile.bat` 才看得到。**包裝層的錯誤沒有資訊量，往下一層找 log。**

### 1. 自家 RTL 用 module reference，不要打包成 IP

```tcl
set_property source_mgmt_mode All [current_project]   ;# 沒這行找不到 RTL
create_bd_cell -type module -reference xspi_slave xspi_slave
```
module reference 一樣會把 AXI 散腳推斷成 interface pin（`m_axi`/`s_axi`），
而且直接讀 `rtl/`。打包成 IP 之後同一個 .v 在專案裡會有**八份副本**
（ip_repo、.gen/ipshared、.ip_user_files…），改 `rtl/` 合成完全不看，
OOC 快取還會一直命中舊 netlist —— 為了那個快取繞了六種方法都沒用。

### 1之二. ⚠ module_ref 也有快取：bd 的 OOC run

bd 預設 OOC-per-IP，**每個 module_ref cell 都有自己的 OOC 合成 run**
（`<bd>_<cell>_N_synth_1/<cell>.dcp`）。改了 `rtl/*.v` 之後 `synth_1` 只合成一個
`_stub.v` 黑盒，邏輯從那顆舊 dcp 連進來 —— 改了等於沒改，而且合成 0 error。
2026-09-04 實測：修完兩條時序路徑、gate 全綠、合成乾淨，routed 報告卻跟修前
**一模一樣**，才發現 OOC dcp 還是前一天的。

查證（每次改 RTL 後必做）：
```bash
ls -la --time-style=+%m-%d\ %H:%M sys_int.runs/*_synth_1/*.dcp   # 每個 cell 的 OOC dcp 要比 rtl/*.v 新
grep -E "=> OSERDESE3|ODDRE1" sys_int.runs/top_bd_xspi_slave_2_synth_1/runme.log  # 原語要在「那個 cell 的」OOC log
```
（`synth_1` 的 log 永遠會有 `_stub.v`、頂層 utilization 不含 OOC cell 內部 —— 那兩個不是證據。）

修法 A（保留 OOC）：`reset_run top_bd_xspi_slave_2_synth_1` + `reset_target/generate_target` + 重合成。
**修法 B（推薦）：整個 bd 改 Global 合成**，快取問題直接消失：
```tcl
set_property SYNTH_CHECKPOINT_MODE None [get_files */top_bd.bd]
reset_target all $bd ; generate_target all $bd ; make_wrapper ... -force ; reset_run synth_1
```
代價是每次合成連 MIG 一起重跑（多幾分鐘），換來「改了就是改了」。

### 1之三. ⚠ OOC 會把模組內的三態轉成邏輯（Synth 8-5799）

module_ref 的 cell 裡有 `assign io = oe ? out : 1'bz`（inout 匯流排）時，OOC 合成
看不到頂層 IOBUF，會報 `CRITICAL WARNING: Converted tricell instance to logic`，
**高阻態被拿掉，FPGA 永遠驅動那條線** —— 上板時主機一寫就撞線，模擬完全看不出來。
2026-09-04 在 xspi_slave 的 OOC log 發現，之前三顆 bitstream 都帶著這個問題。
這也是改 Global 合成的理由：三態要在頂層 inout 才會變成 IOBUF 的 T 腳。
驗證：頂層合成 log 不能有 8-5799，utilization 的 IOBUF 數要等於 inout 位元數。

**「合成過了」不代表「合成的是你改的那份」** —— 看 dcp 日期，不看 log 尾巴的 successfully。

### 1之四. ⚠ module_ref 的參數不會自己帶進來：逐一核對 CONFIG.*

`create_bd_cell -type module -reference` 建出來的 cell **全用 Verilog 預設參數**。
matmul_axi 的 `matmul_top` 有 `EXTERNAL_DATA`（0 = 驗證用 `$readmemh` 路徑、AXI master 閒置；
1 = 真的從 DDR4 串流），bd 沒設 → 三顆 bitstream 時序/DRC 全過，功能是空的。
症狀：合成 log 出現 `[Synth 8-4445] could not open $readmem data file` ——
**那不是可以忽略的警告，是「合成到驗證路徑」的證據。**
```tcl
list_property [get_bd_cells matmul_top] CONFIG.*        ;# 空的 = 全預設
set_property CONFIG.EXTERNAL_DATA 1 [get_bd_cells matmul_top]
```
整合層的驗收要多一條：每個自家模組的「模式開關」參數都被明確設定，並在 synth log 對照。

### 1之五. ⚠ incremental synthesis 會沿用舊 netlist

專案的 synth_1 若有 `AutoIncrementalCheckpoint`（log 第一頁會有
`read_checkpoint -auto_incremental -incremental .../utils_1/imports/synth_1/*.dcp`），改了 RTL 加的暫存器
可能**根本沒進 netlist**（2026-09-04：加了三組暫存器，implement 後 `get_cells` 一顆都沒有，時序反而更差）。
```tcl
set_property AUTO_INCREMENTAL_CHECKPOINT 0 [get_runs synth_1]
set_property INCREMENTAL_CHECKPOINT {} [get_runs synth_1]
```
**改完 RTL 的驗證多一步**：合成後 `open_run synth_1` + `get_cells -hier -filter {NAME =~ *你加的名字*}`，
數量 > 0 才進 implement。「合成成功」三個字在這裡也一樣沒有資訊量。

### 2. board 相關的外部埠用 board automation，不要手接

```tcl
apply_board_connection -board_interface "ddr4_sdram_c1" -ip_intf "mig_ddr4/C0_DDR4" -diagram top_bd
apply_board_connection -board_interface "default_250mhz_clk1" -ip_intf "mig_ddr4/C0_SYS_CLK" -diagram top_bd
```
- 合法的 board interface 名字用 `get_board_part_interfaces` 查，不要猜
  （VCU118 的 DDR4 是 `ddr4_sdram_c1`，MIG 的系統時脈是 `default_250mhz_clk1`，
  `default_sysclk1_300` 會被 MIG 拒絕）
- `get_bd_automation_rules` **這個指令不存在**，`help apply_bd_automation` 才有用法
- 在既有 bd 上刪/換 IP cell 會弄丟 board automation 建好的東西
  （external port 的 BOARD_INTERFACE 關聯），動完要重新 `apply_board_connection`
- reset 按鈕不在 automation 裡，自己從 board file 查腳位：
  `data/xhub/boards/XilinxBoardStore/boards/Xilinx/vcu118/2.0/part0_pins.xml`
  （CPU_RESET = L19, LVCMOS12, **高態有效**；MIG `sys_rst` 和 proc_sys_reset
  `ext_reset_in` 預設也是高態有效，port 叫 `rst_n` 只是名字誤導）
- 沒有 LOC/IOSTANDARD 的 port 在 write_bitstream 會被 DRC UCIO-1 / NSTD-1 擋下

### 2之二. board preset 會把 MIG 的速度鎖死

`C0_DDR4_BOARD_INTERFACE = ddr4_sdram_c1` 時，`C0.DDR4_TimePeriod` 變成
disabled parameter：`set_property` 只回一句 WARNING `[BD 41-721] ... ignored`，
**值不變、指令不報錯**（設完一定要 get_property 回讀確認）。
切 Custom 可以改，但切回 preset 立刻還原；留在 Custom 會失去 MIG 自動產生的
DDR4 腳位 xdc。**想用降 ui_clk 來閃時序問題，在 board preset 下走不通** ——
時序要在 RTL 解（切 pipeline）。

### 3. ⚠ wrapper 是產物：bd 的 port 一改就要重做

```tcl
make_wrapper -files [get_files */top_bd.bd] -top -import -force
set_property top top_bd_wrapper [current_fileset]
```
**最貴的一課。** implement 報
`[Mig 66-99] c0_sys_clk_p/n is/are not connected to top level instance`，
在 bd 上補了四次時脈接線都沒用 —— bd 明明有 `default_250mhz_clk1_clk_p/n`
外部埠，但 **wrapper 是舊的，沒有那兩個 port**，MIG 的時脈當然到不了 top。

診斷法（30 秒）：比對兩邊的 port 清單
```bash
grep -E "^\s*(input|output|inout)" <.gen>/bd/top_bd/hdl/top_bd_wrapper.v
# 對照 Tcl 的 get_bd_ports —— 少的那個就是答案
```
**報「沒接到 top / 找不到 port」時，先比對產物跟來源，不要去改設計。**

### 4. 合成 / implement：啟動就退出，看檔案判斷完成

```tcl
reset_run synth_1
launch_runs synth_1 -jobs 8        ;# 然後直接退出，不要 wait_on_run
# 合成完（synth_1/top_bd_wrapper.dcp 出現）再：
launch_runs impl_1 -to_step write_bitstream -jobs 8
```
- `wait_on_run` 會讓這版 Vivado 崩（EXCEPTION_ACCESS_VIOLATION，兩次）
- 父行程退出後子行程「看起來不見了」但 run 有跑完 ——
  **看 `synth_1/*.dcp`、`impl_1/*.bit`、`runme.log`，不看行程**
- 輪詢用 mtime 比啟動時間新，不然會撿到上一次的舊檔
- 合成 47 秒、implement 分鐘級到小時級，都背景跑

### 4之三. ⚠ 長任務（合成/Implement）執行期間千萬不可結束對話輪次

Hermes CLI 在 assistant 結束該輪（沒有工具在前景運行）時會退出行程，Windows 會強制終止該 session 產生的所有背景子行程（包括 Vivado implement）。
**正確做法**：啟動 implement 後，**必須在同一個對話輪次內**，使用前景命令每 120~240 秒輪詢一次 `sys_int.runs/impl_1/runme.log` 與檔案生成情況（例如 `sleep 180; tail -20 ...`，單次執行時間不超過 600 秒上限），直到 `.bit` 與 `.rpt` 真正生成，完成全晶片時序確認後才結束該輪進行報告！

### 4之二. 合成的 WNS 不算數，只信 `*_timing_summary_routed.rpt`

matmul_axi 實測：合成報 sysclk **+2.3 ns**，implement 繞完 **-0.5 ns**
（16 級邏輯的位址路徑，3.69 ns 塞不進 3.33 ns）。差了快 3 ns。
「300 MHz 收斂」在合成階段說出口，繞線後被推翻。驗收只看
`impl_1/<top>_timing_summary_routed.rpt` 的 **Intra Clock Table**（每個時脈一行 WNS）。
時脈腳不是 GCIO 又用 `CLOCK_DEDICATED_ROUTE FALSE` 時，插入延遲會到 7 ns，
所有對外 I/O 時序都會吃到這 7 ns —— 用 `xspi_clk ? hi : lo` 這種「時脈當 mux 選擇」
的 DDR 輸出寫法會直接變成時脈腳→LUT→輸出腳的組合路徑，要改用 `ODDRE1`。

### 5. 三層驗證，一層比一層嚴

| 層 | 抓得到什麼 | 抓不到什麼 |
|---|---|---|
| iverilog 模擬 | 功能 | 多重驅動（Verilog 允許）|
| 合成 | 時序、資源、latch | **多重驅動也抓不到** |
| implement DRC | `MDRV-1` 多重驅動、IO 沒約束、MIG 時脈沒到 top | — |

同一個 reg 在兩個 always 賦值：模擬過、合成過、**implement 才擋**。
DDR 介面「上升緣寫高 byte、下降緣寫低 byte」要拆成兩個 reg 再用相位選。

### 6. 不要在 bd 專案裡自己約束板子時脈

MIG 的 board preset 已經 `create_clock` 了 sysclk，自己再寫一次會
`Clock 'sysclk' completely overrides clock 'sysclk_p'`。只約束 IP 管不到的
外部時脈（xspi_clk）和跨域 `set_clock_groups -asynchronous`。

### 6之二. 非 GCIO 腳當時脈：placer 會擋，用 impl-only xdc 降級

外部時脈（例如 xspi_clk 的 AK29）不是全域時脈專用腳（GCIO）時，placer 報
`[Place 30-675] rule_gclkio_bufg ... IO Clock Placer failed`。腳位是板子接線
決定的改不了，低頻時脈（50 MHz）可以照它給的範例降級：
```tcl
set_property CLOCK_DEDICATED_ROUTE FALSE [get_nets xspi_clk_IBUF_inst/O]
```
⚠ 這個 net 名字**合成後才存在**，放在一般 xdc 會在合成時報找不到。
放獨立檔並設 `set_property USED_IN_SYNTHESIS false [get_files impl_only.xdc]`。
錯誤訊息裡就有完整的 set_property 範例，照抄，不要自己想 net 名。

### 6之三. 序列 MAC 在 300 MHz 收不了：每元素 K 拍 + multicycle，不要動累加順序

要跟 C 版 bit-exact，FP32 的 mul→add 必須照原順序串行累加，這條 acc→acc 迴圈
在 3.3 ns 內塞不下。做法：資料路徑維持組合邏輯，餵它的暫存器（acc、讀出的 w/x、i/j）
每 K 拍才更新一次（`ph` 計數器），xdc 給 `set_multicycle_path K`。
```tcl
set mac_sinks [get_cells -hier -filter {NAME =~ *u_core/acc_reg* || NAME =~ *u_core/xout_reg*}]
set mac_1cyc  [get_cells -hier -filter {NAME =~ *u_core/ph_reg* || NAME =~ *u_core/state_reg*}]
set_multicycle_path 3 -setup -to $mac_sinks
set_multicycle_path 2 -hold  -to $mac_sinks
set_multicycle_path 1 -setup -from $mac_1cyc -to $mac_sinks   ;# 每拍變的 enable 訊號設回單週期
set_multicycle_path 0 -hold  -from $mac_1cyc -to $mac_sinks
```
⚠ 用「終點」寫法的理由：BRAM 同步讀的輸出暫存器（`w_q <= w_mem[addr]`）合成後**被吸進
RAMB36E2**，`get_cells *w_q_reg*` 是空的（synth log：`Empty from list`）。先用 `open_run synth_1`
+ `get_cells -hier` 看真正的 cell 名再寫約束。BRAM 推斷的條件：寫入在**沒有 reset** 的 always、
讀取是 registered；寫在 async-reset 區塊會得到 `RAM is sensitive to asynchronous reset`。

### 7. xsim 帶 MIG 不要用 RTL 模型

DDR4 校準要跑 25 小時模擬時間。要嘛 `SELECTED_SIM_MODEL tlm`，要嘛跳過模擬
直接合成（連線正確性由 `validate_bd_design` 背書）。

### 8. ⚠ 時脈架構與降頻最佳實踐：嚴禁加 clk_wiz 分接板載時脈，善用 MIG 內建 addn_ui_clkout1 + SmartConnect 雙時脈（2026-09-05 實戰血淚）

當加速器或 RTL 邏輯較深無法在 300 MHz（MIG `ui_clk`）收斂時，需要將計算邏輯降至 100 MHz。

#### ❌ 致命陷阱：加 clk_wiz_0 + axi_clock_converter
- 直覺做法是建一顆 `clk_wiz_0` 產生 100 MHz，並加一顆 `axi_clock_converter` 做跨時脈。
- 但 VCU118 板載 250 MHz 差分引腳（`default_250mhz_clk1`）是專用硬體 Pin，在 UltraScale+ 架構下一組實體差分 Pin **只能接進一顆硬體專用 IBUFDS**（MIG 內部已自帶一顆）。
- 如果讓 `clk_wiz_0` 也接板載差分 port，合成會過（因為合成不做實體 IO 綁定），但一進 Implement 的 `place_design` 就會硬體致命報錯：
  ```text
  ERROR: [Place 30-602] IO port 'default_250mhz_clk1_clk_p' is driving multiple buffers.
  ```
- 如果改由 `clk_wiz` 產生 250 MHz 餵給 MIG，MIG 的硬體 PLL 拒絕接受非專用 GC Pin 來源；若單端反接更會破壞校準。**這條路完全走不通。**

####  黃金標準解法：MIG 內建輔助時脈 + SmartConnect 雙時脈（對照專案 matmul_axi_p 驗證成功）
**完全不需要 `clk_wiz`，也完全不需要手動加 `axi_clock_converter`！**
1. **時脈來源**：MIG DDR4 內部原本就配置有專用 MMCM，並原生提供輔助時脈輸出：`mig_ddr4/addn_ui_clkout1`（預設即為 100 MHz）！
2. **加速器連線**：將 `mig_ddr4/addn_ui_clkout1` 接至 `matmul_top/aclk`、`xspi_slave/aclk`、`rst_aclk/slowest_sync_clk` 以及 `axi_smc/aclk`。
3. **跨時脈轉換**：將 SmartConnect 設為雙時脈（`NUM_CLKS 2`），由 SmartConnect 內部自動實例化高效非同步時脈轉換器：
   - `axi_smc/aclk` 接 100 MHz（`addn_ui_clkout1`）
   - `axi_smc/aclk1` 接 300 MHz（`mig_ddr4/c0_ddr4_ui_clk`）
4. **復位產生**：加一顆 `proc_sys_reset`（命為 `rst_ui`）專門負責 300 MHz 域的 MIG AXI 復位。
5. **板載引腳**：板端 250 MHz 差分 Pin 依然只有 MIG 唯一驅動，**零 Buffer 衝突、零硬體 DRC 違規**。
6. **實測成果**：全面收斂時序（WNS = +0.088 ns，TNS = 0.000 ns），順利產生 Bitstream！

#### 標準 Tcl 範本（直接使用）：
```tcl
# 1. 拔除原本在 ui_clk (300M) 上的 100M 元件
set uinet [get_bd_nets -of_objects [get_bd_pins mig_ddr4/c0_ddr4_ui_clk]]
foreach p {axi_smc/aclk xspi_slave/aclk matmul_top/aclk rst_aclk/slowest_sync_clk} {
  catch {disconnect_bd_net $uinet [get_bd_pins $p]}
}

# 2. SmartConnect 開啟雙時脈
set_property CONFIG.NUM_CLKS 2 [get_bd_cells axi_smc]
connect_bd_net [get_bd_pins mig_ddr4/addn_ui_clkout1] \
               [get_bd_pins axi_smc/aclk] \
               [get_bd_pins xspi_slave/aclk] \
               [get_bd_pins matmul_top/aclk] \
               [get_bd_pins rst_aclk/slowest_sync_clk]
connect_bd_net [get_bd_pins mig_ddr4/c0_ddr4_ui_clk] [get_bd_pins axi_smc/aclk1]

# 3. 建立 300 MHz (ui_clk) 的專用復位模組 rst_ui
set rui [create_bd_cell -type ip -vlnv xilinx.com:ip:proc_sys_reset:5.0 rst_ui]
connect_bd_net [get_bd_pins mig_ddr4/c0_ddr4_ui_clk] [get_bd_pins rst_ui/slowest_sync_clk]
connect_bd_net [get_bd_pins mig_ddr4/c0_ddr4_ui_clk_sync_rst] [get_bd_pins rst_ui/ext_reset_in]
connect_bd_net [get_bd_pins mig_ddr4/c0_init_calib_complete] [get_bd_pins rst_ui/dcm_locked]
set arst [get_bd_nets -quiet -of_objects [get_bd_pins mig_ddr4/c0_ddr4_aresetn]]
if {[llength $arst] > 0} { disconnect_bd_net $arst [get_bd_pins mig_ddr4/c0_ddr4_aresetn] }
connect_bd_net [get_bd_pins rst_ui/peripheral_aresetn] [get_bd_pins mig_ddr4/c0_ddr4_aresetn]

# 4. 驗證、更新 wrapper、重跑合成
validate_bd_design
save_bd_design
reset_target all [get_files */top_bd.bd]
generate_target all [get_files */top_bd.bd]
make_wrapper -files [get_files */top_bd.bd] -top -import -force
set_property top top_bd_wrapper [current_fileset]
```

---


## 八、HLS（如果要用）

Vitis HLS 把 C/C++ 合成成 RTL。

- **C simulation** 很快（秒），先用它驗功能
- **C/RTL co-simulation** 慢很多，但能抓到 pragma 用錯
- 綜合報告要看 **II（Initiation Interval）** 和 latency，不是只看「有沒有過」

**未確認**：本機的 HLS 版本與 `vitis_hls` 路徑。
