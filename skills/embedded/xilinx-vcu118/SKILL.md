---
name: xilinx-vcu118
description: "Xilinx VCU118 (Virtex UltraScale+ VU9P) + Vivado: 建置、綜合、燒錄、驗證。開發此板前必讀。"
tags: [fpga, xilinx, vcu118, vivado, ultrascale, verilog, vhdl, hls]
related_skills: [hardware-design-tradeoffs, rtl-sim-verification, embedded-ui-verification, stm32h7s78-dk]
---

# Xilinx VCU118 + Vivado

⚠ **這份是骨架，多數欄位還沒實測填入。**
標著「未確認」的地方，第一次做的時候要自己查清楚並用
`skill_manage(action='patch')` 補進來。

---

## 一、這塊板子是什麼

| | |
|---|---|
| 板子 | Xilinx VCU118 Evaluation Kit |
| FPGA | **Virtex UltraScale+ XCVU9P**（`xcvu9p-flga2104-2L-e`）|
| 邏輯單元 | 約 2.5M |
| DSP | 6,840 |
| 記憶體 | 板載 DDR4（未確認容量與 part number）|
| 介面 | PCIe Gen3 x16、QSFP28 ×4、USB-UART、FMC+ |
| 燒錄 | 板載 Digilent USB-JTAG |

**這是資料中心等級的大晶片**，不是入門板。實務影響：

- **綜合 + implement 一次要 30 分鐘到數小時**，不是幾秒
- 記憶體吃很兇（Vivado 建議 32GB+ RAM；本機 32GB 是**剛好夠**）
- device 支援檔就要幾十 GB

---

## 二、環境（本機現況：2026-08-28）

```
Vivado      ✗ 未安裝
Vitis HLS   ✗ 未安裝
磁碟        C: 剩 364GB（夠，Vivado + VU9P 約需 100-150GB）
RAM         32GB（VU9P implement 會吃滿，注意）
```

安裝時**只勾 UltraScale+ 的 device 支援**，不要全裝 —— 全裝會多好幾百 GB。

裝完把這幾個加進 PATH 並更新這份 skill 的實際路徑：
```
<Vivado>/bin/vivado
<Vivado>/bin/vitis_hls      （若有裝 HLS）
<Vivado>/bin/xsct           （硬體伺服器 / 燒錄用）
```

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

**未確認**：本機的 Tcl 腳本範本要自己寫，第一次做的時候記得存進這份 skill。

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

## 六、踩過的坑

（還沒有 —— 第一次做完務必把踩到的寫進來，
就像 stm32h7s78-dk 那份一樣。那份的價值全在這一節。）

---

## 七、HLS（如果要用）

Vitis HLS 把 C/C++ 合成成 RTL。

- **C simulation** 很快（秒），先用它驗功能
- **C/RTL co-simulation** 慢很多，但能抓到 pragma 用錯
- 綜合報告要看 **II（Initiation Interval）** 和 latency，不是只看「有沒有過」

**未確認**：本機的 HLS 版本與 `vitis_hls` 路徑。
