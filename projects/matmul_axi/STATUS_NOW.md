# 現況快照（規劃者維護，2026-09-01 12:55）

> 重開機或換 session 接續時**先讀這份**。

## 一句話

`xspi_slave` 編譯乾淨、12 個 cover 全中、AXI 不逾時、資料通路已打通，
但 **`data_integrity` 26/26 全錯** —— 全部讀回 `0000`（期望 `00ff`）。

## 這一夜的軌跡（08-31 20:37 → 09-01 12:53）

| 時間 | 突破 |
|---|---|
| 20:37 | 26 個 mismatch 全 `xxxx`、AXI timeout 24 次 |
| 00:14 | 隱式 wire 修好（`wr_state` Z→2）|
| 00:41 | **AXI timeout 24 → 0** |
| 04:35 | **`xxxx` 12 → 0**（資料通路打通）|
| 09:24 | **換手** —— 上一輪 16 小時沒用波形 |
| 09:40 | 新一輪加上 `$dumpvars`，VCD 產生 |
| 11:00 左右 | 用了兩次 `vision_analyze` 看波形 |
| 12:53 | 資料正確性仍 26/26 |

## 視覺的實測結論（重要）

它真的用了 `vision_analyze`（兩次，有畫波形圖），但**自己判斷不夠用**：

> The vision model is reading the waveform but the window is too wide to
> read exact byte values reliably — **it's guessing.**

> Vision is struggling to read exact hex off a dense waveform. Let me instead
> print the exact signal values at each SCK edge — **ground-truth without
> relying on vision.**

**這個判斷是對的**：視覺看得出訊號什麼時候翻、握手有沒有對上，
但**讀不準密集波形上的 hex 值**。

不過波形沒白開 —— 它現在**直接從 VCD 查任意時刻的訊號值**，
比昨晚「在 tb 加 `$display` 再重跑」快得多。

## ⚠ 規劃者的評估（12:53）

資料正確性 **16 小時沒動**。期間每個階段都真的解掉東西（不是空轉），
但最後這關卡很久。

判準對照：
- 同一問題超過三輪 → 早就觸發
- 修好又改回去 → 發生過一次（01:11→01:26，已建 CHANGELOG 防堵）
- 診斷能力 → **它的診斷都對，但解不掉**

**使用者 2026-09-01 12:53 決定：明天再說，今晚繼續讓它跑。**
如果明天還是 26/26，要重新評估（可能由規劃者下場，估約 60K tokens、兩三輪）。

## ⚠ 還沒做但該做的事

**tb 沒有 `$dumpfile` / `$dumpvars`，所以產不出 VCD。**
時序對齊問題正是波形最擅長的，但現在沒波形可看。
`tb/tb_xspi_slave.v` 的第一個 initial block 上方有詳細註解說明怎麼加。

27B 有視覺能力（mmproj 已掛），畫成波形圖用 `vision_analyze` 問具體問題
會比一輪一輪加 `$display` 快得多。

## 已經解掉的（不要重做）

- `ctl_rd_en` 沒有驅動 → 已補上 assign
- `data` 用 packed 大向量造成一堆 part-select 錯 → 改回 unpacked array
- `wr_beat[(cnt*8+15):(cnt*8)]` 不合法 → 改成 shift-OR
- `32'h9001_0200[31:24]` 對字面常數取 bit-select → 拆成四個 8-bit
- tb 的 `xspi_io` 不是 valid l-value → 加 `xspi_io_master` reg + assign

## 驗證指令

```bash
# 編譯 + 模擬
/c/iverilog/bin/iverilog -o /tmp/tb.out -g2012 -s tb_xspi_slave tb/tb_xspi_slave.v rtl/*.v
/c/iverilog/bin/vvp /tmp/tb.out | grep -E "CHECK|SIMEND"

# 單一 block 的 gate
python C:/Users/pjunm/AppData/Local/hermes/skills/embedded/rtl-sim-verification/references/scripts/simcheck.py --config simcheck.json --block xspi_slave
```

## 系統整合的決定（2026-08-31）

- interconnect、width converter、MIG 全部用 **Xilinx 內建 IP**，不自己寫
- 那些是加密 SystemVerilog、只能用 xsim，**絕不進模組層的 iverilog gate**
- 時脈：`aclk` 100 MHz、`xspi_clk` 50 MHz，約束在 `constraints/timing.xdc`
- 架構圖：`docs/` 沒有，在 Hermes-LLM-Setup 倉庫的
  `projects/matmul_axi/docs/fpga-architecture.html`
