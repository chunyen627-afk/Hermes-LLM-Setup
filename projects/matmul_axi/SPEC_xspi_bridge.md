# xSPI Bridge 規格書

> 2026-08-31 10:45。這份取代先前 NOTE 裡零碎的 xSPI 說明。
> **動手前先讀完這份**，有矛盾的地方以這份為準。

---

## 0. 一句話

**做一個橋接器：STM32 那側看起來像板子上那顆 PSRAM，
FPGA 這側是 AXI master，把每一筆存取轉發到 DDR4 或 matmul_top 的暫存器。**

它**自己不存資料**。

---

## 1. 為什麼要這個東西

`ARCHITECTURE.md` §1 的架構圖上有一個方塊是空的：

```
STM32H7S78-DK                          VCU118 FPGA
┌──────────────┐  8-bit xSPI  ┌────────────────────────────────────┐
│ 跑 llama2.c  │ ───────────► │  xSPI→AXI bridge  ← 這個           │
│ 灌權重       │              │        │                           │
│ 送 activation│ ◄─────────── │        ▼  AXI fabric               │
│ 讀結果       │              │  ┌───────────┐    ┌──────────────┐ │
└──────────────┘              │  │ matmul_top│◄──►│ MIG DDR4     │ │
                              │  │ (s_axi_*) │    │ (30MB 權重)  │ │
                              │  └───────────┘    └──────────────┘ │
                              └────────────────────────────────────┘
```

沒有它，activation 進不來、結果出不去，matmul IP 再完整也是孤島。

---

## 2. 硬性需求（做不到 = 沒做完）

| # | 需求 | 為什麼 |
|---|---|---|
| R1 | **STM32 韌體一行不改** | 用它現在存取 PSRAM 的那份 OCTOSPI memory-mapped 設定，把 CS 指到 FPGA 就能用 |
| R2 | **不得有自己的記憶體** | 資料要真的落在 DDR4 / matmul_top，否則 core 讀不到 STM32 寫的東西 |
| R3 | **DDR4 和暫存器都要通** | 灌權重走 DDR4，設參數/按 start/讀 STATUS 走暫存器 |
| R4 | **支援主機初始化流程** | OCTOSPI 開機會讀 ID、設 mode register。不回應 → 主機掛住 → 後面全部免談 |

R4 最容易漏：大家都記得做資料傳輸，忘記主機根本走不到那一步。

---

## 3. 介面：STM32 那側（xSPI slave）

模仿 **AP Memory APS256XX**（板子上那顆；型號若查證有誤，
以實際 BSP 為準並在此更正）。

### 3.1 訊號

| 訊號 | 方向 | 說明 |
|---|---|---|
| `xspi_sck` | in | 主機時脈，這一側的 clock domain |
| `xspi_cs_n` | in | 低有效；**拉高 = 這次交易結束** |
| `xspi_dq[7:0]` | inout | x8 雙向資料 |
| `xspi_dqs` | inout | data strobe（DDR 模式） |

### 3.2 訊框格式

```
[ instruction 8 bits, SDR ]
[ address     32 bits, DDR x8 → 2 個 SCK cycle（16 bits/cycle）]
[ dummy       N cycles（LatencyCode - 1）]
[ data        DDR x8，16 bits per SCK cycle，位址自動遞增 ]
```

**長度不預先宣告 —— CS 拉高才結束。** 這跟 AXI 的「先宣告 len」相反，
第 5 節講怎麼接。

### 3.3 opcode

| opcode | 動作 |
|---|---|
| `0x00` / `0x20` | 讀（wrap / linear） |
| `0x80` / `0xA0` | 寫（wrap / linear） |
| `0x40` / `0xC0` | mode register 讀 / 寫 |
| `0xFF` | reset |

⚠ **本機拿不到 datasheet（網路被擋）**，上面是從板子 BSP 的驅動設定推導的。
`ARCHITECTURE.md` 要標 `[ASSUMPTION]` 並寫明推導依據。

---

## 4. 介面：FPGA 那側（AXI master）

輸出標準 AXI4 master（AW/W/B/AR/R 五通道）。

**優先直接用 `rtl/axi4_master.v`** —— 已驗過（burst 拆分、4KB 邊界、
outstanding 管理、反壓、變異測試）。不能用的話理由寫進 HANDOFF。

---

## 5. 位址映射（核心設計）

STM32 那側看到一整片連續位址（memory-mapped，從 `0x9000_0000` 起）。
Bridge 用**位址高位解碼**決定送到哪個 AXI slave：

| STM32 看到的位址 | 轉發到 | 用途 |
|---|---|---|
| `0x9000_0000` ~ `0x9000_0FFF` | `matmul_top.s_axi_*` | 暫存器（4KB） |
| `0x9001_0000` 以上 | MIG DDR4 | 權重 / activation / 結果 |

> 切法可以改，但要寫進 `ARCHITECTURE.md` 並說明理由。
> 暫存器區留 4KB 是為了對齊 AXI 的 4KB 邊界規則。

### 5.1 暫存器（沿用現有 register map，`ARCHITECTURE.md` §5）

| offset | 名稱 | R/W |
|---|---|---|
| `0x00` | CTRL（bit0 start / bit1 reset / bit2 bf16_in） | RW |
| `0x04` | STATUS（bit0 done / bit1 busy / bit2 error） | RO |
| `0x08` | W_BASE | RW |
| `0x0C` | X_BASE | RW |
| `0x10` | OUT_BASE | RW |
| `0x14` | M_DIM | RO |
| `0x18` | N_DIM | RO |
| `0x1C` | COUNT | RO |

### 5.2 兩種存取的性質完全不同

| | 暫存器 | DDR4 |
|---|---|---|
| 大小 | 4 byte 單筆 | 30MB 連續 |
| 頻率 | 零星（輪詢 STATUS） | 大量突發 |

**同一條路徑要同時支援。** 特別想清楚：
**正在灌 30MB 權重時，STM32 想讀 STATUS 會被卡多久？**
如果完全讀不到，那個設計在真實系統裡很難用。

---

## 6. 兩種長度模型怎麼接（最難的部分）

| | xSPI 那側 | AXI 那側 |
|---|---|---|
| 長度 | **不預先知道**，CS 拉高才結束 | **先宣告** `awlen`/`arlen` |

這是本 block 的核心設計問題。可能的做法（自己判斷，不要照抄）：

- **寫入**：先收進 FIFO，湊滿一個 burst 就發一次 AXI 寫；
  CS 拉高時把剩下的用較短的 burst 收尾
- **讀取**：預取（主機可能還要更多），CS 拉高時丟掉沒用到的；
  預取多少是 latency 與浪費頻寬的取捨
- **暫存器存取**：單筆，不需要 burst

⚠ 預取要小心：**讀取有副作用的位址不能預取**。
目前的暫存器都是純讀取，但要在文件裡寫明這個前提。

---

## 7. 跨時脈域

`xspi_sck`（主機給的）和 `aclk`（FPGA fabric）**完全無關**。

用 `rtl/async_fifo.v`（gray pointer + 兩級同步，已驗過）。

⚠ 記取 `matmul_top_cdc` 的教訓：**FIFO 深度大於測試資料量的話，
寫指標永遠不會繞回，gray code 那條路徑等於沒測。**
測試要讓 `fifo_wr_wrap > 0`。

---

## 8. 驗收標準

`simcheck.json` 的 `xspi_slave` block，12 個 `require_cover`：

| Cover | 證明什麼 |
|---|---|
| `host_init_sequence` | 主機開機流程被完整回應（R4） |
| `memory_mapped_write` / `_read` | `*(uint32_t*)0x9000xxxx` 能用（R1） |
| `access_slave_reg` | 設參數、按 start、讀 STATUS（R3） |
| `access_ddr4` | 灌權重、寫 activation、讀結果（R3） |
| `address_decode` | 位址正確分流（R3） |
| `interleaved_reg_and_ddr` | 灌權重時還讀得到 STATUS |
| `burst_address_increment` | 連續位址遞增，含跨 wrap 邊界 |
| `cs_deassert_mid_transfer` | CS 中途拉高能乾淨中止 |
| `dummy_cycle_timing` | dummy 期間行為正確 |
| `clock_ratio_extremes` | SCK/aclk 比例在兩端都測 |
| `irregular_host_timing` | 主機時序不規律（中途停 SCK、間隔忽長忽短） |

**規則：只能加不能減。** 真的不適用，理由和日期寫進 HANDOFF
的「已確認行不通的做法」再換掉。

### 8.1 對手模型（testbench 裡的假 master）

**本機沒有 STM32，這個模型是唯一的驗證機會。**

它要照 **OCTOSPI 的實際行為**寫，不是照「主機應該會怎麼做」的想像：
會因為 CPU 忙 / DMA 插隊而中途停 SCK、突然拉高 CS、
連續兩次存取間隔差很多。

怎麼判斷這個模型夠不夠完整，見
`skills/embedded/rtl-sim-verification/references/protocol-interface-verification.md`
的「怎麼知道對手模型夠完整了」。

### 8.2 誠實標注

做完之後 `ARCHITECTURE.md` 要寫：

```
[ASSUMPTION] xSPI bridge 只在模擬中驗過。testbench 的 master 是照
<型號> 的 BSP 驅動設定推導的（本機拿不到 datasheet，網路被擋）。
已對照：訊框格式、opcode、dummy cycles、位址遞增、CS 中止。
未驗證：實際時序裕度、訊號完整性、STM32 韌體的真實行為。
```

**「照規格做了而且測了 N 項」跟「這東西能用」是兩句話。**

---

## 9. 順序

1. 介面契約（第 3、4、5 節）確認無誤 → 寫進 `ARCHITECTURE.md`
2. 檢視第 8 節那 12 個 cover 合不合理，不合理就改（只能加）
3. 寫 `rtl/xspi_slave.v`（**不含 memory store**）
4. 寫 `tb/tb_xspi_slave.v`（含照 OCTOSPI 行為寫的假 master）
5. 跑 gate 到 exit 0
6. **自己做變異測試**：注入 bug 確認測試抓得到（先 grep 確認注入成功）

## 10. 自我檢查

寫完問自己：

1. STM32 寫一個位址之後，`matmul_core` 讀得到那筆資料嗎？
2. STM32 按下 `CTRL.start`，matmul 會不會真的開始算？
3. 我的模組裡有沒有 `reg [..] mem [0:..]`？（**不該有**）

三題都答對，才是接上去的橋，不是一顆獨立的 PSRAM。
