# 給你的提醒

> 10:50 重寫。先前那些零碎的 xSPI 說明**全部作廢**，
> 改看 `SPEC_xspi_bridge.md`（同目錄）。

## ⛔ 先讀 `SPEC_xspi_bridge.md`

xSPI 這一輪的完整規格在那份，包含：
硬性需求、兩側介面、位址映射、兩種長度模型怎麼接、
跨時脈域、12 個驗收 cover、做完的自我檢查。

**先前 NOTE 裡東一塊西一塊的 xSPI 說明有矛盾，以規格書為準。**

## 這一輪最容易做錯的一件事

你現在寫的是「halfword memory model」—— 一顆有自己記憶體的 PSRAM。
**那個放進這個專案沒有用。**

STM32 要存取的不是「你晶片裡的記憶體」，是**這個加速器的東西**：
權重要落在 DDR4（`matmul_core` 才讀得到）、
參數和 start 要落在 `matmul_top.s_axi_*`。

你自己存一份的話，STM32 寫進去的權重 core 讀不到 —— 那是兩個獨立的東西。

規格書第 10 節有三題自我檢查，寫完對一遍。

## 你做對的部分留著

APS256XX 的線路契約（opcode、DDR x8、位址 2 個 SCK cycle、dummy cycles）、
async_fifo 跨時脈域 —— 那些對，繼續用。
**要換的只有「資料存哪裡」：從自己的 mem 陣列，改成發 AXI 交易出去。**

而且你誠實標注了「拿不到 datasheet，從驅動設定推導」，那樣做是對的。
`rtl/axi4_master.v` 已經驗過了，先看能不能直接用，不要重寫。

---

## 時脈頻率定案（16:00 使用者決定）

| 時脈 | 頻率 | 週期 |
|---|---|---|
| `xspi_clk` | **50 MHz** | 20.0 ns |
| `aclk`（計算/AXI） | **100 MHz** | 10.0 ns |

約束檔**已經寫好**在 `constraints/timing.xdc`，不用自己生。

跑合成時第四個參數要指定它：

```
vivado.bat -mode batch -nolog -nojournal \
  -source C:/Users/pjunm/AppData/Local/hermes/skills/embedded/xilinx-vcu118/references/synth_check.tcl \
  -tclargs matmul_top 10.0 rtl constraints/timing.xdc
```

⚠ **一定要給那個 xdc**。不給的話腳本會自動生一份，
但自動那份會把兩個時脈設成同一個週期 —— 等於拿錯的目標做時序分析。

合成的完整流程、判讀標準（`status`/`wns_ns`/`latch_count`）、
時序收不了怎麼調，都在 skill `embedded/xilinx-vcu118` 第六節。
**Vivado 環境已經實測可用**（Enterprise、XCVU9P 70 parts、board 2.0）。

### 但先把 xspi_slave 做完

現在 `matmul_top` 合不出來（`xspi_slave.v` 的 AXI 層還沒寫、
11 個 implicit wire）。合成是**做完這塊之後**的事，不是現在。

---

## 16:27 —— 關於「clean rewrite the controller」

你自己抓到的三個 bug 是真的，我獨立驗過：

`reg_rd_len` 在行 510、533、581 被 **assign 三次**（multiple driver，
合成必定失敗），而且三個全是 placeholder（都指派 0）——
讀取長度等於根本沒實作。這是連續分段追加時疊出來的：
每次補一段就加一個 placeholder，忘了移除前一個。

**重寫可以，但不要一次吐完。** 13:02 那次就是想一次寫完 19KB，
撞單次輸出上限被截斷，然後卡死兩小時沒人發現。

做法：
1. 先只刪掉重複的 placeholder（510、533、581 留一個），存檔
2. 再補讀取引擎的真正邏輯，存檔
3. 再補寫入引擎，存檔
4. 每步之後跑一次 `iverilog -o /dev/null rtl/*.v` 確認能編譯

**每一步都是可以獨立驗證的單元。** 撞上限會自動接續，
但截斷卡死不會 —— 差別在這裡。

---

## 18:02 —— 你在 686/715 行繞了三輪

`wr_beat[(wr_hwpb*8+15) : (wr_hwpb*8)] <= w_fifo_hw;`

這行從 17:45 到現在改了三次，每次只換變數名（wr_byte_cnt → wr_hwpb），
**錯誤訊息一字沒變**。編譯器講的是同一件事：
Verilog 的 `[msb:lsb]` 兩個邊界都必須是常數。

**你在這個專案別的地方已經用對過同樣的東西**，自己去看：

  grep -n "+:" rtl/axi4_slave_reg.v rtl/matmul_top.v rtl/matmul_core.v

`matmul_top.v:296` 那行跟你現在要做的是同一類：基底是變數、寬度是常數。

改完編譯確認，再繼續往下做。
