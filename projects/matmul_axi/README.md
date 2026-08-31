# matmul_axi — 27B 自己做的 BF16 矩陣乘法加速器

**這不是人寫的**。題目是三行，其餘全部由本地 Qwen3.8-27B 完成 ——
包括架構決定、除錯、驗證方法。放在這裡當「小弟能做到什麼程度」的存證。

## 題目（使用者給的原文）

> 做一個矩陣乘法硬體加速器 IP，IP 介面是 AXI Full 的，
> 配合 llama2.c 跑 tinystories 15M、bf16 的精度。
> 跑模擬，證明它算出來的結果跟 C 版本一致。

後續補的系統約束：最終要由 STM32 透過 8-bit xSPI 驅動，
AXI 匯流排要能跟 VCU118 的 MIG DDR4 介面互通。

## 成果（都是我親自重編重跑驗證過的，不是採信它的報告）

| 模組 | 驗證結果 |
|---|---|
| `f32_mul.v` / `f32_add.v` | bit-exact vs 編譯出來的 C oracle |
| `matmul_core.v` | **40/40 bit-exact** vs `c_matmul_oracle.c` |
| `axi4_slave_reg.v` | 32/64/128/256 四種位寬 **全部 0 FAIL** |
| `axi4_master.v` | data_integrity 5581/0 · 6 個 cover 全中 |
| `matmul_top.v` | **288/288 bit-exact** vs C oracle |
| `matmul_top_cdc.v` | 288/288 bit-exact（跨時脈域版）|
| `xspi_slave.v` | ⚠ **還沒通過** —— 見下方 |

### xspi_slave 的真實狀態（2026-08-31 21:50）

完整 gate 十一個 run 只有這一個 FAIL：

```
check 'data_integrity': 26 of 26 mismatched
the test itself reported SIMEND fail
fatal marker in log: 'timeout'
```

| 面向 | 狀態 |
|---|---|
| RTL + tb 編譯 | ✅ 0 error 0 warning |
| 12 個 require_cover | ✅ **全部 fire** |
| 資料正確性 | ❌ **26/26 全錯**（讀回來是 x）|

**這正是「cover 全綠不等於功能對」的實例。** 如果 tb 只檢查 cover
不檢查資料，這會是一個假的通過。它自己的 tb 有 `data_integrity` 檢查，
抓到了 26 個 mismatch，也誠實回報了。

## 架構圖

`docs/fpga-architecture.html` —— 依 `rtl/` 實際 port 宣告與
`ARCHITECTURE.md` 繪製，標示已有 RTL 與尚未建立的 Xilinx IP。

系統整合的決定：**interconnect 和 width converter 用 Xilinx 內建 IP**，
不自己寫（焦點放在 xSPI bridge 和加速器，那兩個才是自己的 IP）。
Xilinx IP 是加密 SystemVerilog、只能用 xsim，所以**絕不進模組層的
iverilog gate** —— 那會讓每次迭代從零點幾秒變成幾十秒。

## 它自己做的關鍵決定

**精度**（一開始追 Python `Fraction` 精確值，燒掉六小時後自己修正）：
- BF16 只有 8-bit 尾數 → ~2.4 位十進位有效數字
- **BF16×BF16 在 FP32 中是精確的**（8+8=16 bits < 24-bit significand）
  ← 這是它自己推出來的，所以乘法可以位元精確，只有累加有誤差
- 驗證對象改成編譯出來的 C，不再是自己寫的 Python 模型

**架構**（只給「8-bit xSPI」和「要接 MIG」兩個約束，推導是它自己走的）：
- xSPI 8-bit @ 100MHz → ~12.5 MB/s
- 15M BF16 = 30MB → **2.4 秒/token，不可接受**
- 結論：權重放 FPGA 端 DDR4，xSPI 只傳 activation + control
- AXI 位寬選 256-bit（對齊 MIG 的 DDR4 ratio）
- MIG 規格**標注為假設**（本機無 Vivado，誠實標注沒有驗證）

## 它自己抓到的 bug（挑幾個有代表性的）

- `bit_length` 高往低掃且沒有 break → 留下的是**最低**位不是最高位
- `{sign, 9'hFF, 23'd0}` 是 33 bits，指派給 32-bit 輸出時**符號位被靜默吃掉**
- testbench 等 `awready`/`arready`（組合訊號，DUT latch 後就掉）→ 永遠等不到，
  要改等 `bvalid`/`rvalid` 回應
- 「0 checked, 0 mismatches, RESULT: PASS」—— 測試向量檔 0 byte，
  一個案例都沒跑卻回 PASS

這些踩坑都寫回 `skills/embedded/rtl-sim-verification` 和
`skills/embedded/hardware-design-tradeoffs` 了。

## 檔案

- `ARCHITECTURE.md` — 它自己寫的架構推導（頻寬計算、位寬選擇理由）
- `HANDOFF.md` — 它自己寫的交接文件（壓縮/撞上限後靠這份接手）
- `rtl/` — RTL 原始碼

完整專案（含 testbench、C oracle、測試向量）在本機
`C:\Users\pjunm\matmul_axi\`，太大不放進倉庫。
