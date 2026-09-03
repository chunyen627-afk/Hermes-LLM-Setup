# STM32 側整合規格

> 規劃者 2026-09-03 訂。上游參考：`chunyen627-afk/stm32h7-llama2`（純軟體版，
> 把 FPGA 當記憶體用）。**那個 repo 唯讀，絕不修改。**
> 我們的版本會 clone 到 `Hermes-LLM-Setup/projects/stm32_llama2_accel/`。

## 一、關鍵發現：協定完全相容

我們的 `rtl/xspi_slave.v` 指令集跟 ITRI 版**完全對得上**（當初設計就對齊了）：

| 指令 | ITRI 版 | 我們的 | |
|---|---|---|---|
| Linear burst 讀 | `0x20` | `CMD_READ_LB = 0x20` | ✅ |
| Linear burst 寫 | `0xA0` | `CMD_WRITE_LB = 0xa0` | ✅ |
| MR 讀 | `0x40~0x43` | `CMD_REG_READ = 0x40` | ✅ |
| MR 寫 | `0xC0~0xC3` | `CMD_REG_WRITE = 0xc0` | ✅ |
| Global reset | `0xFF` | `CMD_RESET = 0xff` | ✅ |
| HBPIM `0xF0~0xF8` | 有 | 無 | **不需要**（使用者確認） |

**所以協定層、時序、50MHz 設定全部不用改。**

## 二、要改的只有位址

上游把整個空間當扁平記憶體；我們用高位解碼分兩區
（`rtl/xspi_slave.v:41-44`）：

| 用途 | 上游（ITRI） | 我們的 |
|---|---|---|
| 加速器控制暫存器 | 無 | **`0x9000_0000`** ← 新增 |
| 模型權重 | `0x0000_0000` | **`0x9001_0000`** |
| KV cache | `0x0500_0000` | **`0x9601_0000`**（+0x9001_0000）|

上游的常數在 `Appli/Core/Src/main.c:45-46`：
```c
#define FPGA_MODEL_BASE_ADDR      0x00000000UL
#define FPGA_KV_CACHE_BASE_ADDR   0x05000000UL
```

## 三、驅動層封裝得很乾淨，不用動

所有 FPGA 存取都走這四個（`main.c`）：
```c
void FPGA_Read_Buffer (uint32_t addr, void* buffer, uint32_t size);   // 2066
void FPGA_Write_Buffer(uint32_t addr, void* buffer, uint32_t size);   // 2094
void Safe_FPGA_Read_Buffer (uint32_t addr, uint8_t* buf, uint32_t size);  // 798
void Safe_FPGA_Write_Buffer(uint32_t addr, uint8_t* buf, uint32_t size);  // 850
```
**改 base address 常數就能轉向我們的 slave。**

## 四、要新增的：加速器 API

上游是純軟體 matmul，我們要 offload。新增大約 150 行：

1. 寫 activation 到 `0x9000_0000` 的 reg 區
2. 寫控制暫存器觸發計算
3. 輪詢 done 旗標
4. 讀回結果

⚠ **`matmul_top` 的 reg map 還沒定義** —— 那是 FPGA 端完工前要補的，
見 `SPEC_xspi_bridge.md`。STM32 側要等那個定案才能寫。

## 五、⛔ 上游 repo 的鐵律（照抄，不要重踩）

從 `_upstream_readonly/claude-memory/` 讀到的：

1. **重燒前必須等前一趟完全閒置** —— burst 半途被砍會讓 slave 回傳位移的
   垃圾資料，直到 FPGA 斷電才恢復。`Global Reset (0xFF)` 對 ITRI slave 無效。
2. **XSPI 50MHz、`ClockPrescaler = 3`（37.5MHz）不要改** —— 那是兩塊板子的
   共同安全值，改了換板會爆。
3. **APP.BIN 壞 = 板子變磚**，SDWR 也救不了。開機路徑禁用未初始化 extern。
4. 動過板子周邊線材後，**先跑健檢再做實驗** —— 物理擾動會無聲 wedge slave。

## 六、限制

規劃者**沒有實體板子可以燒錄驗證**。STM32 側只能做到「編譯過 + 邏輯正確」，
上板測試要使用者執行。

---

## 七、只加速 BF16（使用者 09-03 決定）

INT8（`matmul_fpga_q8_0`）和 FP32（`matmul_fpga`）**保留純軟體版不動**。
只改 `matmul_bf16`（`main.c:1260`）。

### 收益試算（15M 模型，dim=288、hidden=768、6 層）

| 項目 | 數字 |
|---|---|
| 每 token 要讀的權重 | **11.4 MB** |
| 現況：73.4 MB/s 讀完 | 162.7 ms → **理論上限 6.15 TPS** |
| **實測 BF16** | **1.69 TPS**（VCU118）|
| 加速後只送 activation+結果 | **128 KB** → 1.79 ms（傳輸不再是瓶頸）|
| 每 token MAC 數 | 5.97 M |
| **1 MAC @300MHz** | 19.9 ms → **50.2 TPS** |
| 1 MAC @100MHz | 59.7 ms → 16.7 TPS |

**結論：1.69 → 約 17-50 TPS，即 10-30 倍。**
瓶頸從「搬 11.4MB 權重過 50MHz 的線」變成「FPGA 內部算 6M 次 MAC」——
權重常駐 DDR4 不再過線，這正是整個專案的立論。

⚠ **300MHz 這個數字還沒驗證** —— 它是 MIG 的 `c0_ddr4_ui_clk` 頻率
（27B 解時脈域問題時把整個 AXI 域接上去的），**不是合成後確認能收斂的頻率**。
`constraints/timing.xdc` 目前還寫 100MHz（period 10ns），要改。
**時序能不能在 300MHz 收斂，要跑完合成看 WNS 才知道。**

| 合成結果 | 動作 | 估計 TPS |
|---|---|---|
| 300MHz 收斂 | 直接用 | ~50 |
| 降到 150-200MHz | MIG 的 `addn_ui_clkout1` 可配其他頻率 | 25-33 |
| 只能 100MHz | 加 CDC 回 clk_wiz | ~17 |

**即使最差的 100MHz 也有 10 倍提升**，所以方案不會因時序失敗，只是快多少。

⚠ 上面假設 1 MAC 全速無停頓。實際會被 DDR4 讀權重的頻寬限制
（6M 次 MAC 要讀 11.4MB，DDR4 端要餵得動），實際值會低一些。
**但即使打三折也有 5-15 TPS，遠優於現在的 1.69。**

### 要改的（只有一個函式）

`matmul_bf16(float* xout, float* x, uint32_t w_addr_base, int n, int d)`
**簽章完全不變** → 12 個呼叫點一個都不用動。

函式體換成：
1. 寫 `x`（n 個 float）到加速器的 activation 區
2. 寫 `w_addr_base` / `n` / `d` 到控制暫存器
3. 觸發，輪詢 done
4. 讀回 `xout`（d 個 float）

⚠ **`matmul_top` 的 reg map 還沒定義** —— FPGA 端要先補這個規格，
STM32 側才能寫。這是目前的相依阻塞點。

### ⚠ 保留 fallback

`g_fpga_down` 機制已經在（連續 8 次逾時就判定 FPGA 沒回應）。
加速器版也要走同一套：**加速失敗要能退回純軟體 BF16**，
否則 FPGA 一出問題整台就不能用了。
